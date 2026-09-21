"""Offline scenario through the production CLI application, policy and ReAct loop.

Run: python -m docs.examples.demo_dialogue_react
Only the model outputs and candidate answers are scripted. No network or API key.
"""

from pathlib import Path

from agents.question.react import QuestionAgentDecision
from agents.tracing import emit_trace
from app.adapters.evaluation import AnswerEvidence
from app.application import build_application
from app.cli import run_interview
from app.parsing.resume import ResumeExtraction
from app.reporting.final_report import ReportNarrative
from app.tracing import FileTrace

ANSWERS = [
    "Transformer temporal modeling",
    "我实现了时间戳重采样和偏移校正；Transformer 用的是已有模型。",
    "是的，模型权重没有修改。我负责重采样、偏移校正和接入管线。",
    "我保留相同测试片段，对比校正前后的人工标注偏移。",
    "以未校正结果为基线，平均偏移从 120 ms 降到 40 ms，使用同一批测试片段。",
]
QUESTIONS = [
    "口型同步项目中，你亲自实现了哪一部分？",
    "你具体对这个时序建模模块做了什么修改？",
    "你先提到 Transformer，随后描述时间戳处理；你自己的实现是已有模型外围的对齐代码吗？",
    "你如何验证时间戳校正改善了口型同步？",
    "你比较的是哪个基线，观察到怎样的偏移变化？",
]


class ScriptedModel:
    """Scripts model choices; production validates and executes them."""

    def __call__(self, prompt, data, schema):
        # Scripted semantic pass; this fixture does not evaluate question quality.
        if schema.__name__ == "QuestionQualityReview":
            return schema(issues=[])
        if schema is ResumeExtraction:
            return schema(
                candidate_name="离线示例候选人",
                skills=["Python", "Transformer"],
                projects=[
                    dict(
                        name="口型同步系统",
                        domain="音视频",
                        description="实现时间戳对齐并验证同步效果",
                        technologies=["Transformer"],
                        claims=["实现音视频时间戳对齐", "验证时间戳校正效果"],
                        metrics=[],
                    )
                ],
            )
        if schema is QuestionAgentDecision:
            index = data["state_summary"]["question_index"]
            view = data["dialogue_state"]
            project = view["projects"][0]
            if index == 0 and not data["observations"]:
                return schema(
                    action="get_project",
                    project_id=project["project_id"],
                    topic=None,
                    limit=None,
                    text=None,
                )
            if index == 2:
                if not data["observations"]:
                    return schema(action="get_history", topic=None, limit=2, text=None)
                if len(data["observations"]) == 1:
                    assert len(data["observations"][0]["result"]["entries"]) == 2
                    assert "dimensions" not in str(data["observations"])
                    return schema(action="get_plan", topic=None, limit=None, text=None)
            action = ["new_topic", "clarify", "clarify", "new_topic", "probe"][index]
            topic_key = (
                view["active_thread"]["topic_key"]
                if action in {"clarify", "probe"}
                else project["topics"][0]["topic_key"]
            )
            goals = [
                "说明个人实现范围",
                "具体修改了什么模块",
                "确认模型复用与个人贡献边界",
                "说明效果验证方法",
                "说明基线和偏移变化",
            ]
            summaries = [
                "依据项目资料确认个人实现",
                "回答只有模块名称，具体修改仍不清楚",
                "两轮回答的贡献边界需要核对",
                "贡献边界已澄清，转向验证话题",
                "已有验证方法，继续询问具体结果",
            ]
            return schema(
                action="final",
                topic=None,
                limit=None,
                text=QUESTIONS[index],
                selection=dict(
                    dialogue_action=action,
                    project_id=project["project_id"],
                    topic_key=topic_key,
                    information_goal=goals[index],
                    decision_summary=summaries[index],
                ),
            )
        if schema is AnswerEvidence:
            index = ANSWERS.index(data["answer"])
            analysis = dict(
                status="substantive",
                new_information=True,
                thread_complete=index in (2, 4),
                answer_scope="label_only" if index == 0 else "concrete",
                missing_information=[],
                contradictions=[],
                summary="补充了具体事实",
            )
            dimensions = []
            if index == 0:
                analysis.update(
                    status="partial",
                    missing_information=["具体修改了什么"],
                    summary="只给出模块名称，没有说明个人实现",
                )
            if index == 1:
                analysis.update(
                    contradictions=["先说 Transformer 时序建模，后说复用模型；需核对贡献边界"],
                    summary="描述了时间戳处理，需要澄清与模型实现的边界",
                )
            if index == 3:
                analysis["missing_information"] = ["具体基线和偏移测量结果"]
            if index > 0:
                names = ["ownership"] if index < 3 else ["evaluation", "technical_depth"]
                dimensions = [
                    dict(
                        competency=name,
                        observation="supported",
                        quote=data["answer"],
                        fact="个人负责时间戳处理" if index < 3 else "在同一批片段测量校正前后偏移",
                        rationale="回答中提供了对应的具体行为",
                        rubric_level=3,
                        strength=0.7 if index in (1, 3) else 0.8,
                    )
                    for name in names
                ]
            return schema(
                answer_relevance=0.9 if index else 0.3,
                evidence_strength=0.8 if dimensions else 0,
                analysis=analysis,
                dimensions=dimensions,
            )
        if schema is ReportNarrative:
            return schema(
                strengths=["说明了个人贡献边界和同样本前后对比方法。"],
                weaknesses=["其他能力尚无充分证据；该输出来自预设离线场景。"],
            )
        raise AssertionError(schema)


def main():
    root = Path(__file__).resolve().parents[2]
    answers = iter(ANSWERS)
    with FileTrace(root / "output") as trace:
        emit_trace("simulation.started")
        result = run_interview(
            build_application(ScriptedModel()),
            "口型同步项目：实现音视频时间戳对齐；验证时间戳校正效果。",
            max_questions=5,
            max_questions_per_project=5,
            max_follow_up_per_topic=2,
            job_title="AI Engineer",
            read_answer=lambda _: next(answers),
            write=lambda _: None,
        )
        trace.save_result(result)
    actions = [entry["dialogue_action"] for entry in result["question_history"]]
    assert actions == ["new_topic", "clarify", "clarify", "new_topic", "probe"], actions
    assert result["decision_logs"][2]["question_agent_stop_reason"] == "FINAL"
    assert [step["action"] for step in result["decision_logs"][2]["question_agent_steps"]] == [
        "get_history",
        "get_plan",
        "final",
    ]
    print("OFFLINE SIMULATION: " + " -> ".join(actions))
    print(trace.path)


if __name__ == "__main__":
    main()
