"""Responsibilities: provide login user's resume original/edit version, units and recommendation
slots, private download and explicit parsing.
Implementation: all resources filtered by owner; parsing reuses existing rules + visual pipeline and
explicit queue configuration.
Related Modules: resume_editor validates units and confirms slots, resume_slots extracts original
pending fields;
resume_models stores private files; recommendation.catalog/runtime/rerank provides job sources,
coarse ranking and API fine-ranking;
resume_api/pdf_queue handles original parsing, does not modify experimental parameters.
Configuration Index: ResumeSummary.Meta.model/fields/read_only_fields define public metadata;
ResumeInput's
label/file/text define input; ResumeVersionViewSet.queryset delays reading file content,
serializer_class
specifies read-only serializer, permission_classes enforce login, parser_classes accept
JSON/multipart,
http_method_names do not allow in-place modification, editions create snapshots separately;
MAX_BYTES reuses PDF file limit, not a constant implemented in this module.

Declaration Index:
- ResumeSummary: Public metadata, no original files or list content exposed.
- ResumeSummary.Meta: Define read-only metadata fields.
- ResumeInput: Validate exclusive file/text input for new versions.
- ResumeInput.validate: Validate quantity, size, and PDF identification, no model invocation.
- ResumeVersionViewSet: Provide version API, no content modification allowed.
- ResumeVersionViewSet.get_queryset: Restrict to own records.
- ResumeVersionViewSet.finalize_response: Set no-cache for private responses.
- ResumeVersionViewSet.create: Create immutable input version.
- ResumeVersionViewSet.retrieve: Return personal version content and interview availability.
- ResumeVersionViewSet.current: Switch current version within transaction.
- ResumeVersionViewSet.download: Return personal original PDF.
- ResumeVersionViewSet.destroy: Reject deletion of referenced or parsing versions.
- ResumeVersionViewSet.parse: Explicitly start one parsing run and return stage stream.
- ResumeVersionViewSet.editor: Read units, confirmed slots, and full-field extraction
  suggestions/evidence from original pending fields.
- ResumeVersionViewSet.editions: Save independent edit snapshots and protect originals and
  baselines.
- ResumeVersionViewSet.recommendation_profile: Output candidate fields directly usable by existing
  recommendation API.
- ResumeVersionViewSet.recommendations: Coarse-rank against saved slots from explicit job library,
  then single LLM fine-rank and explain.
- ResumeVersionViewSet.export: Download standalone UTF-8 edited draft/extraction text, does not
  replace original PDF.
- version_events: Wrap existing parsing stream, persist success, failure, or interruption states.
- resolve_resume_version: Resolve available version for WebSocket command parsing.

Variable Index:
- logger: Log version ID, ownership, and status, without recording filename, text, or key.
"""

import json
import logging
from contextlib import aclosing

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from pydantic import ValidationError as ProfileValidationError
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ..recommendation.catalog import CatalogUnavailable, load_catalog
from ..recommendation.rerank import RerankUnavailable, rerank_jobs
from ..recommendation.runtime import ModelUnavailable, rank_pairs
from ..recommendation.schemas import CandidateInput
from ..resume_editor import (
    SLOT_UNITS,
    UNIT_LABELS,
    ResumeEditionInput,
    candidate_payload,
    render_units,
    split_units,
)
from ..resume_models import ResumeVersion
from ..resume_pdf import MAX_BYTES
from ..resume_slots import collect_slots

logger = logging.getLogger(__name__)


