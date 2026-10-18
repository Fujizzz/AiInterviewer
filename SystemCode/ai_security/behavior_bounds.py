"""Responsibilities: 检查操作、状态、数据、参数许可及明确后端保密值的实际披露。
Implementation: 逐项比较许可和来源受众；附加严格声明的明文凭据披露检查，不分类输入攻击。
Related Modules: BehaviorEngine 在模型前调用；behavior_confidentiality 编译明确保密声明。

Declaration Index:
- canonical_json: 精确比较 JSON 值，区分布尔值与整数。
- inspect_behavior_bounds: 返回越界/明文披露项 ID；无违反项只表示可继续语义检查。

Variable Index:
None

Constraints:
fields 必须对应执行器真实读写/投影字段；不得检查小范围后执行大范围。
资源、受众和来源标签来自后端。未声明的隐式数据流和任意回调行为无法被本模块发现。
"""

import json

from shared.contracts.behavior import BehaviorRequest

from .behavior_confidentiality import disclosed_literal_requirements
from .policy import SecurityPolicy


def canonical_json(value) -> str:
    """功能：规范化 JSON；输入：已验证值；输出：稳定字符串；不归一化正文或合并类型。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def inspect_behavior_bounds(request: BehaviorRequest, policy: SecurityPolicy) -> tuple[str, ...]:
    """功能：检查许可；输入：行为快照和全局策略；输出：有限违反项；未知操作/参数默认拒绝。

    字符预算覆盖整个请求，包含后端要求和参数；沿用显式 max_scan_chars，不改默认数值。
    读取证据本身不意味着向收件人披露；只传播提案声明的派生来源和引用的证据受众。
    程序许可通过后，按选定后端明确保密声明检查提案明文；不存在明文披露仍必须语义检查。
    """
    failures = []
    boundary, proposal = request.boundary, request.proposal
    if len(canonical_json(request.model_dump(mode="json"))) > policy.max_scan_chars:
        failures.append("SCAN_LIMIT")
    if proposal.operation not in policy.allowed_actions:
        failures.append("OPERATION_NOT_ENABLED")
    permit = next((p for p in boundary.permits if p.operation == proposal.operation), None)
    if permit is None:
        return tuple([*failures, "OPERATION_NOT_PERMITTED"])
    for valid, code in (
        (boundary.actor_role in permit.roles, "ROLE_OUTSIDE_BOUNDARY"),
        (boundary.phase in permit.phases, "PHASE_OUTSIDE_BOUNDARY"),
        (boundary.stage in permit.stages, "STAGE_OUTSIDE_BOUNDARY"),
        (proposal.resource_id in permit.resource_ids, "RESOURCE_OUTSIDE_BOUNDARY"),
        (proposal.recipient in permit.recipients, "RECIPIENT_OUTSIDE_BOUNDARY"),
        (
            set(proposal.fields).issubset(permit.fields)
            and (bool(proposal.fields) or not permit.fields),
            "FIELDS_OUTSIDE_BOUNDARY",
        ),
        (not permit.requires_evidence or bool(proposal.evidence_ids), "EVIDENCE_REQUIRED"),
        ((proposal.content is not None) == (permit.effect == "output"), "CONTENT_EFFECT_MISMATCH"),
    ):
        if not valid:
            failures.append(code)
    parameters = {p.name: p for p in permit.parameters}
    if set(proposal.arguments) - parameters.keys():
        failures.append("UNKNOWN_PARAMETER")
    for name, bound in parameters.items():
        if name not in proposal.arguments:
            if bound.required:
                failures.append("MISSING_PARAMETER")
            continue
        value = proposal.arguments[name]
        valid = (
            canonical_json(value) in {canonical_json(v) for v in bound.values}
            if bound.kind == "enum"
            else type(value) is int and bound.minimum <= value <= bound.maximum
        )
        if not valid:
            failures.append("PARAMETER_OUTSIDE_BOUNDARY")
    readers = {}
    for item in request.evidence:
        readers[item.content_id] = set(item.readable_by)
        for parent in item.derived_from:
            readers[item.content_id].intersection_update(readers[parent])
    disclosed = set(proposal.evidence_ids)
    if proposal.content is not None:
        allowed = set(proposal.content.readable_by)
        disclosed.update(proposal.content.derived_from)
    else:
        allowed = {"internal", "staff", "candidate"}
    for parent in disclosed:
        allowed.intersection_update(readers[parent])
    if proposal.recipient not in allowed:
        failures.append("PROVENANCE_DISCLOSURE")
    if not failures:
        failures.extend(disclosed_literal_requirements(request))
    return tuple(dict.fromkeys(failures))
