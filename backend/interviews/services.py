"""事务性业务用例。将题目选择、版本竞争、单题转换和结束场次作为明确事务边界。

目录：
- create_session：
  功能：按启用题库或指定 ID 顺序创建归属于调用用户的场次及文字快照。
- claim_session：
  功能：为指定版本的 active 场次取得本次事务的写入资格。
- update_item：
  功能：执行单题 start、complete 或 skip 状态转换。
- finish_session：
  功能：将场次置为 completed，并将尚未完成的题目标记 skipped。

关键变量：
- logger：
  当前模块的控制台日志入口；上下文标识及异常处理方式见相应函数。
"""

import logging

from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .errors import Conflict
from .models import PracticeSession, Question, SessionQuestion

logger = logging.getLogger(__name__)


@transaction.atomic
def create_session(question_ids=None, *, owner=None):
    """功能：按启用题库或指定 ID 顺序创建场次及文字快照。
    输入：可选 question_ids 和已认证 owner；None 仅供未登录的本地开发记录；返回场次。
    方法：检查 1–100 题边界后，在同一事务中创建场次并批量写入单题。
    异常：缺失、停用或空选择抛出 ValidationError，事务整体回滚。"""
    available = Question.objects.filter(enabled=True)
    if question_ids is not None:
        questions = list(available.filter(pk__in=question_ids))
        by_id = {q.id: q for q in questions}
        if any(pk not in by_id for pk in question_ids):
            raise ValidationError({"question_ids": "Some questions are missing or disabled."})
        questions = [by_id[pk] for pk in question_ids]
    else:
        # 第 101 条只用于判定既定上限，避免无效大题库被整体载入内存。
        questions = list(available[:101])
    if not questions or len(questions) > 100:
        raise ValidationError({"question_ids": "Select between 1 and 100 enabled questions."})
    session = PracticeSession.objects.create(owner=owner)
    SessionQuestion.objects.bulk_create(
        [
            SessionQuestion(session=session, question=q, question_text=q.text, position=index)
            for index, q in enumerate(questions, start=1)
        ]
    )
    logger.info("Session created session=%s questions=%d", session.id, len(questions))
    return session


def claim_session(session_id, version):
    """功能：为指定版本的 active 场次取得本次事务的写入资格。
    方法：先条件 UPDATE 递增版本，再读回状态，避免依赖 SQLite 行锁。
    返回：更新后的场次；不存在为 404，未更新任何行则为 Conflict。
    约束：只从外层原子事务调用，后续验证失败时版本也必须回滚。"""
    changed = PracticeSession.objects.filter(
        pk=session_id, version=version, status="active"
    ).update(version=F("version") + 1)
    session = get_object_or_404(PracticeSession, pk=session_id)
    if not changed:
        raise Conflict(
            "Session is completed or version is stale. "
            "Fetch its current state before deciding the next action."
        )
    return session


@transaction.atomic
def update_item(session_id, item_id, command):
    """功能：执行单题 start、complete 或 skip 状态转换。
    输入：场次 ID、单题 ID 与已验证动作；返回：更新后的完整场次对象。
    方法：先竞争版本，再检查归属及状态，最后保存作答与服务端时间。
    副作用：事务写库及上下文日志；非法转换不产生部分修改。"""
    session = claim_session(session_id, command["version"])
    item = get_object_or_404(SessionQuestion, pk=item_id, session=session)
    action = command["action"]
    now = timezone.now()
    if action == "start":
        if item.status != "pending" or session.items.filter(status="answering").exists():
            raise Conflict("Only a pending item can start, and only one item may be answering.")
        item.status, item.started_at = "answering", now
    elif action == "complete":
        if item.status != "answering":
            raise Conflict("Only an answering item can complete.")
        item.status, item.finished_at = "completed", now
        item.answer_text = command.get("answer_text", "")
        item.duration_ms = command.get("duration_ms")
    else:
        if item.status not in ("pending", "answering"):
            raise Conflict("Only pending or answering items can be skipped.")
        item.status, item.finished_at = "skipped", now
    item.save()
    logger.info(
        "Item transition session=%s item=%s action=%s version=%d",
        session.id,
        item.id,
        action,
        session.version,
    )
    return session


@transaction.atomic
def finish_session(session_id, version):
    """功能：将场次置为 completed，并将尚未完成的题目标记 skipped。
    方法：先竞争版本，单次获取结束时间，在一个事务中更新题目和场次。
    返回：新版本场次；版本冲突、已结束或不存在时保持错误语义。"""
    session = claim_session(session_id, version)
    now = timezone.now()
    session.items.filter(status__in=["pending", "answering"]).update(
        status="skipped", finished_at=now
    )
    session.status, session.finished_at = "completed", now
    session.save(update_fields=["status", "finished_at"])
    logger.info("Session finished session=%s version=%d", session.id, session.version)
    return session
