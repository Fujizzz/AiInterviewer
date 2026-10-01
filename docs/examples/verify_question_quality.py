"""Opt-in real-provider regression, using synthetic resume/answers (API charges apply).

Run from the project root: python -m docs.examples.verify_question_quality
No resume parsing or candidate scoring is performed. Offline pytest does not run this.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from agents.config import load_agent_settings
from agents.domain.models import InterviewHistoryEntry
from agents.orchestrator import InterviewAgentService
from agents.question.quality import QuestionQualityGate
from agents.question.react import QuestionAgentDecision, ReactQuestionAgent
from agents.tracing import trace_sink
from app.adapters import InMemoryInterviewRepository
from app.adapters.llm import ProviderLLMAdapter
from app.providers.llm import OpenAILLM
from shared.contracts import (
    AnswerAnalysis,
    CandidateAnswer,
    CandidateClaim,
    CandidateProfile,
    CandidateProject,
    EvaluationFeedback,
    InitializeInterviewRequest,
    InterviewStage,
    JobProfile,
)

OLD_QUESTION = (
    "In your lip synchronization project, what Transformer architecture did you use "
    "and how did you adapt its attention mechanism for temporal alignment?"
)
CASES = [
    (
        "换词重问",
        "SEMANTIC_REPEAT",
        True,
        "In your lip synchronization project, can you identify the Transformer architecture "
        "you designed and the attention adaptations you implemented?",
    ),
    (
        "一个问号里的多个要求",
        "OVERLOADED_QUESTION",
        True,
        "In your lip synchronization project, describe a concrete pipeline problem, "
        "the optimization you implemented and its measured performance impact?",
    ),
    (
        "内部预算泄露",
        "INTERNAL_RULE_LEAK",
        False,
        "Since the topic limit has been reached, which step used Ray "
        "in your lip synchronization project?",
    ),
    (
        "未经证实的具体经历",
        "UNSUPPORTED_PREMISE",
        False,
        "In your lip synchronization project, how did you resolve the data synchronization "
        "failures using Ray actors and tasks?",
    ),
    (
        "有效的收窄追问",
        None,
        True,
        "In your lip synchronization project, which part of the Transformer-related work "
        "did you personally handle?",
    ),
    (
        "有依据的 Ray 开场",
        None,
        False,
        "In the GPU-CPU pipeline of your lip synchronization project, which step used Ray?",
    ),
    (
        "正常的技术预算讨论",
        None,
        False,
        "If your lip synchronization pipeline had to run within a smaller GPU memory budget, "
        "which component would you inspect first?",
    ),
    (
        "Ray 标签下重开 Transformer",
        "TOPIC_MISMATCH",
        False,
        "In your lip synchronization project, "
        "which Transformer component did you personally build?",
    ),
    (
        "单个修改不是多问",
        None,
        False,
        "In your lip synchronization project's Ray pipeline, what was one specific change "
        "you made to its configuration or code?",
    ),
]


async def scenario():
    repository = InMemoryInterviewRepository()
    service = InterviewAgentService(repository=repository)
    request = InitializeInterviewRequest(
        interview_id="quality-regression",
        candidate_profile=CandidateProfile(
            candidate_id="synthetic",
            projects=[
                CandidateProject(
                    project_id="lip",
                    name="Lip synchronization",
                    technologies=["Transformer", "Ray"],
                    claims=[
                        CandidateClaim(
                            claim_id="alignment", text="Transformer-based temporal alignment"
                        ),
                        CandidateClaim(
                            claim_id="pipeline", text="Used Ray to optimize a GPU-CPU pipeline"
                        ),
                    ],
                )
            ],
        ),
        job_profile=JobProfile(job_id="ai", title="AI Engineer", competency_importance={}),
        duration_seconds=900,
        enabled_stages=[InterviewStage.PROJECT_DEEP_DIVE],
    )
    root = (await service.initialize_interview(request)).first_action.question
    root = root.model_copy(
        update={"text": OLD_QUESTION, "information_goal": "Understand architecture and adaptation"}
    )
    context = await repository.get_interview_context(request.interview_id)
    context.active_thread.goals = [root.information_goal]
    context.question_history = [
        InterviewHistoryEntry(
            question=root,
            answer=CandidateAnswer(
                interview_id=request.interview_id,
                question_id=root.question_id,
                answer_id="synthetic-answer",
                text="I design the system",
            ),
            feedback=EvaluationFeedback(
                request_id="synthetic-feedback",
                question_id=root.question_id,
                answer_relevance=0.3,
                evidence_strength=0.1,
                analysis=AnswerAnalysis(answer_scope="label_only", status="partial"),
            ),
        )
    ]
    return root, context


class SeededBadDraft:
    """Inject one historical-style draft, then use real generation and real reviews."""

    def __init__(self, provider):
        self.provider = provider
        self.seeded = False

    async def generate_structured(self, *, prompt_name, payload, response_model):
        if response_model is QuestionAgentDecision and not self.seeded:
            self.seeded = True
            active = payload["dialogue_state"]["active_thread"]
            return response_model(
                action="final",
                text=CASES[0][3],
                selection={
                    "dialogue_action": "clarify",
                    "project_id": active["project_id"],
                    "topic_key": active["topic_key"],
                    "information_goal": active["goals"][0],
                    "decision_summary": "Injected historical-style repeated draft for regression.",
                },
            )
        return await self.provider.generate_structured(
            prompt_name=prompt_name,
            payload=payload,
            response_model=response_model,
        )


async def main():
    root, context = await scenario()
    settings = load_agent_settings()
    provider = OpenAILLM()
    adapter = ProviderLLMAdapter(provider)
    gate = QuestionQualityGate(adapter, settings)
    lines = [
        "# 提问质量真实模型验证",
        "",
        f"时间（UTC）：{datetime.now(UTC).isoformat()}",
        f"模型：{provider.model}",
        "",
        "简历、回答及待审问题为预设数据；质量判断和后续修复调用真实模型。"
        "这不是完整面试，也不验证候选人评分。",
        "",
    ]
    failures = 0
    for name, expected, continuing, text in CASES:
        question = root.model_copy(
            update={
                "text": text,
                "dialogue_action": "clarify" if continuing else "new_topic",
                "topic": root.topic
                if continuing
                else (
                    "GPU-CPU pipeline optimization"
                    if name == "正常的技术预算讨论"
                    else "Ray pipeline"
                ),
                "information_goal": "Clarify the stated work in the selected topic",
            }
        )
        review = await gate.review(question, context)
        codes = [issue.code for issue in review.issues]
        passed = expected in codes if expected else not codes
        failures += not passed
        lines += [
            f"## {name}",
            f"问题：{text}",
            f"审查：{', '.join(codes) or 'PASS'}；符合预期：{passed}",
            "修复建议：" + "；".join(issue.instruction for issue in review.issues)
            if review.issues
            else "无需修复。",
            "",
        ]
        print(f"{name}: {codes or 'PASS'}; expected={passed}", flush=True)

    events = []
    token = trace_sink.set(lambda event, data: events.append((event, data)))
    try:
        result = await ReactQuestionAgent(SeededBadDraft(adapter), settings).generate(
            root,
            "",
            interview=context,
            autonomous=True,
        )
    finally:
        trace_sink.reset(token)
    lines += ["## 一次真实修复流程", "第一份重复草稿为人为注入；审查和修复均为真实调用。"]
    for event, data in events:
        if event == "question.quality":
            lines.append(f"质量检查：{data['status']}；{', '.join(data['issues'])}")
        elif event == "model.error":
            lines.append(
                f"调用错误：{data['operation']} / {data['category']} / "
                f"{data.get('validation_issues', [])}"
            )
    final_text = result.question.text if result.question else "未放行，生产流程将使用兜底问题"
    lines += [
        f"结束原因：{result.stop_reason}；已修复：{result.repaired}",
        f"最终问题：{final_text}",
    ]
    failures += not (result.question and result.repaired)
    lines += [
        "",
        f"未符合预期的检查数：{failures}",
        "语义审查仍可能漏判或误判，本次结果只覆盖这些样例。",
    ]
    path = Path("output/提问质量真实模型验证.md")
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    print(path.resolve(), flush=True)
    return failures


if __name__ == "__main__":
    raise SystemExit(bool(asyncio.run(main())))
