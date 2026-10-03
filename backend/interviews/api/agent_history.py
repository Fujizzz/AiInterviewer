"""Responsibilities: provide local read-only Agent interview history, without treating history
queries as re-execution or recovery commands.

Implementation: paginate only metadata; model content reconstructed solely from matched summaries,
never exposing original internal context.
Related Modules: use agent_models, filter by session-authenticated user; unowned old records are not
exposed to registered accounts.

Declaration Index:
- InterviewSummary: Explicitly list publicly available interview metadata, without loading candidate
  context in lists.
- InterviewSummary.Meta: Define model and read-only fields.
- RequestSummary: Public request status and fixed error codes, no materials or response body
  returned in lists.
- RequestSummary.Meta: Define request metadata fields.
- AgentHistoryViewSet: Read-only history and request queries, no create, modify, delete, or
  auto-retry.
- AgentHistoryViewSet.get_queryset: Filter by logged-in user, sharing same ownership boundary with
  detail and sub-request routing.
- AgentHistoryViewSet.finalize_response: Set no-cache headers for both successful and errored
  responses.
- AgentHistoryViewSet.retrieve: Reconstruct public materials, questions, and evaluations from
  approved responses.
  Include personal answers, timestamps, progress, and exception metadata; do not expose unapproved
  Agent content.
- AgentHistoryViewSet.requests: Paginated return of current interview request metadata.
- AgentHistoryViewSet.request_result: Retrieve checked results by both interview and request, return
  404 across interviews.

Variable Index:
- logger: Log only interview-related ID, number of approved requests, and visible questions; do not
  log model content or user data.
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
    """Explicitly list publicly available interview metadata, without loading candidate context in
    lists.
    """

    class Meta:
        """Define model and read-only fields; list does not include resume, answers, internal logs,
        or reports.
        """

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
    """Public request status and fixed error codes, no materials or response body returned in lists.
    """

    class Meta:
        """Define request metadata fields; response body must be explicitly retrieved via
        single-request query.
        """

        model = AgentRequest
        fields = ["id", "kind", "status", "error_code", "created_at", "finished_at"]
        read_only_fields = fields


class AgentHistoryViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only history and request queries, no create, modify, delete, or auto-retry.

    Filter by authenticated user; anonymous mode only reads unowned records. UUID is not an
    authorization credential.
    queryset avoids loading large JSON in list reads; details are fetched only after ID lookup.
    """

    queryset = AgentInterview.objects.defer("context", "latest_action", "resume_text_snapshot")
    serializer_class = InterviewSummary
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        """Filter by authenticated user and optional status query; validate ownership first for
        details and requests, without reading other users' content.
        """
        queryset = super().get_queryset().filter(owner_id=getattr(self.request.user, "pk", None))
        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def finalize_response(self, request, response, *args, **kwargs):
        """Set no-cache headers for successful and errored historical responses; all other DRF
        response handling remains unchanged.
        """
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store, private"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def retrieve(self, request, *args, **kwargs):
        """Return approved outputs and personally accepted answers, distinguishing internal
        submissions from publicly available results.

        Input is interview UUID from URL; read-only database, no model invocation.
        Evaluations are empty if no evaluation was approved for public release.
        Old records or rejected results are not automatically approved; do not read original
        context, question.payload, or answer.evaluation.
        Evaluation extracted from checked response of same answer request; final report extracted
        from checked finished;
        None if no record exists.
        processing returns latest request status and fixed error code; do not claim running requests
        as recoverable interviews.
        schema_version=2 includes latest checked plan, revisions, progress, and logs; return None
        explicitly if old response lacks fields.
        Request exception list contains only fixed metadata; question/answer timestamps taken from
        personal relationship records,
        not filled in from internal JSON.
        Success query logs contain only associated ID and output count, aiding diagnosis of missing
        approval records,
        without logging Q&A content.
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
        """Paginated return of current interview request metadata; return 404 for non-existent
        interview, no response JSON returned.
        """
        interview = self.get_object()
        page = self.paginate_queryset(interview.requests.defer("response"))
        return self.get_paginated_response(RequestSummary(page, many=True).data)

    @action(detail=True, methods=["get"], url_path=r"requests/(?P<request_id>[0-9a-f-]{36})")
    def request_result(self, request, pk=None, request_id=None):
        """Input interview and request ID; return metadata and checked content or None, return 404
        across interviews; no retry or model invocation.
        """
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
