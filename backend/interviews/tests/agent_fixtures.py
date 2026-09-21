"""离线结构化模型替身，只供测试注入，生产路由不引用。

目录：
- FixtureLLM：
  提供通过真实 Pydantic 契约验证的离线数据，统计调用并可注入失败。
- FixtureLLM.__init__：
  每个测试会话独立统计，不建立模型连接。
- FixtureLLM.__call__：
  按实际 MVP 所需 schema 构造确定性输出，未知调用立即失败。
- FixtureLLM.close：
  标记资源清理，让断线测试验证生命周期。

关键变量：
- ANSWER：
  与虚构项目配套的固定答案，只用于离线测试。
- RESUME：
  虚构的固定简历文本，只用于离线测试。

关键状态说明：
FixtureLLM.calls 记录请求 schema；closed 标记清理是否发生。
测试输出不代表真实模型能力，也不进入生产默认路径。
"""

from agents.question.react import QuestionAgentDecision
from app.adapters.evaluation import AnswerEvidence
from app.adapters.llm import GeneratedText
from app.parsing.resume import ResumeExtraction
from app.reporting.final_report import ReportNarrative
from tests.agent.mocks.dialogue_output import plan_for, selection_for

RESUME = "Alex built a Python log analysis pipeline, tested malformed records with pytest."
ANSWER = "I implemented a bounded-memory parser and tested malformed records separately."


class FixtureLLM:
    """提供通过真实 Pydantic 契约验证的离线数据，统计调用并可注入失败。"""

    def __init__(self, *, interview_id=None):
        """每个测试会话独立统计，不建立模型连接。"""
        self.calls = []
        self.closed = False

    def __call__(self, prompt, data, schema):
        # Scripted semantic pass; this fixture does not evaluate question quality.
        if schema.__name__ == "QuestionQualityReview":
            return schema(issues=[])
        """按实际 MVP 所需 schema 构造确定性输出，未知调用立即失败。"""
        self.calls.append(schema)
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
