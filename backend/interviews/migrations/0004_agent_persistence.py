"""职责：新增 Agent 面试、请求、题目、回答和单轮审计表，不迁移或更改旧练习数据。

实现：由 Django schema editor 创建表、外键、状态约束和历史索引；反向迁移会删除新增表。
关联：对应 agent_models，依赖已存在的 0003_delete_streamprobe。

目录：
- Migration：声明新增表及约束操作，只有显式运行 migrate 才写数据库。

关键变量：
（无模块级变量。）

配置说明：
Migration.dependencies 固定迁移前序；operations 与当前模型结构一致，无数据填充或参数改写。
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """声明新增表及约束操作，只有显式运行 migrate 才写数据库。"""

    dependencies = [
        ("interviews", "0003_delete_streamprobe"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentInterview",
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
                ("status", models.CharField(default="preparing", max_length=16)),
                ("job_title", models.TextField(blank=True)),
                ("context", models.JSONField(blank=True, null=True)),
                ("state_version", models.PositiveIntegerField(default=0)),
                ("latest_action", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(fields=["-created_at", "-id"], name="agent_history_order")
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("context__isnull", True), ("state_version", 0)),
                            models.Q(("context__isnull", False), ("state_version__gte", 1)),
                            _connector="OR",
                        ),
                        name="agent_context_version",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("closed_at__isnull", True),
                                ("status__in", ["preparing", "active"]),
                            ),
                            models.Q(
                                ("closed_at__isnull", False),
                                ("status__in", ["completed", "interrupted", "failed"]),
                            ),
                            _connector="OR",
                        ),
                        name="agent_lifecycle_time",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="AgentQuestion",
            fields=[
                (
                    "id",
                    models.CharField(max_length=255, primary_key=True, serialize=False),
                ),
                ("ordinal", models.PositiveIntegerField()),
                ("payload", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "interview",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="questions",
                        db_index=False,
                        to="interviews.agentinterview",
                    ),
                ),
            ],
            options={
                "ordering": ["ordinal", "id"],
            },
        ),
        migrations.CreateModel(
            name="AgentRequest",
            fields=[
                (
                    "id",
                    models.UUIDField(editable=False, primary_key=True, serialize=False),
                ),
                ("kind", models.CharField(max_length=16)),
                ("status", models.CharField(default="running", max_length=16)),
                ("response", models.JSONField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "interview",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="requests",
                        db_index=False,
                        to="interviews.agentinterview",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.CreateModel(
            name="AgentAnswer",
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
                ("text", models.TextField()),
                ("evaluation", models.JSONField(blank=True, null=True)),
                (
                    "committed_state_version",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "question",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="answer",
                        to="interviews.agentquestion",
                    ),
                ),
                (
                    "request",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="answer",
                        to="interviews.agentrequest",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AgentTurn",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("state_version", models.PositiveIntegerField()),
                ("action", models.JSONField()),
                ("decision_log", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "feedback_request",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="committed_turn",
                        to="interviews.agentrequest",
                    ),
                ),
                (
                    "interview",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="turns",
                        db_index=False,
                        to="interviews.agentinterview",
                    ),
                ),
            ],
            options={
                "ordering": ["state_version"],
            },
        ),
        migrations.AddConstraint(
            model_name="agentquestion",
            constraint=models.UniqueConstraint(
                fields=("interview", "ordinal"), name="agent_question_order"
            ),
        ),
        migrations.AddIndex(
            model_name="agentrequest",
            index=models.Index(fields=["interview", "created_at"], name="agent_request_history"),
        ),
        migrations.AddConstraint(
            model_name="agentrequest",
            constraint=models.UniqueConstraint(
                fields=("interview",),
                condition=models.Q(status="running"),
                name="agent_one_running_request",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentrequest",
            constraint=models.CheckConstraint(
                condition=models.Q(("kind__in", ["prepare", "start", "answer"])),
                name="agent_request_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentrequest",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("finished_at__isnull", True),
                        ("response__isnull", True),
                        ("status", "running"),
                    ),
                    models.Q(
                        ("finished_at__isnull", False),
                        ("response__isnull", False),
                        ("status", "succeeded"),
                    ),
                    models.Q(
                        ("finished_at__isnull", False),
                        ("response__isnull", True),
                        ("status__in", ["failed", "interrupted"]),
                    ),
                    _connector="OR",
                ),
                name="agent_request_result",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentanswer",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("committed_state_version__isnull", True),
                        ("evaluation__isnull", True),
                    ),
                    models.Q(
                        ("committed_state_version__gte", 1),
                        ("committed_state_version__isnull", False),
                        ("evaluation__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="agent_answer_evidence",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentturn",
            constraint=models.UniqueConstraint(
                fields=("interview", "state_version"), name="agent_turn_version"
            ),
        ),
    ]
