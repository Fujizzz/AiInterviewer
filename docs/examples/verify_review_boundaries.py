"""Real-provider regression for false rejections in the 2026-09-21 05:59 interview.

Run: python -m docs.examples.verify_review_boundaries
Question, resume and answer fixtures are preset; every verdict uses the real model.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from agents.config import load_agent_settings
from agents.question.quality import QuestionQualityGate
from app.adapters.llm import ProviderLLMAdapter
from app.providers.llm import OpenAILLM
from docs.examples.verify_question_quality import scenario
from shared.contracts import CandidateClaim

# Project, prior question and answer define the scope; expected verdicts are assertions,
# never supplied to the provider.
CONTEXTS = {
    "ray": (
        "AI Subtitle-to-Lip Synchronization System",
        "Optimized GPU-CPU inference and visualization pipelines with Ray",
        "How did you use Ray to parallelize or distribute the processing in this pipeline?",
        "I optimize them",
    ),
    "sql": (
        "Core Business R&D at Bochun Network Technology",
        "Participated in MySQL table design and SQL optimization",
        None,
        None,
    ),
    "api": (
        "Core Business R&D at Bochun Network Technology",
        "Managed API development and collaborated with front-end teams for integration",
        "What was a specific challenge you faced when integrating these APIs?",
        "yes",
    ),
}
CASES = [
    (
        "Ray：一个处理阶段",
        "ray",
        None,
        "In the AI Subtitle-to-Lip Synchronization System, "
        "which specific processing stage did you optimize using Ray?",
    ),
    (
        "Ray：同一答案的备选项",
        "ray",
        None,
        "In the AI Subtitle-to-Lip Synchronization System, which specific pipeline stage "
        "(e.g., inference or visualization) did you optimize using Ray?",
    ),
    (
        "Ray：一个具体修改",
        "ray",
        None,
        "In your Ray pipeline work, what was one specific technical change "
        "you made to its configuration or code?",
    ),
    (
        "SQL：任选一个例子",
        "sql",
        None,
        "In your Bochun Network Technology work, can you describe a specific instance "
        "where you optimized a slow SQL query or redesigned a table schema?",
    ),
    (
        "SQL：一个例子加效果，确实多问",
        "sql",
        "OVERLOADED_QUESTION",
        "In your Bochun Network Technology work, can you describe a specific SQL optimization "
        "you performed and what performance improvement resulted?",
    ),
    (
        "Ray：阶段和做法，确实多问",
        "ray",
        "OVERLOADED_QUESTION",
        "In the Ray pipeline, which processing stage did you optimize "
        "and how did you implement that optimization?",
    ),
    (
        "API：yes 后澄清个人职责",
        "api",
        None,
        "In your Bochun Network Technology API integration work, what was your own responsibility?",
    ),
    (
        "Ray：不能放行虚构的故障经历",
        "ray",
        "UNSUPPORTED_PREMISE",
        "In your lip synchronization project, how did you resolve the data synchronization "
        "failures using Ray actors and tasks?",
    ),
]


async def main():
    root, original = await scenario()
    provider = OpenAILLM()
    gate = QuestionQualityGate(ProviderLLMAdapter(provider), load_agent_settings())
    lines = [
        "# 审查边界真实模型回归",
        "",
        f"时间（UTC）：{datetime.now(UTC).isoformat()}；模型：{provider.model}",
        "",
        "问答和项目依据为预设回归数据；审查调用真实模型。",
        "",
    ]
    failures = 0
    for name, scope, expected, text in CASES:
        project_name, topic, previous, answer = CONTEXTS[scope]
        context = original.model_copy(deep=True)
        project = context.candidate_profile.projects[0]
        project.name = project_name
        project.technologies = []
        project.claims = [CandidateClaim(claim_id=scope, text=topic)]
        if previous:
            entry = context.question_history[0]
            entry.question = root.model_copy(
                update={"topic": topic, "text": previous, "information_goal": "Describe this work"}
            )
            entry.answer.text = answer
            entry.feedback.analysis.answer_scope = "none"
            entry.feedback.analysis.status = "non_answer"
        else:
            context.question_history = []
        question = root.model_copy(
            update={
                "topic": topic,
                "text": text,
                "information_goal": "Clarify the candidate's work in the selected topic",
                "dialogue_action": "clarify" if previous else "new_topic",
            }
        )
        review = await gate.review(question, context)
        codes = [issue.code for issue in review.issues]
        passed = expected in codes if expected else not codes
        failures += not passed
        lines += [
            f"## {name}",
            "",
            f"上一问：{previous or '新话题'}",
            f"上一答：{answer or '无'}",
            f"待审问题：{text}",
            f"独立回答要求：{review.answer_requests}",
            f"审查结果：{codes or 'PASS'}；符合预期：{passed}",
            "建议：" + "; ".join(issue.instruction for issue in review.issues),
            "",
        ]
        print(f"{name}: {codes or 'PASS'}; expected={passed}", flush=True)
    lines += [f"未符合预期的样例数：{failures}"]
    path = Path("output/审查边界真实模型回归.md")
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(path.resolve(), flush=True)
    return failures


if __name__ == "__main__":
    raise SystemExit(bool(asyncio.run(main())))
