"""职责：提供本机只读 Agent 面试历史，不将历史查询当作重新执行或恢复命令。

实现：分页列表仅读关系元数据；详情按面试外键读取问题、回答与最后成功报告。
关联：使用 agent_models，沿用 LocalOnlyMiddleware；未来共享访问须先增加身份和对象权限。

目录：
- InterviewSummary：显式列出可公开的面试元数据，不在列表加载候选人上下文。
- InterviewSummary.Meta：定义模型与只读字段。
- RequestSummary：公开请求状态和固定错误码，不在列表返回资料或响应正文。
- RequestSummary.Meta：定义请求元数据字段。
- AgentHistoryViewSet：只读历史及请求查询，不开放创建、修改、删除或自动重试。
- AgentHistoryViewSet.finalize_response：对历史成功及错误响应设置禁止缓存头。
- AgentHistoryViewSet.retrieve：返回上下文、已接受回答及提交标记，区分未完成报告。
- AgentHistoryViewSet.requests：分页返回当前面试的请求元数据。
- AgentHistoryViewSet.request_result：按面试和请求双条件读取保存结果，跨面试返回 404。

关键变量：
（无模块级变量。）
"""

from django.shortcuts import get_object_or_404
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..agent_models import AgentInterview, AgentRequest


class InterviewSummary(serializers.ModelSerializer):
    """显式列出可公开的面试元数据，不在列表加载候选人上下文。"""

    class Meta:
        """定义模型与只读字段；列表不附带简历、回答、内部日志或报告。"""

        model = AgentInterview
        fields = [
            "id",
            "status",
            "job_title",
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

    当前无用户身份，访问权限完全沿用本机限制；UUID 不是授权凭据，不开放远程共享。
    queryset 避免列表读取较大的 JSON 列；详情在按 ID 定位后再读取它们。
    """

    queryset = AgentInterview.objects.defer("context", "latest_action")
    serializer_class = InterviewSummary
    http_method_names = ["get", "head", "options"]

    def finalize_response(self, request, response, *args, **kwargs):
        """对历史成功及错误响应设置禁止缓存头；其余 DRF 响应处理保持原样。"""
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store, private"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def retrieve(self, request, *args, **kwargs):
        """返回上下文、已接受回答及提交标记，区分未完成报告。

        输入为 URL 中面试 UUID；只读数据库，不调用模型。回答按题号排序，评价为空表示未提交。
        已结束的 Agent 状态不等于文字报告已经完成；报告只取已保存的 finished 响应。
        """
        interview = self.get_object()
        context = interview.context or {}
        questions = []
        for question in interview.questions.select_related("answer"):
            answer = getattr(question, "answer", None)
            questions.append(
                {
                    "ordinal": question.ordinal,
                    "question": question.payload,
                    "answer": None
                    if answer is None
                    else {
                        "id": str(answer.id),
                        "text": answer.text,
                        "evaluation": answer.evaluation,
                        "committed_state_version": answer.committed_state_version,
                        "request_id": str(answer.request_id),
                    },
                }
            )
        finished = (
            interview.requests.filter(status="succeeded", response__type="finished")
            .order_by("-finished_at")
            .first()
        )
        result = finished.response["result"] if finished else {}
        return Response(
            {
                **InterviewSummary(interview).data,
                "can_resume": False,
                "candidate_profile": context.get("candidate_profile"),
                "job_profile": context.get("job_profile"),
                "interview_state": context.get("state"),
                "latest_action": interview.latest_action,
                "questions": questions,
                "final_report": result.get("final_report"),
                "report_narrative_status": result.get("report_narrative_status"),
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
        """按面试和请求双条件读取保存结果，跨面试返回 404；查询不重试、恢复或调用模型。"""
        interview = self.get_object()
        record = get_object_or_404(interview.requests, id=request_id)
        return Response({**RequestSummary(record).data, "response": record.response})
