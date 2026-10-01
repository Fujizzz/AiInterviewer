"""职责：提供登录用户的简历原件/编辑版本、单元及推荐槽位、私有下载与显式解析。
实现：所有资源按 owner 过滤；解析沿用现有规则+视觉管线及显式队列配置。
关联：resume_editor 校验单元与确认槽位，resume_slots 提取原件待确认字段；
resume_models 存储私有文件；recommendation.catalog/runtime 提供显式岗位来源及原模型排序；
resume_api/pdf_queue 负责原有解析，不修改实验参数。
配置索引：ResumeSummary.Meta.model/fields/read_only_fields 定义公开元数据；ResumeInput 的
label/file/text 定义输入；ResumeVersionViewSet.queryset 延迟读取文件正文，serializer_class
指定只读序列化器，permission_classes 强制登录，parser_classes 接受 JSON/multipart，
http_method_names 不开放原地修改，editions 单独创建快照；
MAX_BYTES 沿用 PDF 文件限额，不是本模块实现的常量。
目录：
- ResumeSummary：公开元数据，不公开原始文件或列表正文。
- ResumeSummary.Meta：定义只读元数据字段。
- ResumeInput：校验新版本的互斥文件/文本输入。
- ResumeInput.validate：校验数量、大小和 PDF 标识，不调用模型。
- ResumeVersionViewSet：提供版本 API，不开放内容修改。
- ResumeVersionViewSet.get_queryset：限定本人记录。
- ResumeVersionViewSet.finalize_response：禁止缓存私有响应。
- ResumeVersionViewSet.create：创建不可变输入版本。
- ResumeVersionViewSet.retrieve：返回本人版本正文与面试可用状态。
- ResumeVersionViewSet.current：事务内切换当前版本。
- ResumeVersionViewSet.download：返回本人原始 PDF。
- ResumeVersionViewSet.destroy：拒绝删除被引用或解析中的版本。
- ResumeVersionViewSet.parse：显式启动一次解析并返回阶段流。
- ResumeVersionViewSet.editor：读取单元、已确认槽位及原件待确认的全字段提取建议/证据。
- ResumeVersionViewSet.editions：保存独立编辑稿快照并保护原件与基线。
- ResumeVersionViewSet.recommendation_profile：输出可直接用于现有推荐 API 的候选人字段。
- ResumeVersionViewSet.recommendations：用本人已保存槽位对显式岗位库调用真实模型并返回卡片。
- ResumeVersionViewSet.export：下载独立的 UTF-8 编辑稿/提取文本，不替换原 PDF。
- version_events：包围既有解析流，持久化成功、失败或中断状态。
- resolve_resume_version：为 WebSocket 命令解析本人可用版本。
关键变量：
- logger：记录版本 ID、归属和状态，不记录文件名、文本或密钥。
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
    """功能：元数据序列化；输入为版本记录，输出只读字段；约束：不加载列表文件或正文。"""

    class Meta:
        """功能：定义公开字段；逻辑：只读版本状态；约束：不暴露 owner 或原文件字节。"""

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
    """功能：创建输入校验；输入为 label 与单个 file/text，输出规范化输入；约束：无隐式解析。"""

    label = serializers.CharField(max_length=120, required=False, default="", allow_blank=True)
    file = serializers.FileField(required=False)
    text = serializers.CharField(required=False, max_length=200000)

    def validate(self, attrs):
        """输入已解析字段，输出互斥输入；有界读取 PDF 并保存字节；无模型或数据库副作用。"""
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
    """功能：本人版本管理；输入为认证请求，输出 JSON/私有文件/流；约束：无匿名版本或自动重试。"""

    queryset = ResumeVersion.objects.defer("original_pdf", "text", "units", "recommendation_slots")
    serializer_class = ResumeSummary
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        """读取认证身份并返回本人查询集；跨用户 ID 由 get_object 返回 404，无副作用。"""
        return super().get_queryset().filter(owner=self.request.user)

    def finalize_response(self, request, response, *args, **kwargs):
        """输入框架响应，输出禁止缓存与 MIME 嗅探的响应；不改变业务状态或正文。"""
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store, private"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def create(self, request):
        """输入有界文件或文本；创建新版本并返回 201 元数据，不隐式切换当前版本或调用模型。"""
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
        """按本人版本 ID 返回正文及 can_interview；ready 仅表示文本可用，不声称结构化解析完成。"""
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
        """输入版本 ID；锁定用户后切换唯一当前版本；未 ready 返回 400，不改变内容或历史绑定。"""
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
        """输入本人版本 ID；返回私有 PDF 附件，文本版本返回 400；不生成公共文件 URL。"""
        version = self.get_object()
        if version.original_pdf is None:
            raise ValidationError("This version has no original PDF.")
        response = HttpResponse(bytes(version.original_pdf), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="resume-{version.pk}.pdf"'
        return response

    def destroy(self, request, pk=None):
        """输入本人版本 ID；锁内删除未引用且非 parsing 的版本，文件随行删除；冲突返回 409。"""
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
        """输入 uploaded PDF 和可选 mode（默认 traditional）；领取解析返回流，重复 409，不重试。"""
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
        """输入本人 ready 版本 ID，返回单元、确认槽位与原件全字段提取建议，不写库或调用模型。
        原件只依据显式文本提取待确认值；编辑稿不重新提取，保留用户明确清空的字段与历史快照。
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
        """输入本人 ready 基线、单元和确认槽位，事务内创建新 ready 快照，原件/旧稿不覆盖。
        source_version 固定原件根，edited_from 记录基线；不复制 PDF、不自动切换 current 或调用模型。
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
        """输入本人 ready 版本 ID，返回 candidate 与槽位关联说明；不执行排序、补值或模型调用。"""
        version = self.get_object()
        if version.status != "ready":
            raise ValidationError("Resume is not ready.")
        return Response({"candidate": candidate_payload(version), "slot_units": SLOT_UNITS})

    @action(detail=True, methods=["post"])
    def recommendations(self, request, pk=None):
        """输入本人 ready 版本 ID 和空请求体，返回显式岗位目录的原模型排序及展示字段。
        只读已保存槽位，不采用自动建议/前端未保存值；未知保持未知。登录和 CSRF 沿用
        ViewSet；无持久化或外部调用，记录版本 ID、数量及故障类型，失败不生成替代推荐。
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
        by_id = {job.requirements.job_id: job for job in catalog.jobs}
        for result in ranked["results"]:
            job = by_id[result["job_id"]]
            result["job"] = job.model_dump()
            result["matched_skills"] = [
                skill
                for skill in (job.requirements.required_skills or [])
                if skill in (candidate.skills or [])
            ]
        logger.info(
            "Resume recommendation completed version=%s jobs=%s", version.pk, len(catalog.jobs)
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
        """输入本人 ready 版本 ID，输出 UTF-8 文本附件；下载内容为该快照，不修改原件或版本。"""
        version = self.get_object()
        if version.status != "ready":
            raise ValidationError("Resume is not ready.")
        response = HttpResponse(version.text, content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="resume-edition-{version.pk}.txt"'
        return response


async def version_events(version_id, data, *, mode="traditional"):
    """输入版本 ID、PDF 和模式；按配置解析并透传，result 前保存文本，error 保存失败码。
    取消或提前关闭将 parsing 标为 interrupted；异常记录类型后传播，不增加回退或重试。
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
    """输入协议命令和可信用户 ID；返回填入本人版本文本的命令，不调用模型或改变输入版本。
    未提供 ID 保持旧文本路径；匿名、跨用户、未 ready 均抛 ValueError，由协议返回固定错误。
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
