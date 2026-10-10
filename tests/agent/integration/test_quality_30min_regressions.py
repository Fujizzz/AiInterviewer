"""Production gate regressions for the ROI repeat and internship grounding failures."""

from unittest.mock import AsyncMock

import pytest

from agents.config import load_agent_settings
from agents.question.quality import (
    GroundingAdjudication,
    QuestionQualityGate,
    QuestionQualityReview,
    RepeatAdjudication,
)
from agents.question.react import ReactQuestionAgent
from tests.agent.integration.test_quality_completion import scenario
from tests.agent.integration.test_question_react import decision

PREVIOUS = "你们是如何验证ROI裁剪确实减少了DiT tokens、VAE计算量和GPU内存占用的？"
ANSWER = (
    "用同一输入视频、分辨率、模型、种子和采样配置，对比全帧处理与ROI处理。"
    "记录DiT token数量、VAE处理时间和峰值GPU显存。我没有可确认的收益数字。"
)
REPEAT = "你们如何验证ROI的收益？请说明具体的对比实验设置和测量指标。"
RESULT_REQUEST = "在相同配置下，实际测得的GPU峰值显存下降数值是多少？"


class ROIReview:
    def __init__(self, text=REPEAT, detail_quote="", mixed=False):
        self.text, self.detail_quote, self.mixed = text, detail_quote, mixed

    async def generate_structured(self, *, prompt_name, payload, response_model):
        prior = payload["comparisons"][0]
        units = [
            {
                "request_quote": self.text,
                "requested_fact": "实际测得的GPU峰值显存下降数值",
                "missing_detail": "实际测得的GPU峰值显存下降数值尚未提供",
                "new_detail_quote": self.detail_quote,
            }
        ]
        if self.mixed:
            units.append(
                {
                    "request_quote": "对比实验设置",
                    "requested_fact": "对比实验设置",
                    "previous_answer_quote": prior["answer"].split("。")[0],
                }
            )
        return response_model(
            checks=[
                {
                    "comparison_question_id": prior["question_id"],
                    "verdict": "not_repeat",
                    "relation": "narrower_unanswered_request",
                    "current_request_quote": self.text,
                    "previous_request_quote": prior["text"],
                    "reason": "Results are missing, although the setup and metrics were described.",
                    "answer_units": units,
                }
            ]
        )


async def roi_case(text=REPEAT):
    _, context, target = await scenario()
    prior = context.question_history[0]
    prior.question.text, prior.answer.text = PREVIOUS, ANSWER
    target.text = text
    target.information_goal = "Provide actual measured ROI results"
    disputed = QuestionQualityReview(
        issues=[
            {
                "code": "SEMANTIC_REPEAT",
                "instruction": "Ask only a new unanswered detail",
                "question_quote": text,
                "repair_action": "narrow_unanswered_request",
                "comparison_question_id": prior.question.question_id,
                "comparison_question_quote": PREVIOUS,
                "comparison_answer_id": prior.answer.answer_id,
                "comparison_answer_quote": ANSWER,
                "repeat_relation": "already_answered",
                "actual_request": "Experiment setup and measurement metrics",
            }
        ]
    )
    return context, target, disputed


@pytest.mark.asyncio
@pytest.mark.parametrize("detail_quote", ["", "测量指标", "实际测得的GPU峰值显存下降数值"])
async def test_missing_results_cannot_authorize_unchanged_setup_question(detail_quote):
    context, target, disputed = await roi_case()
    gate = QuestionQualityGate(ROIReview(detail_quote=detail_quote), load_agent_settings())
    review = await gate._adjudicate_repeat(
        gate.payload(target, context), disputed.model_dump(), target, None, None
    )
    assert [issue.code for issue in review.issues] == ["SEMANTIC_REPEAT"]
    assert review.issues[0].repair_action == "narrow_unanswered_request"
    assert "实际测得的GPU峰值显存下降数值" in review.issues[0].instruction


