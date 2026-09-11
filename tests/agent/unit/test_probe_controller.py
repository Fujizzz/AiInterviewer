from agents.policies import ProbeController
from shared.contracts import Competency
from tests.agent.factories import interview_plan, interview_state


def test_probe_limit_is_strictly_enforced() -> None:
    state = interview_state()
    plan = interview_plan()
    state.consecutive_probes = 3
    plan.max_consecutive_probes = 3

    decision = ProbeController().decide(
        competency=Competency.DEBUGGING,
        state=state,
        plan=plan,
    )

    assert decision.should_probe is False
    assert decision.reason_code == "MAX_PROBES_REACHED"
