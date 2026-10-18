"""Bounded evidence-gap questions with no model requests or rubric disclosure."""

from uuid import uuid4

from agents.policies.dialogue_controller import DialogueController
from shared.contracts import PlannedQuestion, QuestionType

PROMPTS = {
    "ownership.delivery": (
        "Describe a delivery that was blocked: what did you personally do to "
        "break down tasks, coordinate dependencies, communicate risks, and "
        "verify completion?",
        "请举一个交付受阻的真实例子，说明你如何拆解任务、协调依赖、沟通风险，并确认交付完成？",
    ),
    "ownership.accountability": (
        "Describe a problem caused by one of your own decisions: what impact did "
        "it have, what did you personally change, and how did you verify the "
        "recovery?",
        "请举一次你个人决策引发问题的真实例子，说明影响、你采取的补救行动，以及如何验证影响已消除？",
    ),
    "adaptability.transfer": (
        "Describe how you applied something learned in one project to a "
        "different situation, what you changed, and how you checked that it "
        "worked?",
        "请举一个把已有经验用于不同场景的真实例子，说明你做了哪些调整，以及如何验证有效？",
    ),
    "debugging.prevention": (
        "After fixing a real failure, what did you change to prevent recurrence, "
        "and what evidence showed that the prevention worked?",
        "请举一次真实故障修复后的例子，说明你采取了什么措施防止再次发生，以及如何验证这些措施有效？",
    ),
}


def coverage_question(context, aggregation, settings):
    if aggregation is None or aggregation.snapshot.status == "evaluation_failed":
        return None
    snapshot = aggregation.snapshot
    if snapshot.overall_score_publishable:
        return None
    asked = context.coverage_question_criteria
    if len(asked) >= settings.scoring.max_supplemental_questions:
        return None
    controller = DialogueController(context, settings)
    projects = [
        p
        for p in context.candidate_profile.projects
        if not controller.project_exhausted(p.project_id)
    ]
    if context.candidate_profile.projects and not projects:
        return None
    project = next(
        (p for p in projects if p.project_id == context.state.active_project_id),
        projects[0] if projects else None,
    )
    if controller.project_exhausted(project.project_id if project else None):
        return None
    importance = {x.competency: x.weight for x in aggregation.inputs.profile.competencies}
    ordered = sorted(
        snapshot.competencies,
        key=lambda c: (c.status == "published", -importance.get(c.competency, 0)),
    )
    chinese = bool(
        context.question_history
        and any(
            "\u4e00" <= x <= "\u9fff" for x in (context.question_history[-1].question.text or "")
        )
    )
    for competency in ordered:
        if importance.get(competency.competency, 0) <= 0:
            continue
        for criterion in competency.criteria:
            key = criterion.criterion_id
            if criterion.status == "published" or key in asked:
                continue
            rubric = aggregation.inputs.rubric.for_competency(competency.competency)
            definition = next(c for c in rubric.criteria if c.criterion_id == key)
            prompts = PROMPTS.get(
                key,
                (
                    f"Describe a concrete example of your "
                    f"{key.rsplit('.', 1)[-1].replace('_', ' ')}: "
                    "what was the situation, what did you personally do, "
                    "and how did you verify the outcome?",
                    f"请举一个与{definition.name}有关的真实例子，说明具体情境、你的个人行动，以及如何核实结果？",
                ),
            )
            text = prompts[int(chinese)]
            if project:
                text = (
                    (f"结合你在{project.name}中的经历，" + text)
                    if chinese
                    else f"Thinking about {project.name}: {text}"
                )
            identifier = str(uuid4())
            question = PlannedQuestion(
                question_id=identifier,
                project_id=project.project_id if project else None,
                topic="补充经历" if chinese else "Additional experience",
                topic_key=f"coverage:{key}",
                text=text,
                difficulty=settings.initial_question_difficulty,
                probe_depth=1,
                question_type=QuestionType.DESCRIPTION,
                intent=text,
                information_goal=text,
                thread_id=identifier,
                dialogue_action="new_topic",
            )
            return key, question
    return None
