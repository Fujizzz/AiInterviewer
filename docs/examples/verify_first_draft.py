"""Compare first-draft quality on fixed synthetic contexts with a real provider.

Run: python -m docs.examples.verify_first_draft --baseline-prompt PATH
No repair attempts are allowed. Both variants use the unchanged production reviewer.
Fixtures cover the recent run's failure patterns; they are not a live interview replay.
"""

import argparse
import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from agents.config import load_agent_settings
from agents.domain.models import DialogueThread
from agents.policies.topic_selector import TopicSelector
from agents.question.quality import followup_brief
from agents.question.react import ReactQuestionAgent
from agents.tracing import trace_sink
from app.adapters.llm import ProviderLLMAdapter
from app.providers.llm import OpenAILLM
from docs.examples.verify_question_quality import scenario
from shared.contracts import CandidateClaim, CandidateProject
from shared.contracts.planning import TopicAllocation

# name, resume scope, broad objective, previous question, answer, switch away from old thread
CASES = [
    (
        "fusion",
        "企业知识库中结合向量和关键词检索，融合后前10条进入重排序",
        "Verify hybrid retrieval implementation and its effect on recall",
        "你怎样结合两路检索结果？",
        "合并后取前10条，再重排序。",
        False,
    ),
    (
        "buffer",
        "日志平台采用逐行迭代，每1000条批量写入数据库",
        "Understand buffering implementation and handling of write failures",
        "你如何实现流式日志处理？",
        "逐行读取，每1000条批量写入。",
        False,
    ),
    (
        "stream",
        "日志平台逐行解析，1000条批量写入，坏行隔离并记录原因",
        "Validate streaming implementation and data integrity",
        None,
        None,
        False,
    ),
    (
        "faq_switch",
        "企业知识库使用人工标注的120条内部FAQ进行评估",
        "Assess FAQ selection and coverage of business scenarios",
        "日志平台批量缓冲如何实现？",
        "不清楚具体实现。",
        True,
    ),
    (
        "pgvector",
        "企业知识库使用PostgreSQL和pgvector保存向量",
        "Assess pgvector index configuration and its performance impact",
        None,
        None,
        False,
    ),
    (
        "multihop",
        "企业知识库尚未覆盖复杂多跳问题",
        "Understand architectural limitations and potential mitigation strategies",
        None,
        None,
        False,
    ),
    (
        "vague_change",
        "日志平台优化了流式解析性能",
        "Clarify one concrete implementation change",
        "你在日志平台负责什么？",
        "我做了优化。",
        False,
    ),
    (
        "english_fusion",
        "Knowledge base combines vector and keyword retrieval before reranking",
        "Verify retrieval fusion and its performance impact",
        "How did you combine the retrieval results?",
        "I merge them and take the top ten.",
        False,
    ),
]


async def fixture(case):
    name, claim, objective, previous, answer, switching = case
    root, context = await scenario()
    project = CandidateProject(
        project_id="target",
        name="测试项目 / Test project",
        claims=[CandidateClaim(claim_id=name, text=claim)],
    )
    topic = TopicSelector().candidates(project)[0]
    context.candidate_profile.projects = [project]
    context.plan.planning_enabled = True
    context.plan.topics = [
        TopicAllocation(
            project_id=project.project_id,
            topic_key=topic.topic_key,
            objective=objective,
            completion_criteria=objective,
            budget_seconds=300,
            expected_questions=3,
        )
    ]
    context.topic_progress = {}
    context.closed_threads = []
    context.used_topic_keys = []
    context.active_thread = None
    context.state.current_question_id = None
    question = root.model_copy(
        update={
            "project_id": project.project_id,
            "topic": topic.topic,
            "topic_key": topic.topic_key,
            "information_goal": objective,
            "text": previous or "",
        }
    )
    if previous:
        entry = context.question_history[0]
        if switching:
            old = CandidateProject(
                project_id="old",
                name="旧日志项目",
                claims=[CandidateClaim(claim_id="old", text="实现流式日志解析和去重")],
            )
            context.candidate_profile.projects.append(old)
            old_topic = TopicSelector().candidates(old)[0]
            entry.question = question.model_copy(
                update={
                    "project_id": old.project_id,
                    "topic_key": old_topic.topic_key,
                    "topic": old_topic.topic,
                }
            )
        else:
            entry.question = question
        entry.answer.text = answer
        entry.feedback.analysis.status = "explicit_unknown" if switching else "partial"
        entry.feedback.analysis.thread_complete = False
        entry.feedback.analysis.missing_information = [objective]
        context.active_thread = DialogueThread(
            thread_id=entry.question.thread_id,
            project_id=entry.question.project_id,
            topic=entry.question.topic,
            topic_key=entry.question.topic_key,
            goals=[entry.question.information_goal],
        )
        context.state.current_question_id = entry.question.question_id
        context.used_topic_keys = [entry.question.topic_key]
    else:
        context.question_history = []
    return question, context