@pytest.mark.asyncio
async def test_explicit_unanswered_result_is_a_valid_new_request():
    context, target, disputed = await roi_case(RESULT_REQUEST)
    gate = QuestionQualityGate(
        ROIReview(RESULT_REQUEST, "实际测得的GPU峰值显存下降数值"), load_agent_settings()
    )
    review = await gate._adjudicate_repeat(
        gate.payload(target, context), disputed.model_dump(), target, None, None
    )
    assert not review.issues


@pytest.mark.asyncio
async def test_draft_mixing_answered_setup_and_new_result_still_requires_rewrite():
    text = RESULT_REQUEST + "并说明对比实验设置。"
    context, target, disputed = await roi_case(text)
    gate = QuestionQualityGate(
        ROIReview(text, "实际测得的GPU峰值显存下降数值", mixed=True), load_agent_settings()
    )
    review = await gate._adjudicate_repeat(
        gate.payload(target, context), disputed.model_dump(), target, None, None
    )
    assert review.issues[0].code == "SEMANTIC_REPEAT"


@pytest.mark.asyncio
async def test_repeat_repair_path_rewrites_draft_and_keeps_the_confirmed_intent():
    context, target, disputed = await roi_case()

    class RepairingModel:
        generations = 0

        async def generate_structured(self, *, prompt_name, payload, response_model):
            if response_model is RepeatAdjudication:
                return await ROIReview().generate_structured(
                    prompt_name=prompt_name, payload=payload, response_model=response_model
                )
            if response_model is QuestionQualityReview:
                return (
                    disputed
                    if payload["candidate_question"] == REPEAT
                    else response_model(issues=[])
                )
            self.generations += 1
            return response_model.model_validate(
                decision(text=REPEAT if self.generations == 1 else RESULT_REQUEST)
            )

    model = RepairingModel()
    target.text = None
    generated = await ReactQuestionAgent(model, load_agent_settings()).generate(
        target, "", interview=context
    )
    assert generated.question.text == RESULT_REQUEST
    assert generated.question.information_goal == target.information_goal
    assert generated.repaired and model.generations == 2


@pytest.mark.asyncio
async def test_internship_role_and_company_are_citable_sources_linked_to_claims():
    _, context, target = await scenario()
    project = context.candidate_profile.projects[0]
    project.name = "Baidu (Shenzhen) | NLP Algorithm Intern"
    project.domain = "NLP"
    project.claims[0].text = "Applied K-Means to sparse behavioral records to identify bad cases."
    target.text = "在百度深圳的NLP算法实习中，你如何使用K-Means筛选坏例？"
    model = AsyncMock()
    gate = QuestionQualityGate(model, load_agent_settings())
    payload = gate.payload(target, context)
    name = next(s for s in payload["grounding_sources"] if s["text"] == project.name)
    claim = next(s for s in payload["grounding_sources"] if "K-Means" in s["text"])
    assert name["project_id"] == claim["project_id"] == project.project_id
    model.generate_structured.return_value = GroundingAdjudication(
        checks=[
            {
                "issue_index": 0,
                "verdict": "refuted",
                "relation": "established_context",
                "premise_basis": "established_fact",
                "request_quote": "百度深圳的NLP算法实习",
                "sources": [
                    {"source_id": name["source_id"], "quote": name["text"]},
                    {"source_id": claim["source_id"], "quote": claim["text"]},
                ],
                "reason": "The project title establishes the company and role for this claim.",
            }
        ]
    )
    review = await gate._adjudicate_grounding(
        payload,
        {
            "issues": [
                {
                    "code": "UNSUPPORTED_PREMISE",
                    "question_quote": target.text,
                    "instruction": "Check the company and internship role",
                    "repair_action": "remove_unfounded_premise",
                }
            ]
        },
        target,
        None,
        None,
    )
    assert not review.issues
    sent = model.generate_structured.call_args.kwargs["payload"]
    assert sent["project_context"]["name"] == project.name
    assert name in sent["sources"]
