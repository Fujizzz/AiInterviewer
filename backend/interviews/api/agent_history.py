"""职责：提供本机只读 Agent 面试历史，不将历史查询当作重新执行或恢复命令。

实现：分页仅读元数据；模型正文只从摘要匹配的已检查响应重建，不公开原始内部上下文。
关联：使用 agent_models，按会话认证用户过滤；无归属旧记录不向注册账号公开。

目录：
- InterviewSummary：显式列出可公开的面试元数据，不在列表加载候选人上下文。
- InterviewSummary.Meta：定义模型与只读字段。
- RequestSummary：公开请求状态和固定错误码，不在列表返回资料或响应正文。
- RequestSummary.Meta：定义请求元数据字段。
- AgentHistoryViewSet：只读历史及请求查询，不开放创建、修改、删除或自动重试。
- AgentHistoryViewSet.get_queryset：按登录用户过滤，使详情与子请求路由共用同一归属边界。
- AgentHistoryViewSet.finalize_response：对历史成功及错误响应设置禁止缓存头。
- AgentHistoryViewSet.retrieve：从已检响应重建公开资料、问题及评价。
  附本人回答、时间、进度和异常元数据；不公开未经批准的 Agent 正文。
- AgentHistoryViewSet.requests：分页返回当前面试的请求元数据。
- AgentHistoryViewSet.request_result：按面试和请求双条件读取已检结果，跨面试返回 404。

关键变量：
- logger：仅记录面试关联 ID、获准请求数与可见问题数，不记录模型正文或用户资料。
"""

import logging

from django.shortcuts import get_object_or_404
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..agent_models import AgentInterview, AgentRequest
from ..agent_safety import approved_response

logger = logging.getLogger(__name__)


class InterviewSummary(serializers.ModelSerializer):
    """显式列出可公开的面试元数据，不在列表加载候选人上下文。"""

    class Meta:
        """定义模型与只读字段；列表不附带简历、回答、内部日志或报告。"""

        model = AgentInterview
        fields = [
            "id",
            "status",
            "job_title",
            "resume_version_id",
            "state_version",
            "created_at",
            "updated_at",
            "closed_at",
        ]
        read_only_fields = fields


class RequestSummary(serializers.ModelSerializer):
    """公开请求状态和固定错误码，不在列表返回资料或响应正文。"""

    class Meta:
        """定义请求元数据字段；响应正文须通过单条请求查询显式读取。"""

        model = AgentRequest
        fields = ["id", "kind", "status", "error_code", "created_at", "finished_at"]
        read_only_fields = fields


class AgentHistoryViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """只读历史及请求查询，不开放创建、修改、删除或自动重试。

    按登录用户过滤；本地匿名模式只读未归属记录。UUID 不是授权凭据。
    queryset 避免列表读取较大的 JSON 列；详情在按 ID 定位后再读取它们。
    """

    queryset = AgentInterview.objects.defer("context", "latest_action", "resume_text_snapshot")
    serializer_class = InterviewSummary
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        """按认证用户过滤并应用可选 status 查询；详情及请求先验证归属，不读取其他用户内容。"""
        queryset = super().get_queryset().filter(owner_id=getattr(self.request.user, "pk", None))
        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def finalize_response(self, request, response, *args, **kwargs):
        """对历史成功及错误响应设置禁止缓存头；其余 DRF 响应处理保持原样。"""
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store, private"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def retrieve(self, request, *args, **kwargs):
        """返回已获准输出和本人已接受回答，区分内部提交与可公开结果。

        输入为 URL 中面试 UUID；只读数据库，不调用模型。评价为空表示没有获准公开的评价。
        旧记录或被拒绝结果不自动批准；不读取原始 context、question.payload 或 answer.evaluation。
        评价从同一回答请求的已检响应提取，最终报告从已检 finished 提取；无记录则为 None。
        processing 返回最近请求的状态与固定错误码；不将运行中请求声称为可恢复面试。
        schema_version=2 附最新已检计划、修订、进度及日志；旧响应缺字段时明确返回 None。
        请求异常列表只含固定元数据；问题/回答时间取本人关系记录，不从内部 JSON 补正文。
        成功查询日志仅含关联 ID 与输出数量，便于诊断缺失批准记录，不记录问答正文。
        """
        interview = self.get_object()
        approved = {}
        visible_questions = {}
        profile = job = state = latest_action = None
        result = {}
        progress = dict.fromkeys(
            ("interview_plan", "plan_history", "topic_progress", "decision_logs")
        )
        for record in interview.requests.filter(status="succeeded").order_by("finished_at", "id"):
            payload = approved_response(record)
            if payload is None:
                continue
            approved[str(record.id)] = payload
            if payload["type"] == "prepared":
                profile = payload["candidate_profile"]
            elif payload["type"] == "question":
                visible_questions[payload["question"]["question_id"]] = payload["question"]
                state = payload["interview_state"]
                latest_action = {"type": "ask_question", "question": payload["question"]}
                progress = {key: payload.get(key) for key in progress}
            elif payload["type"] == "finished":
                result = payload["result"]
                profile, job = result.get("candidate_profile"), result.get("job_profile")
                state = result.get("interview_state")
                latest_action = {"type": "finish", "question": None}
                progress = {key: result.get(key) for key in progress}
        questions = []
        for question in interview.questions.select_related("answer"):
            checked_question = visible_questions.get(str(question.id))
            if checked_question is None:
                continue
            answer = getattr(question, "answer", None)
            checked_answer = approved.get(str(answer.request_id), {}) if answer else {}
            evaluation = checked_answer.get("last_evaluation")
            if checked_answer.get("type") == "finished":
                evaluation = next(
                    (
                        entry.get("evaluation")
                        for entry in checked_answer["result"].get("question_history", [])
                        if entry.get("question_id") == str(question.id)
                    ),
                    None,
                )
            questions.append(
                {
                    "ordinal": question.ordinal,
                    "created_at": question.created_at,
                    "question": checked_question,
                    "answer": None
                    if answer is None
                    else {
                        "id": str(answer.id),
                        "text": answer.text,
                        "created_at": answer.created_at,
                        "evaluation": evaluation,
                        "committed_state_version": answer.committed_state_version,
                        "request_id": str(answer.request_id),
                    },
                }
            )
        logger.info(
            "Agent history read interview=%s schema=2 approved_requests=%d visible_questions=%d",
            interview.id,
            len(approved),
            len(questions),
        )
        return Response(
            {
                **InterviewSummary(interview).data,
                "schema_version": 2,
                **progress,
                "request_issues": RequestSummary(
                    interview.requests.filter(status__in=["failed", "interrupted"]), many=True
                ).data,
                "can_resume": False,
                "security_output_available": bool(approved),
                "candidate_profile": profile,
                "job_profile": job,
                "interview_state": state,
                "latest_action": latest_action,
                "questions": questions,
                "final_report": result.get("final_report"),
                "report_narrative_status": result.get("report_narrative_status"),
                "processing": RequestSummary(
                    interview.requests.order_by("-created_at", "-id").first()
                ).data
                if interview.requests.exists()
                else None,
            }
        )

    @action(detail=True, methods=["get"])
    def requests(self, request, pk=None):
        """分页返回当前面试的请求元数据；不存在的面试返回 404，不返回响应 JSON。"""
        interview = self.get_object()
        page = self.paginate_queryset(interview.requests.defer("response"))
        return self.get_paginated_response(RequestSummary(page, many=True).data)

    @action(detail=True, methods=["get"], url_path=r"requests/(?P<request_id>[0-9a-f-]{36})")
    def request_result(self, request, pk=None, request_id=None):
        """输入面试和请求 ID；返回元数据及已检正文或 None，跨面试 404；不重试或调用模型。"""
        interview = self.get_object()
        record = get_object_or_404(interview.requests, id=request_id)
        payload = approved_response(record)
        return Response(
            {
                **RequestSummary(record).data,
                "response": payload,
                "security_output_available": payload is not None,
            }
        )
