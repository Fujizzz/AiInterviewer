"""职责：存储本人简历的不可变输入版本和显式解析生命周期。
实现：原文件保存在数据库，避免公开媒体路径；文本和文件均不覆盖历史版本。
关联：resume_versions 提供授权 API，AgentInterview 固定引用和资料快照。
状态索引：id 为版本 UUID；owner 为用户归属；label 为展示标签；original_name/original_pdf
保存原始 PDF 名称和私有字节；text 为输入或解析正文；status/error_code 为处理状态与固定错误码；
is_current 为当前选择；extraction_mode 记录解析时选定的模式，未开始为空；created_at/updated_at
为审计时间。Meta.constraints 定义状态及当前唯一约束。
source_version 引用原件根版本，edited_from 引用编辑基线；均 PROTECT，不能删除仍被编辑稿引用的版本。
units 保存用户单元文本，recommendation_slots 保存用户确认的推荐字段；原件留空字典。
目录：
- ResumeVersion：保存输入、解析结果、当前选择与失败码。
- ResumeVersion.Meta：按创建时间排序并限制每个用户最多一个当前版本。
关键变量：
（无模块级变量。）
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class ResumeVersion(models.Model):
    """功能：持久化版本；输入为授权 API 的有界 PDF 或文本，输出为数据库记录。
    逻辑：PDF 从 uploaded 经 parsing 到 ready/failed/interrupted；文本直接 ready。
    单元编辑保存新的 ready 快照，保留原件根和基线。
    约束：owner 不为空，原文件和输入不可修改，当前标记不改变版本内容；解析不自动重试。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    label = models.CharField(max_length=120, blank=True)
    original_name = models.CharField(max_length=255, blank=True)
    original_pdf = models.BinaryField(null=True, editable=False)
    source_version = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="editions"
    )
    edited_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="derived_versions"
    )
    units = models.JSONField(default=dict, blank=True)
    recommendation_slots = models.JSONField(default=dict, blank=True)
    text = models.TextField(blank=True)
    status = models.CharField(max_length=16, default="uploaded")
    extraction_mode = models.CharField(max_length=16, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """功能：排序与状态约束；逻辑：条件唯一约束限制当前选择；约束：不按内容合并版本。"""

        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner"], condition=Q(is_current=True), name="resume_one_current"
            ),
            models.CheckConstraint(
                condition=Q(status__in=["uploaded", "parsing", "ready", "failed", "interrupted"]),
                name="resume_valid_status",
            ),
        ]