class VariantAdapter(ProviderLLMAdapter):
    def __init__(self, provider, baseline_prompt, context):
        super().__init__(provider)
        self.baseline_prompt = baseline_prompt
        self.context = context

    def _prompt(self, name):
        if name == "question_react_v1" and self.baseline_prompt:
            return self.baseline_prompt
        return super()._prompt(name)

    async def generate_structured(self, *, prompt_name, payload, response_model):
        if prompt_name == "question_react_v1" and self.baseline_prompt:
            payload = deepcopy(payload)
            payload.pop("writing_brief", None)
            payload.pop("rejected_attempts", None)
            payload["followup_brief"] = followup_brief(self.context)
        return await super().generate_structured(
            prompt_name=prompt_name,
            payload=payload,
            response_model=response_model,
        )


async def main(args):
    baseline = args.baseline_prompt.read_text(encoding="utf-8")
    provider = OpenAILLM()
    settings = load_agent_settings()
    settings.retries.llm_generation_retries = 0
    lines = [
        "# 首次出题真实 API 对比",
        "",
        f"模型：{provider.model}；固定虚构场景；禁止修复；同一个生产审查器。",
        "",
        "旧版使用修改前提示和输入；新版使用新提示和范围摘要。非历史逐字回放。",
        "",
    ]
    counts = {"baseline": 0, "updated": 0}
    try:
        for index, case in enumerate(CASES):
            # Alternate ordering to reduce consistent order effects.
            variants = ["baseline", "updated"] if index % 2 == 0 else ["updated", "baseline"]
            for variant in variants:
                question, context = await fixture(case)
                events = []
                token = trace_sink.set(
                    lambda event, data, events=events: events.append((event, data))
                )
                try:
                    result = await ReactQuestionAgent(
                        VariantAdapter(
                            provider, baseline if variant == "baseline" else None, context
                        ),
                        settings,
                    ).generate(question, "", interview=context, autonomous=True)
                finally:
                    trace_sink.reset(token)
                counts[variant] += bool(result.question)
                drafts = [
                    d["decision"].get("text")
                    for e, d in events
                    if e == "react.decision" and d["decision"]["action"] == "final"
                ]
                errors = [d["errors"] for e, d in events if e == "react.validation"]
                lines += [
                    f"## {case[0]} / {variant}",
                    "",
                    f"草稿：{drafts}",
                    f"首次合格：{bool(result.question)}；原因：{errors}；{result.stop_reason}",
                    "",
                ]
                print(f"{case[0]} {variant}: {bool(result.question)} {errors}", flush=True)
    finally:
        provider.client.close()
        lines += [
            f"首次合格：{counts}；每组 {len(CASES)} 个场景。",
            "小样本结果；审查通过不等于人工质量满分，不能保证未来通过率。",
        ]
        path = Path("output") / f"first_draft_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(path.resolve(), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-prompt", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
