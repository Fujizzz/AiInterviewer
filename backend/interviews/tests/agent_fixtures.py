"""职责：提供离线业务/安全模型及显式测试保存帮助函数，生产路由不引用。
实现：业务输出通过真实契约验证；安全替身固定完整放行，只验证接线和业务回归。
关联：供 Agent 协议、持久化和阶段测试使用；拒绝与故障由 test_agent_safety 独立验证。

目录：
- FixtureLLM：
  提供通过真实 Pydantic 契约验证的离线数据，统计调用并可注入失败。
- FixtureLLM.__init__：
  每个测试会话独立统计，不建立模型连接。
- FixtureLLM.__call__：
  按实际 MVP 所需 schema 构造确定性输出，未知调用立即失败。
- FixtureLLM.close：
  标记资源清理，让断线测试验证生命周期。
- FixtureBehaviorReviewer：显式离线放行替身，不评估真实安全效果。
- FixtureBehaviorReviewer.assess：返回覆盖全部要求的合格结果。
- SafetyTestMixin：隔离已有业务回归的安全外部调用。
- SafetyTestMixin.setUp：仅在测试生命周期注入离线安全端口并注册清理。
- complete_fixture_request：为直接测试仓库的离线结果附加测试批准记录。

关键变量：
- ANSWER：
  与虚构项目配套的固定答案，只用于离线测试。
- RESUME：
  虚构的固定简历文本，只用于离线测试。

关键状态说明：
FixtureLLM.calls 记录请求 schema；closed 标记清理是否发生。
测试输出不代表真实模型能力，也不进入生产默认路径。
"""

from unittest.mock import patch

from agents.question.react import QuestionAgentDecision
from app.adapters.evaluation import AnswerEvidence
from app.adapters.llm import GeneratedText
from app.parsing.resume import ResumeExtraction
from app.reporting.final_report import ReportNarrative
from interviews.agent_models import AgentInterview
from interviews.agent_records import complete_request
from interviews.agent_safety import make_output_receipt
from shared.contracts.behavior import BehaviorAssessment
from tests.agent.mocks.dialogue_output import plan_for, selection_for
from tests.evaluation.port_helpers import SCHEMAS, evaluation_output

RESUME = "Alex built a Python log analysis pipeline, tested malformed records with pytest."
ANSWER = "I implemented a bounded-memory parser and tested malformed records separately."


class FixtureBehaviorReviewer:
    """功能：隔离外部模型；逻辑：固定放行；约束：仅用于业务回归，不能证明真实安全检测能力。"""

    async def assess(self, request):
        """输入真实行为请求，返回完整要求覆盖；不联网、不修改请求，不模拟数据库。"""
        return BehaviorAssessment(
            verdict="compliant",
            checked_requirement_ids=tuple(r.requirement_id for r in request.boundary.requirements),
            violated_requirement_ids=(),
        )


class SafetyTestMixin:
    """功能：为旧业务回归提供显式安全替身；逻辑：逐测试 patch；约束：生产入口不加载。"""

    def setUp(self):
        """无外部参数；读取测试生命周期，替换安全端口并注册还原，返回 None。"""
        super().setUp()
        patcher = patch("interviews.agent_safety.create_behavior_reviewer", FixtureBehaviorReviewer)
        patcher.start()
        self.addCleanup(patcher.stop)


async def complete_fixture_request(interview_id, request_id, result):
    """输入直接 Agent 单元测试的离线结果，显式构造测试凭据并保存；不宣称完成安全审查。

    仅供仓库/评分业务测试绕过外部检测模型；真实网关的检查与拒绝由 test_agent_safety 覆盖。
    """
    record = await AgentInterview.objects.aget(id=interview_id)
    await complete_request(
        interview_id,
        request_id,
        result,
        owner_id=record.owner_id,
        receipt=make_output_receipt(result, request_id, record.state_version),
    )


class FixtureLLM:
    """提供通过真实 Pydantic 契约验证的离线数据，统计调用并可注入失败。"""

    def __init__(self, *, interview_id=None):
        """每个测试会话独立统计，不建立模型连接。"""
        self.calls = []
        self.closed = False

    def __call__(self, prompt, data, schema):
        """按实际 MVP 所需 schema 构造确定性输出，未知调用立即失败。"""
        # Scripted semantic pass; this fixture does not evaluate question quality.
        if schema.__name__ == "QuestionQualityReview":
            return schema(issues=[])
        self.calls.append(schema)
        if schema in SCHEMAS:
            return evaluation_output(prompt, data, schema)
        if schema is ResumeExtraction:
            output = {
                "candidate_name": "Alex",
                "skills": ["Python", "pytest"],
                "projects": [
                    {
                        "name": "Log Analysis Pipeline",
                        "domain": "data engineering",
                        "description": RESUME,
                        "technologies": ["Python", "pytest"],
                        "claims": ["Handled malformed records separately."],
                        "metrics": [],
                    }
                ],
            }
        elif schema in (GeneratedText, QuestionAgentDecision):
            selection = selection_for(data) if "dialogue_state" in data else None
            plan = plan_for(data, selection) if selection else data["question_plan"]
            topic = str(plan["topic"]).rstrip(".,;:")
            output = {"text": f"What did you personally implement for {topic}, and why?"}
        elif schema is AnswerEvidence:
            output = {
                "answer_relevance": 0.9,
                "evidence_strength": 0.8,
                "analysis": {
                    "status": "substantive",
                    "new_information": True,
                    "thread_complete": True,
                },
                "dimensions": [
                    {
                        "competency": "ownership",
                        "observation": "supported",
                        "quote": data["answer"],
                        "fact": data["answer"],
                        "rationale": "Describes personal implementation",
                        "rubric_level": 3,
                        "strength": 0.8,
                    }
                ],
            }
        elif schema is ReportNarrative:
            output = {
                "strengths": ["Explained implementation."],
                "weaknesses": ["Some competencies remain untested."],
            }
        else:
            raise AssertionError(f"Unexpected schema: {schema.__name__}")
        if schema is QuestionAgentDecision:
            output.update(action="final", topic=None, limit=None, selection=selection)
        return schema.model_validate(output)

    def close(self):
        """标记资源清理，让断线测试验证生命周期。"""
        self.closed = True
