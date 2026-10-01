"""职责：为现有 Agent v1.1 仓库接口提供绑定单场面试的数据库适配器。

实现：使用版本条件 UPDATE 抢占单轮提交，再在同一事务写问题、评价、动作和日志。
关联：AgentSession 注入本适配器；不修改 Agent 策略、评分、问题或逻辑时间参数。

目录：
- DjangoInterviewRepository：绑定面试 ID，禁止跨面试查询或写入。
- DjangoInterviewRepository.__init__：只保存作用域和待提交评价，不打开数据库或模型连接。
- DjangoInterviewRepository._scope：验证传入面试标识与适配器作用域一致。
- DjangoInterviewRepository.get_interview_context：读取并验证最新上下文及关系版本一致性。
- DjangoInterviewRepository.initialize_interview：将准备状态提升为已初始化上下文，版本只增加一次。
- DjangoInterviewRepository.accept_answer：在评价前保存回答，验证请求、当前问题和会话归属。
- DjangoInterviewRepository.commit_turn：原子发布新版本、问题、反馈证据和审计日志。
- DjangoInterviewRepository.get_processed_feedback_action：只在本面试内查询已提交反馈的动作。
- DjangoInterviewRepository.get_question：按问题 ID 和面试双条件读取不可变快照。
- DjangoInterviewRepository.decision_logs_for：按提交版本异步读取本面试决策日志。

关键变量：
- logger：记录已提交版本和冲突类别，不记录上下文或回答正文。

状态说明：
pending_feedback 仅暂存本次评价；对应回答已持久化但仍未评分。
commit_turn 成功后才写入评价及提交版本并清空暂存；失败时事务回滚，不发布半轮结果。
仅实现 Agent 当前使用的 v1.1 路径，不新增旧版兼容或内存回退。
"""

import logging

from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction
from django.utils import timezone

from agents.domain.errors import InvalidAgentState, StateConflictError
from agents.domain.models import AgentDecisionLog, CommitTurnResult, InterviewContext
from shared.contracts import InterviewAction, PlannedQuestion

from .agent_models import AgentAnswer, AgentInterview, AgentQuestion, AgentRequest, AgentTurn

logger = logging.getLogger(__name__)


