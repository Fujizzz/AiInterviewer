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

from app.adapters.evaluation import AnswerEvidence
from app.adapters.llm import GeneratedText
from app.parsing.resume import ResumeExtraction
from app.reporting.final_report import ReportNarrative

RESUME = "Alex built a Python log analysis pipeline, tested malformed records with pytest."
ANSWER = "I implemented a bounded-memory parser and tested malformed records separately."


class FixtureLLM:
    """提供通过真实 Pydantic 契约验证的离线数据，统计调用并可注入失败。"""

    def __init__(self, *, interview_id=None):
        """每个测试会话独立统计，不建立模型连接。"""
        self.calls = []
        self.closed = False

    def __call__(self, prompt, data, schema):
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
        elif schema is GeneratedText:
            topic = str(data["question_plan"]["topic"]).rstrip(".,;:")
            output = {"text": f"What did you personally implement for {topic}, and why?"}
        elif schema is AnswerEvidence:
            output = {
                "answer_relevance": 0.9,
                "evidence_strength": 0.8,
                "evaluation_confidence": 0.85,
                "rubric_level": 3,
                "contradiction_detected": False,
                "needs_clarification": False,
                "evidence_summary": "The answer describes implementation and tests.",
            }
        elif schema is ReportNarrative:
            output = {
                "strengths": ["Explained implementation."],
                "weaknesses": ["Some competencies remain untested."],
                "summary": "Offline fixture report; no real model was called.",
            }
        else:
            raise AssertionError(f"Unexpected schema: {schema.__name__}")
        return schema.model_validate(output)

    def close(self):
        """标记资源清理，让断线测试验证生命周期。"""
        self.closed = True
