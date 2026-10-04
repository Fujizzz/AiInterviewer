"""Responsibilities: 检查系统拟执行行为并提供检查后执行接口，不分类用户攻击。
Implementation: 快照、确定性边界、必需语义检查、完整覆盖、重读绑定和业务操作。
Related Modules: behavior_bounds 负责程序约束，BehaviorReviewer 提供语义审查端口。

Declaration Index:
- BehaviorReviewer: 可替换的异步行为审查端口。
- BehaviorReviewer.assess: 审查拟执行行为，返回要求级结果。
- BehaviorBlocked: 行为明确不合格。
- BehaviorBlocked.__init__: 保存脱敏拒绝决策。
- BehaviorCheckFailed: 无法完成行为检查。
- BehaviorCheckFailed.__init__: 保存失败决策。
- behavior_digest: 计算完整请求的稳定摘要。
- BehaviorEngine: 组合程序边界与语义合格性。
- BehaviorEngine.__init__: 冻结策略并验证端口。
- BehaviorEngine.check: 检查提案，不执行或修改提案。
- BehaviorEngine.require_allowed: 只返回allow，否则抛明确异常。
- execute_behavior_checked: 检查、刷新并调用一次受保护业务操作。

Variable Index:
- logger: 有限标识、阶段、违反项、异常类型和耗时日志。
- Result: 业务回调结果类型。

Constraints:
_policy 是独立快照，_reviewer 为必需端口。无模型失败回退、自动重试或静默修复。
输入中的恶意指令不是拒绝依据；拒绝依据是提案违反后端边界。error 不视为攻击命中。
operation 必须按已检快照执行，并在数据库事务或发送协调边界再次核对状态和授权。
刷新后仍可能有竞态；接口不认证来源、签发可重用令牌或回滚已经开始的业务副作用。
"""

import asyncio
import hashlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Protocol, TypeVar
from uuid import uuid4

from shared.contracts.behavior import BehaviorAssessment, BehaviorDecision, BehaviorRequest

from .behavior_bounds import canonical_json, inspect_behavior_bounds
from .errors import SecurityContextChanged
from .policy import SecurityPolicy

logger = logging.getLogger(__name__)
Result = TypeVar("Result")


class BehaviorReviewer(Protocol):
    """功能：隔离行为审查模型；逻辑：单次异步快照审查；约束：不能调用业务工具或增加许可。"""

    async def assess(self, request: BehaviorRequest) -> BehaviorAssessment:
        """功能：评估行为；输入：后端边界、证据、提案；输出：合格性；异常由引擎分类，不重试。"""
        ...


class BehaviorBlocked(RuntimeError):
    """功能：表示行为被拒绝；逻辑：保存违反项；约束：不能进入业务自动修复或备用执行路径。"""

    def __init__(self, decision: BehaviorDecision):
        """功能：构造异常；输入：拒绝结果；输出：实例；字符串不包含提案或证据正文。"""
        self.decision = decision
        super().__init__("Behavior outside permitted boundary")


class BehaviorCheckFailed(RuntimeError):
    """功能：表示检查未完成；逻辑：区别于违反边界；约束：仍阻止执行，不给候选人攻击标签。"""

    def __init__(self, decision: BehaviorDecision):
        """功能：构造异常；输入：失败结果；输出：实例；仅暴露固定错误码。"""
        self.decision = decision
        super().__init__(f"Behavior check incomplete: {decision.error_code}")


def behavior_digest(request: BehaviorRequest) -> str:
    """功能：绑定完整行为；输入：快照；输出：SHA256；逻辑：包含全部边界、正文和参数，不记录原文。"""
    return hashlib.sha256(canonical_json(request.model_dump(mode="json")).encode()).hexdigest()


