"""职责：Agent 面试的关系存储，与固定题库练习表分离。

实现：关系列承载身份、状态、顺序和唯一约束；JSON 保存版本化的 Agent 契约快照。
关联：models 导入以注册模型；agent_repository 原子提交，agent_records 记录请求生命周期。

目录：
- AgentInterview：保存最新上下文和服务生命周期，不重复存储完整历史。
- AgentInterview.Meta：约束上下文版本与终态时间，索引历史列表排序。
- AgentRequest：持久化已接受命令及结果，不保存原始简历或输入摘要散列。
- AgentRequest.Meta：约束请求状态与完成时间，索引面试内请求历史。
- AgentQuestion：保存不可变问题快照，问题 ID 在全库唯一并绑定一场面试。
- AgentQuestion.Meta：约束同场次题号唯一并按题号排序。
- AgentAnswer：保存已接受回答；评价与提交版本同时为空或同时存在。
- AgentAnswer.Meta：约束评价提交标记，待评价回答不作为已评分证据。
- AgentTurn：保存一次已提交动作与决策日志，按状态版本形成审计序列。
- AgentTurn.Meta：约束同场次提交版本和反馈请求唯一。

关键变量：
（无模块级变量。）

状态说明：
Interview.status 为 preparing/active/completed/interrupted/failed；与 Agent 内部状态分开。
Request.status 为 running/succeeded/failed/interrupted；进程骤停可能留下 running，不能自动重放。
context/state_version 是当前状态唯一来源；Request.response 是发送前存储的不可变响应快照，
用于确认结果，不表示客户端已经收到。JSON 不用于跨候选人检索或代替关系约束。
"""

import uuid

from django.db import models
from django.db.models import Q


class AgentInterview(models.Model):
    """保存最新上下文和服务生命周期，不重复存储完整历史。

    输入由后端及 Agent 仓库提供；id 沿用后端生成的 UUID。无用户账户时仅在本机访问边界内使用。
    context 为空表示尚未完成初始化；state_version 与 JSON 内版本由仓库事务同步写入。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=16, default="preparing")
    job_title = models.TextField(blank=True)
    context = models.JSONField(null=True, blank=True)
    state_version = models.PositiveIntegerField(default=0)
    latest_action = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """约束上下文版本与终态时间，索引历史列表排序。"""

        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["-created_at", "-id"], name="agent_history_order")]
        constraints = [
            models.CheckConstraint(
                condition=Q(context__isnull=True, state_version=0)
                | Q(context__isnull=False, state_version__gte=1),
                name="agent_context_version",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["preparing", "active"], closed_at__isnull=True)
                | Q(status__in=["completed", "interrupted", "failed"], closed_at__isnull=False),
                name="agent_lifecycle_time",
            ),
        ]


class AgentRequest(models.Model):
    """持久化已接受命令及结果，不保存原始简历或输入摘要散列。

    id 是客户端 UUID，全库唯一可阻止重连后重复触发调用。重复 ID 不返回其他连接的响应。
    response 保存成功结果；error_code 仅保存后端固定错误码，不保存供应商异常或密钥。
    """

    id = models.UUIDField(primary_key=True, editable=False)
    interview = models.ForeignKey(
        AgentInterview, related_name="requests", on_delete=models.CASCADE, db_index=False
    )
    kind = models.CharField(max_length=16)
    status = models.CharField(max_length=16, default="running")
    response = models.JSONField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """约束请求状态与完成时间，索引面试内请求历史。"""

        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["interview", "created_at", "id"], name="agent_request_history")
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["interview"],
                condition=Q(status="running"),
                name="agent_one_running_request",
            ),
            models.CheckConstraint(
                condition=Q(kind__in=["prepare", "start", "answer"]), name="agent_request_kind"
            ),
            models.CheckConstraint(
                condition=Q(status="running", finished_at__isnull=True, response__isnull=True)
                | Q(status="succeeded", finished_at__isnull=False, response__isnull=False)
                | Q(
                    status__in=["failed", "interrupted"],
                    finished_at__isnull=False,
                    response__isnull=True,
                ),
                name="agent_request_result",
            ),
        ]


class AgentQuestion(models.Model):
    """保存不可变问题快照，问题 ID 在全库唯一并绑定一场面试。

    id 使用 Agent 的字符串标识而非假定其必为 UUID；payload 保留共享 PlannedQuestion 原值。
    ordinal 使用 Agent question_index；本表不更改题目顺序、难度或能力维度。
    """

    id = models.CharField(primary_key=True, max_length=255)
    interview = models.ForeignKey(
        AgentInterview, related_name="questions", on_delete=models.CASCADE, db_index=False
    )
    ordinal = models.PositiveIntegerField()
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """约束同场次题号唯一并按题号排序。"""

        ordering = ["ordinal", "id"]
        constraints = [
            models.UniqueConstraint(fields=["interview", "ordinal"], name="agent_question_order")
        ]


class AgentAnswer(models.Model):
    """保存已接受回答；评价与提交版本同时为空或同时存在。

    question 和 request 均一对一，避免一题多答与一条请求多份回答；跨表会话归属由事务层验证。
    committed_state_version 不为空才表示该回答已进入 Agent 状态；模型失败时保留原回答待诊断。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.OneToOneField(AgentQuestion, related_name="answer", on_delete=models.CASCADE)
    request = models.OneToOneField(AgentRequest, related_name="answer", on_delete=models.CASCADE)
    text = models.TextField()
    evaluation = models.JSONField(null=True, blank=True)
    committed_state_version = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """约束评价提交标记，待评价回答不作为已评分证据。"""

        constraints = [
            models.CheckConstraint(
                condition=Q(evaluation__isnull=True, committed_state_version__isnull=True)
                | Q(
                    evaluation__isnull=False,
                    committed_state_version__isnull=False,
                    committed_state_version__gte=1,
                ),
                name="agent_answer_evidence",
            )
        ]


class AgentTurn(models.Model):
    """保存一次已提交动作与决策日志，按状态版本形成审计序列。

    action、decision_log 均为 Agent 原始契约；feedback_request 为空表示初始化或阶段转换。
    数据库事务将本行、当前上下文、问题和回答评价一起提交，避免出现半轮评分。
    """

    interview = models.ForeignKey(
        AgentInterview, related_name="turns", on_delete=models.CASCADE, db_index=False
    )
    state_version = models.PositiveIntegerField()
    feedback_request = models.OneToOneField(
        AgentRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="committed_turn"
    )
    action = models.JSONField()
    decision_log = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """约束同场次提交版本和反馈请求唯一。"""

        ordering = ["state_version"]
        constraints = [
            models.UniqueConstraint(
                fields=["interview", "state_version"], name="agent_turn_version"
            )
        ]