class DjangoInterviewRepository:
    """绑定面试 ID，禁止跨面试查询或写入。所有 ORM 方法经同步线程执行短事务。"""

    def __init__(self, interview_id):
        """只保存作用域和待提交评价，不打开数据库或模型连接；输入为服务器面试 ID。"""
        self.interview_id = str(interview_id)
        self.pending_feedback = None

    def _scope(self, interview_id):
        """验证传入面试标识与适配器作用域一致；不匹配抛 InvalidAgentState，无 I/O。"""
        if str(interview_id) != self.interview_id:
            raise InvalidAgentState("Repository interview scope mismatch")

    @sync_to_async
    def get_interview_context(self, interview_id):
        """读取并验证最新上下文及关系版本一致性；未初始化或版本损坏时明确失败。"""
        self._scope(interview_id)
        record = AgentInterview.objects.filter(id=self.interview_id).first()
        if record is None or record.context is None:
            raise InvalidAgentState("Interview context is not initialized")
        context = InterviewContext.model_validate(record.context)
        self._scope(context.interview_id)
        if context.state.state_version != record.state_version:
            raise InvalidAgentState("Stored context version does not match relational version")
        return context

    @sync_to_async
    def initialize_interview(self, context):
        """将准备状态提升为已初始化上下文，版本只增加一次。

        输入为共享 InterviewContext；面试壳必须由已接受请求建立。返回深拷贝后的新上下文。
        条件更新防止重复初始化；不依赖 SQLite 不支持的行锁，也不在事务内等待模型。
        """
        self._scope(context.interview_id)
        saved = context.model_copy(deep=True)
        saved.state.state_version += 1
        changed = AgentInterview.objects.filter(
            id=self.interview_id, context__isnull=True, state_version=0, status="preparing"
        ).update(
            context=saved.model_dump(mode="json"),
            state_version=saved.state.state_version,
            status="active",
            updated_at=timezone.now(),
        )
        if changed != 1:
            raise StateConflictError("Interview is missing or already initialized")
        return saved

    @sync_to_async
    def accept_answer(self, request_id, answer):
        """在评价前保存回答，验证请求、当前问题和会话归属。

        输入为已接受请求 UUID 和 CandidateAnswer；返回 None。原始回答持久化但评价保持空。
        一题一答约束阻止重复消费；不复用失败回答或对其自动重试。
        """
        self._scope(answer.interview_id)
        with transaction.atomic():
            record = AgentInterview.objects.get(id=self.interview_id)
            current = (record.latest_action or {}).get("question") or {}
            if record.status != "active" or current.get("question_id") != answer.question_id:
                raise InvalidAgentState("Answer does not target the active question")
            request = AgentRequest.objects.get(
                id=request_id, interview_id=self.interview_id, kind="answer", status="running"
            )
            question = AgentQuestion.objects.get(
                id=answer.question_id, interview_id=self.interview_id
            )
            AgentAnswer.objects.create(
                id=answer.answer_id, request=request, question=question, text=answer.text
            )

    @sync_to_async
    def commit_turn(self, request):
        """原子发布新版本、问题、反馈证据和审计日志。

        输入为 Agent 已验证的 CommitTurnRequest；返回 CommitTurnResult。先进行版本 CAS 写入，
        再读取当前上下文，避免 SQLite 先读后升级写锁；后续失败会撤销 CAS 和全部关联写入。
        反馈必须与本适配器暂存评价及已接收回答匹配。唯一约束冲突转为 StateConflictError，
        数据库忙或不可用保留原异常，不自动重试或替代评分。
        """
        self._scope(request.interview_id)
        version = request.expected_state_version
        if request.new_state.state_version != version:
            raise StateConflictError("State payload version does not match expected version")
        if request.decision_log.state_version != version + 1:
            raise InvalidAgentState("Decision log must match the committed state version")
        try:
            with transaction.atomic():
                changed = AgentInterview.objects.filter(
                    id=self.interview_id,
                    state_version=version,
                    status="active",
                    context__isnull=False,
                ).update(state_version=version + 1, updated_at=timezone.now())
                if changed != 1:
                    raise StateConflictError("Interview version changed or interview is inactive")
                record = AgentInterview.objects.get(id=self.interview_id)
                stored = InterviewContext.model_validate(record.context)
                self._scope(stored.interview_id)
                if stored.state.state_version != version:
                    raise InvalidAgentState("Stored context version is inconsistent")
                saved = (
                    request.new_context.model_copy(deep=True)
                    if request.new_context is not None
                    else stored.model_copy(deep=True)
                )
                saved.state = request.new_state.model_copy(deep=True)
                saved.state.state_version = version + 1
                saved.processed_feedback_ids = list(stored.processed_feedback_ids)
                if request.feedback_request_id is not None:
                    feedback = self.pending_feedback
                    if feedback is None or feedback.request_id != request.feedback_request_id:
                        raise InvalidAgentState("Commit lacks the matching evaluated answer")
                    if feedback.request_id in stored.processed_feedback_ids:
                        raise StateConflictError("Feedback has already been committed")
                    answer = AgentAnswer.objects.select_related("request").get(
                        request_id=feedback.request_id,
                        request__interview_id=self.interview_id,
                        request__status="running",
                        question_id=feedback.question_id,
                        question__interview_id=self.interview_id,
                        committed_state_version__isnull=True,
                    )
                    answer.evaluation = feedback.model_dump(mode="json")
                    answer.committed_state_version = version + 1
                    answer.save(update_fields=["evaluation", "committed_state_version"])
                    saved.processed_feedback_ids.append(feedback.request_id)
                if request.question is not None:
                    question, created = AgentQuestion.objects.get_or_create(
                        id=request.question.question_id,
                        defaults={
                            "interview_id": self.interview_id,
                            "ordinal": saved.state.question_index,
                            "payload": request.question.model_dump(mode="json"),
                        },
                    )
                    if not created and (
                        str(question.interview_id) != self.interview_id
                        or question.payload != request.question.model_dump(mode="json")
                        or question.ordinal != saved.state.question_index
                    ):
                        raise StateConflictError("Question ID already belongs to another snapshot")
                AgentTurn.objects.create(
                    interview_id=self.interview_id,
                    state_version=version + 1,
                    feedback_request_id=request.feedback_request_id,
                    action=request.resulting_action.model_dump(mode="json"),
                    decision_log=request.decision_log.model_dump(mode="json"),
                )
                record.context = saved.model_dump(mode="json")
                record.latest_action = request.resulting_action.model_dump(mode="json")
                record.save(update_fields=["context", "latest_action"])
        except IntegrityError as exc:
            logger.warning(
                "Agent commit constraint conflict interview=%s version=%d",
                self.interview_id,
                version,
            )
            raise StateConflictError("Turn violates a persistence uniqueness constraint") from exc
        if request.feedback_request_id is not None:
            self.pending_feedback = None
        logger.info("Agent turn committed interview=%s version=%d", self.interview_id, version + 1)
        return CommitTurnResult(committed=True, state=saved.state, action=request.resulting_action)

    @sync_to_async
    def get_processed_feedback_action(self, interview_id, feedback_request_id):
        """只在本面试内查询已提交反馈的动作；不存在返回 None，不透露其他面试记录。"""
        self._scope(interview_id)
        row = AgentTurn.objects.filter(
            interview_id=self.interview_id, feedback_request_id=feedback_request_id
        ).first()
        return InterviewAction.model_validate(row.action) if row else None

    @sync_to_async
    def get_question(self, question_id):
        """按问题 ID 和面试双条件读取不可变快照；越界与不存在均抛 InvalidAgentState。"""
        row = AgentQuestion.objects.filter(id=question_id, interview_id=self.interview_id).first()
        if row is None:
            raise InvalidAgentState("Question is unavailable in this interview")
        return PlannedQuestion.model_validate(row.payload)

    @sync_to_async
    def decision_logs_for(self, interview_id):
        """按提交版本异步读取本面试决策日志；返回独立模型列表，不返回惰性 QuerySet。"""
        self._scope(interview_id)
        return [
            AgentDecisionLog.model_validate(row.decision_log)
            for row in AgentTurn.objects.filter(interview_id=self.interview_id)
        ]
