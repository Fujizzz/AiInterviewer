"""职责：新增简历版本存储及面试绑定，不回填或认领旧记录。
实现：添加私有 PDF/文本表、当前版本唯一约束和面试 PROTECT 外键；旧面试引用保持空。
关联：ResumeVersion 与 AgentInterview，依赖账号归属迁移和认证用户表。
目录：
- Migration：定义版本表、绑定列及状态约束；逆迁移删除这些新增数据和列。
关键变量：
（无模块级变量。）
配置说明：
dependencies 固定迁移顺序；operations 仅调整结构，不调用模型或修改评分。
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """功能：结构迁移；逻辑：先创建版本再绑定；约束：不改变已有面试状态或实验参数。"""

    dependencies = [
        ("interviews", "0006_account_ownership"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="agentinterview",
            name="resume_text_snapshot",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="ResumeVersion",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("label", models.CharField(blank=True, max_length=120)),
                ("original_name", models.CharField(blank=True, max_length=255)),
                ("original_pdf", models.BinaryField(null=True)),
                ("text", models.TextField(blank=True)),
                ("status", models.CharField(default="uploaded", max_length=16)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("is_current", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddField(
            model_name="agentinterview",
            name="resume_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="interviews",
                to="interviews.resumeversion",
            ),
        ),
        migrations.AddConstraint(
            model_name="resumeversion",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_current", True)),
                fields=("owner",),
                name="resume_one_current",
            ),
        ),
        migrations.AddConstraint(
            model_name="resumeversion",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "status__in",
                        ["uploaded", "parsing", "ready", "failed", "interrupted"],
                    )
                ),
                name="resume_valid_status",
            ),
        ),
    ]