class BehaviorEngine:
    """功能：检查系统行为；逻辑：程序越界先拒绝，其余必需语义覆盖；约束：不执行工具。"""

    def __init__(self, policy: SecurityPolicy, reviewer: BehaviorReviewer):
        """功能：初始化；输入：显式全局操作名单、预算和行为端口；输出：引擎；无默认端口或策略。"""
        self._policy = SecurityPolicy.model_validate_json(policy.model_dump_json())
        if not inspect.iscoroutinefunction(getattr(reviewer, "assess", None)):
            raise ValueError("an asynchronous behavior reviewer is required")
        self._reviewer = reviewer

    async def check(self, request: BehaviorRequest) -> BehaviorDecision:
        """功能：判断行为合格性；输入：完整请求；输出：allow/deny/error；非法请求与取消向上传播。

        合格必须覆盖该操作全部语义要求；未知要求、非法响应、超时均失败关闭。不从正文推断权限。
        日志只记录请求标识、违反项、异常类型和耗时；模型端口收到隔离副本，不能污染摘要。
        """
        started = perf_counter()
        snapshot = BehaviorRequest.model_validate_json(request.model_dump_json())
        violations = inspect_behavior_bounds(snapshot, self._policy)
        status, coverage, error = (
            ("deny", "deterministic", None) if violations else ("allow", "semantic", None)
        )
        logger.info(
            "Behavior check starting request=%s operation=%s",
            snapshot.request_id,
            snapshot.proposal.operation,
        )
        if not violations:
            permit = next(
                p for p in snapshot.boundary.permits if p.operation == snapshot.proposal.operation
            )
            required = set(permit.requirement_ids)
            try:
                semantic_started = perf_counter()
                async with asyncio.timeout(self._policy.semantic_timeout_seconds):
                    raw = await self._reviewer.assess(snapshot.model_copy(deep=True))
                if perf_counter() - semantic_started >= self._policy.semantic_timeout_seconds:
                    raise TimeoutError("behavior deadline exceeded")
                result = BehaviorAssessment.model_validate(raw)
                checked = set(result.checked_requirement_ids)
                if not checked.issubset(required):
                    raise ValueError("unknown semantic requirement")
                if result.verdict == "compliant":
                    if checked != required:
                        raise ValueError("incomplete semantic coverage")
                elif result.verdict == "noncompliant":
                    status, violations = "deny", result.violated_requirement_ids
                else:
                    status, coverage, error = "error", "incomplete", "semantic_uncertain"
            except TimeoutError:
                status, coverage, error = "error", "incomplete", "semantic_timeout"
            except asyncio.CancelledError:
                logger.info("Behavior check cancelled request=%s", snapshot.request_id)
                raise
            except Exception as exc:
                logger.warning(
                    "Behavior review failed request=%s exception=%s",
                    snapshot.request_id,
                    type(exc).__name__,
                )
                status, coverage, error = "error", "incomplete", "semantic_failure"
        decision = BehaviorDecision(
            request_id=snapshot.request_id,
            decision_id=uuid4().hex,
            request_digest=behavior_digest(snapshot),
            policy_version=self._policy.policy_version,
            boundary_version=snapshot.boundary.policy_version,
            status=status,
            coverage=coverage,
            violations=violations,
            error_code=error,
            latency_ms=(perf_counter() - started) * 1000,
        )
        logger.info(
            "Behavior decision request=%s decision=%s status=%s coverage=%s violations=%s "
            "error=%s latency_ms=%.2f",
            decision.request_id,
            decision.decision_id,
            status,
            coverage,
            violations,
            error,
            decision.latency_ms,
        )
        return decision

    async def require_allowed(self, request: BehaviorRequest) -> BehaviorDecision:
        """功能：强制检查；输入：请求；输出：allow 决策；deny/error 分别抛错，不得吞掉继续。"""
        decision = await self.check(request)
        if decision.status == "deny":
            raise BehaviorBlocked(decision)
        if decision.status == "error":
            raise BehaviorCheckFailed(decision)
        return decision


async def execute_behavior_checked(
    engine: BehaviorEngine,
    request: BehaviorRequest,
    *,
    refresh_request: Callable[[], Awaitable[BehaviorRequest]],
    operation: Callable[[BehaviorRequest], Awaitable[Result]],
) -> Result:
    """功能：保护执行边界；输入：引擎、请求、后端重读和事务回调；输出：原业务结果。

    只检查一次，重读后的完整摘要必须一致；未通过则不调用业务，异常/取消传播且不重试。
    重读必须查询真实状态与许可；operation 必须使用传入快照，并原子校验授权/版本。
    """
    snapshot = BehaviorRequest.model_validate_json(request.model_dump_json())
    stage = "inspection"
    try:
        decision = await engine.require_allowed(snapshot)
        stage = "refresh"
        latest = await refresh_request()
        latest = BehaviorRequest.model_validate_json(latest.model_dump_json())
        if behavior_digest(latest) != decision.request_digest:
            raise SecurityContextChanged("behavior changed before execution")
        stage = "operation"
        logger.info(
            "Behavior execution starting request=%s decision=%s state_version=%s",
            snapshot.request_id,
            decision.decision_id,
            snapshot.boundary.state_version,
        )
        result = await operation(snapshot.model_copy(deep=True))
        logger.info("Behavior execution completed request=%s", snapshot.request_id)
        return result
    except asyncio.CancelledError:
        logger.info("Behavior execution cancelled request=%s stage=%s", snapshot.request_id, stage)
        raise
    except Exception as exc:
        logger.warning(
            "Behavior execution failed request=%s stage=%s exception=%s",
            snapshot.request_id,
            stage,
            type(exc).__name__,
        )
        raise
