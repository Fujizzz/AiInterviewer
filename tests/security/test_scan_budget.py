import json

from ai_security.behavior import BehaviorEngine
from shared.contracts.behavior import BehaviorRequest
from tests.security.test_behavior import port


async def test_large_request_still_requires_semantic_review(behavior_request, policy):
    raw = behavior_request.model_dump(mode="json")
    raw["proposal"]["content"]["text"] = "x" * 220_000
    request = BehaviorRequest.model_validate_json(json.dumps(raw))
    reviewer = port(request)
    large_policy = policy.model_copy(update={"max_scan_chars": 1_000_000})
    result = await BehaviorEngine(large_policy, reviewer).check(request)
    assert result.status == "allow" and result.coverage == "semantic"
    reviewer.assess.assert_awaited_once()


async def test_large_request_still_obeys_configured_budget(behavior_request, policy):
    raw = behavior_request.model_dump(mode="json")
    raw["proposal"]["content"]["text"] = "x" * 220_000
    request = BehaviorRequest.model_validate_json(json.dumps(raw))
    reviewer = port(request)
    result = await BehaviorEngine(policy, reviewer).check(request)
    assert result.status == "deny" and "SCAN_LIMIT" in result.violations
    reviewer.assess.assert_not_awaited()