class ResumeSummary(serializers.ModelSerializer):
    """Function: metadata serialization; input is version record, output read-only fields;
    constraint: do not load list files or content.
    """

    class Meta:
        """Function: define public fields; logic: read-only version status; constraint: do not
        expose owner or original file bytes.
        """

        model = ResumeVersion
        fields = [
            "id",
            "label",
            "original_name",
            "status",
            "extraction_mode",
            "error_code",
            "is_current",
            "source_version",
            "edited_from",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ResumeInput(serializers.Serializer):
    """Function: create input validation; input is label and single file/text, output normalized
    input; constraint: no implicit parsing.
    """

    label = serializers.CharField(max_length=120, required=False, default="", allow_blank=True)
    file = serializers.FileField(required=False)
    text = serializers.CharField(required=False, max_length=200000)

    def validate(self, attrs):
        """Input parsed fields, output exclusive inputs; bounded PDF read and byte storage; no model
        or database side effects.
        """
        if set(self.initial_data) - {"label", "file", "text"}:
            raise ValidationError("Unknown fields.")
        if hasattr(self.initial_data, "getlist") and len(self.initial_data.getlist("file")) > 1:
            raise ValidationError("Only one file is allowed.")
        if ("file" in attrs) == ("text" in attrs):
            raise ValidationError("Provide exactly one file or text.")
        if "file" in attrs:
            upload = attrs.pop("file")
            if not 0 < upload.size <= MAX_BYTES:
                raise ValidationError("PDF must be nonempty and at most 10 MiB.")
            data = upload.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES or not data.startswith(b"%PDF-"):
                raise ValidationError("Invalid PDF input.")
            attrs.update(original_pdf=data, original_name=upload.name[:255], status="uploaded")
        else:
            attrs["status"] = "ready"
        return attrs


class ResumeVersionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Function: personal version management; input is authenticated request, output JSON/private
    file/stream; constraint: no anonymous versions or auto-retry.
    """

    queryset = ResumeVersion.objects.defer("original_pdf", "text", "units", "recommendation_slots")
    serializer_class = ResumeSummary
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        """Read authenticated identity and return own queryset; return 404 for cross-user ID lookup,
        no side effects.
        """
        return super().get_queryset().filter(owner=self.request.user)

    def finalize_response(self, request, response, *args, **kwargs):
        """Input framework response, output no-cache and MIME sniffing disabled response; no change
        to business state or content.
        """
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store, private"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def create(self, request):
        """Input bounded file or text; create new version and return 201 metadata, no implicit
        current version switch or model invocation.
        """
        serializer = ResumeInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        version = ResumeVersion.objects.create(owner=request.user, **serializer.validated_data)
        logger.info(
            "Resume version created version=%s owner=%s status=%s",
            version.pk,
            request.user.pk,
            version.status,
        )
        return Response(ResumeSummary(version).data, status=201)

    def retrieve(self, request, pk=None):
        """Return content and can_interview by personal version ID; ready only indicates text
        availability, not structural parsing completion.
        """
        version = self.get_object()
        return Response(
            {
                **ResumeSummary(version).data,
                "text": version.text,
                "can_interview": version.status == "ready",
            }
        )

    @action(detail=True, methods=["post"])
    def current(self, request, pk=None):
        """Input version ID; lock user and switch unique current version; return 400 if not ready,
        no content or history binding changes.
        """
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            version = self.get_object()
            if version.status != "ready":
                raise ValidationError("Resume is not ready.")
            ResumeVersion.objects.filter(owner=request.user, is_current=True).update(
                is_current=False
            )
            ResumeVersion.objects.filter(pk=version.pk).update(is_current=True)
        version.refresh_from_db()
        logger.info("Resume current selected version=%s owner=%s", version.pk, request.user.pk)
        return Response(ResumeSummary(version).data)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """Input personal version ID; return private PDF attachment, return 400 for text version; do
        not generate public file URL.
        """
        version = self.get_object()
        if version.original_pdf is None:
            raise ValidationError("This version has no original PDF.")
        response = HttpResponse(bytes(version.original_pdf), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="resume-{version.pk}.pdf"'
        return response

    def destroy(self, request, pk=None):
        """Input personal version ID; within lock, delete unreferenced and non-parsing versions,
        files deleted inline; conflict returns 409.
        """
        with transaction.atomic():
            version = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
            if (
                version.status == "parsing"
                or version.interviews.exists()
                or version.editions.exists()
                or version.derived_versions.exists()
            ):
                return Response({"code": "resume_in_use"}, status=409)
            version.delete()
        logger.info("Resume version deleted version=%s owner=%s", pk, request.user.pk)
        return Response(status=204)

    @action(detail=True, methods=["post"])
    def parse(self, request, pk=None):
        """Input uploaded PDF and optional mode (default traditional); claim parsing and return
        stream, return 409 on repeat, no retry.
        """
        mode = request.data.get("mode", "traditional")
        if set(request.data) - {"mode"} or mode not in ("traditional", "advanced"):
            raise ValidationError("mode must be traditional or advanced.")
        version = self.get_object()
        changed = ResumeVersion.objects.filter(pk=version.pk, status="uploaded").update(
            status="parsing", extraction_mode=mode, updated_at=timezone.now()
        )
        if not changed:
            return Response({"code": "resume_not_uploaded"}, status=409)
        response = StreamingHttpResponse(
            version_events(version.pk, bytes(version.original_pdf), mode=mode),
            content_type="application/x-ndjson",
        )
        response["X-Accel-Buffering"] = "no"
        return response

    @action(detail=True, methods=["get"])
    def editor(self, request, pk=None):
        """Input personal ready version ID, return units, confirmed slots, and full-field extraction
        suggestions from original pending values, no database write or model call.
        Original only based on explicit text extraction for pending values; edited drafts do not
        re-extract, preserve user-cleared fields and historical snapshots.
        """
        version = self.get_object()
        if version.status != "ready":
            raise ValidationError("Resume is not ready.")
        original = (
            get_object_or_404(self.get_queryset(), pk=version.source_version_id)
            if version.source_version_id
            else version
        )
        units = version.units or split_units(version.text)
        suggestions = collect_slots(units) if not version.source_version_id else None
        logger.info(
            "Resume editor loaded version=%s suggestions=%s", version.pk, suggestions is not None
        )
        return Response(
            {
                "id": str(version.pk),
                "label": version.label,
                "source_version": str(version.source_version_id or version.pk),
                "units": units,
                "unit_labels": UNIT_LABELS,
                "slots": candidate_payload(version),
                "slot_units": SLOT_UNITS,
                "slot_suggestions": suggestions,
                "original_text": original.text,
                "original_name": original.original_name,
            }
        )

    @action(detail=True, methods=["post"])
    def editions(self, request, pk=None):
        """Input the user's ready baseline, unit, and confirmation slot; create a new ready snapshot
        within the transaction, without overwriting the original or draft. The source_version is
        fixed to the original root, and edited_from records the baseline; no PDF copying, no
        automatic switching of current, or model invocation occurs.
        """
        serializer = ResumeEditionInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            base = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
            if base.status != "ready":
                raise ValidationError("Resume is not ready.")
            original = (
                get_object_or_404(self.get_queryset(), pk=base.source_version_id)
                if base.source_version_id
                else base
            )
            data = serializer.validated_data
            version = ResumeVersion.objects.create(
                owner=request.user,
                source_version=original,
                edited_from=base,
                label=data["label"],
                status="ready",
                text=render_units(data["units"]),
                units=data["units"],
                recommendation_slots=data["slots"],
            )
        logger.info(
            "Resume edition saved version=%s base=%s owner=%s", version.pk, base.pk, request.user.pk
        )
        return Response(ResumeSummary(version).data, status=201)

    @action(detail=True, methods=["get"], url_path="recommendation-profile")
    def recommendation_profile(self, request, pk=None):
        """Input the user's ready version ID; return candidate and slot association description; no
        sorting, value filling, or model invocation is performed.
        """
        version = self.get_object()
        if version.status != "ready":
            raise ValidationError("Resume is not ready.")
        return Response({"candidate": candidate_payload(version), "slot_units": SLOT_UNITS})

    @action(detail=True, methods=["post"])
    def recommendations(self, request, pk=None):
        """Input the user's ready version ID and an empty request body; return the top 5 positions
        after coarse ranking by model (20 items) followed by LLM fine ranking, along with
        reasoning. Only read saved slots; do not use automatic suggestions or frontend unsaved
        values; unknown remains unknown. Login and CSRF are inherited from the ViewSet; only fine
        ranking invokes the existing model API, with no persistence. Record version ID, count,
        and failure type. API/configuration failures return 503, invalid fine ranking returns
        502, no filling or fallback to coarse ranking; small directories reflect actual counts.
        """
        version = self.get_object()
        if request.data:
            raise ValidationError("Only saved resume features are accepted.")
        if version.status != "ready":
            return Response({"code": "resume_not_ready"}, status=409)
        try:
            candidate = CandidateInput.model_validate(candidate_payload(version))
        except ProfileValidationError:
            logger.error("Resume recommendation invalid saved profile version=%s", version.pk)
            return Response({"code": "recommendation_profile_invalid"}, status=503)
        if all(
            value is None for key, value in candidate.model_dump().items() if key != "candidate_id"
        ):
            return Response({"code": "recommendation_profile_empty"}, status=422)
        try:
            catalog = load_catalog()
            ranked = rank_pairs([(candidate, job.requirements) for job in catalog.jobs], "jobs")
        except CatalogUnavailable as exc:
            return Response({"code": str(exc)}, status=503)
        except ModelUnavailable:
            logger.error("Resume recommendation model unavailable version=%s", version.pk)
            return Response({"code": "recommendation_model_unavailable"}, status=503)
        except ValueError:
            logger.error(
                "Resume recommendation feature computation rejected version=%s", version.pk
            )
            return Response({"code": "recommendation_features_invalid"}, status=422)
        try:
            ranked = rerank_jobs(candidate, catalog, ranked)
        except RerankUnavailable as exc:
            code = str(exc)
            logger.error("Resume recommendation rerank failed version=%s code=%s", version.pk, code)
            return Response(
                {"code": code}, status=502 if code == "recommendation_llm_invalid_output" else 503
            )
        logger.info(
            "Resume recommendation completed version=%s catalog=%s final=%s",
            version.pk,
            len(catalog.jobs),
            len(ranked["results"]),
        )
        return Response(
            {
                **ranked,
                "resume_version_id": str(version.pk),
                "source_name": catalog.source_name,
                "source_kind": catalog.source_kind,
            }
        )

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        """Input the version ID; output UTF-8 text attachment; downloaded content is the snapshot,
        without modifying the original or version.
        """
        version = self.get_object()
        if version.status != "ready":
            raise ValidationError("Resume is not ready.")
        response = HttpResponse(version.text, content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="resume-edition-{version.pk}.txt"'
        return response


async def version_events(version_id, data, *, mode="traditional"):
    """Input version ID, PDF, and mode; parse and forward according to configuration; save text
    before result, error code in error. Canceling or prematurely closing sets parsing status to
    interrupted; exceptions record type and propagate without additional rollback or retry.
    """
    from ..pdf_queue import queued_resume_events
    from ..resume_api import resume_events

    events = (
        queued_resume_events(data, mode=mode)
        if settings.PDF_TASK_EXECUTION == "celery"
        else resume_events(data, mode=mode)
    )
    try:
        async with aclosing(events):
            async for line in events:
                event = json.loads(line)
                if event["type"] in {"result", "error"}:
                    success = event["type"] == "result"
                    await sync_to_async(
                        ResumeVersion.objects.filter(pk=version_id, status="parsing").update
                    )(
                        status="ready" if success else "failed",
                        text=event.get("text", "") if success else "",
                        error_code="" if success else "resume_parse_failed",
                        updated_at=timezone.now(),
                    )
                    logger.info(
                        "Resume parse terminal version=%s status=%s",
                        version_id,
                        "ready" if success else "failed",
                    )
                yield line
    except Exception as exc:
        logger.error("Resume parse failed version=%s exception=%s", version_id, type(exc).__name__)
        raise
    finally:
        await sync_to_async(ResumeVersion.objects.filter(pk=version_id, status="parsing").update)(
            status="interrupted", error_code="resume_parse_interrupted", updated_at=timezone.now()
        )


@sync_to_async
def resolve_resume_version(command, owner_id):
    """Input protocol command and trusted user ID; return the command filled with the user's version
    text, without invoking model or altering input version. If ID is not provided, retain old
    text path; raise ValueError for anonymous, cross-user, or non-ready cases, with fixed error
    returned by protocol.
    """
    version_id = getattr(command, "resume_version_id", None)
    if version_id is None:
        return command
    version = (
        ResumeVersion.objects.filter(pk=version_id, owner_id=owner_id, status="ready").first()
        if owner_id
        else None
    )
    if version is None:
        raise ValueError("Resume version unavailable")
    return command.model_copy(update={"resume_text": version.text, "resume_version_id": None})
