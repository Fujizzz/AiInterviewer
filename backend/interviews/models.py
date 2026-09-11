"""业务数据模型与数据库约束。题目快照和状态时间关系在存储层得到保护。

目录：
- Question
- Question.Meta
- PracticeSession
- PracticeSession.Status
- PracticeSession.Meta
- SessionQuestion
- SessionQuestion.Status
- SessionQuestion.Meta
"""

import uuid

from django.db import models
from django.db.models import Q


class Question(models.Model):
    """题库实体。排序字段决定新场次取题顺序；停用不影响已创建的题目快照。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    text = models.TextField(max_length=4000)
    position = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """按 position、创建时间和主键稳定排序，避免同序号下的随机顺序。"""

        ordering = ["position", "created_at", "id"]


class PracticeSession(models.Model):
    """场次实体。保存固定准备/回答时长、状态、时间和乐观并发版本。"""

    class Status(models.TextChoices):
        """场次状态域：active 可修改，completed 为不可重新开启的终态。"""

        ACTIVE = "active"
        COMPLETED = "completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=16, choices=Status, default=Status.ACTIVE)
    prep_seconds = models.PositiveIntegerField(default=10, editable=False)
    answer_seconds = models.PositiveIntegerField(default=90, editable=False)
    version = models.PositiveIntegerField(default=1)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """按创建时间倒序检索，并约束状态与 finished_at 的空值关系。"""

        ordering = ["-started_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(status="active", finished_at__isnull=True)
                | Q(status="completed", finished_at__isnull=False),
                name="session_status_timestamp",
            )
        ]


class SessionQuestion(models.Model):
    """单题快照与作答实体。关联源题目可置空，历史文字和作答仍保留。"""

    class Status(models.TextChoices):
        """单题状态域：pending、answering、completed、skipped。"""

        PENDING = "pending"
        ANSWERING = "answering"
        COMPLETED = "completed"
        SKIPPED = "skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(PracticeSession, related_name="items", on_delete=models.CASCADE)
    question = models.ForeignKey(Question, null=True, on_delete=models.SET_NULL)
    question_text = models.TextField(max_length=4000)
    position = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    answer_text = models.TextField(max_length=20000, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """约束同场次题目顺序唯一、最多一题 answering，以及合法状态时间组合。"""

        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["session", "position"], name="unique_session_position"),
            models.UniqueConstraint(
                fields=["session"], condition=Q(status="answering"), name="one_answering_item"
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="pending", started_at__isnull=True, finished_at__isnull=True)
                    | Q(status="answering", started_at__isnull=False, finished_at__isnull=True)
                    | Q(status="completed", started_at__isnull=False, finished_at__isnull=False)
                    | Q(status="skipped", finished_at__isnull=False)
                ),
                name="item_status_timestamps",
            ),
        ]
