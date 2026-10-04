from evaluation.contracts import CriterionAssessment, EvidenceItem, QuoteSpan
from evaluation.ids import evidence_id
from evaluation.inputs import AnswerSnapshot, EvaluationInput, QuestionSnapshot, ThreadTurn
from evaluation.resolution import EvidenceSource, RelationDecision, ResolutionHistory
from evaluation.resolver import replay_resolution


def source(
    key,
    text="I used a profiler to identify lock contention.",
    *,
    claim=None,
    project="cache",
    thread="thread-cache",
    specificity="concrete",
    ownership="personal",
):
    turn = ThreadTurn(
        question=QuestionSnapshot(
            question_id=f"q-{key}",
            thread_id=thread,
            project_id=project,
            topic_key="diagnosis",
            text="What did you do?",
            information_goal="Personal diagnosis",
        ),
        answer=AnswerSnapshot(
            interview_id="interview",
            answer_id=f"a-{key}",
            question_id=f"q-{key}",
            text=text,
        ),
    )
    claim = claim or text
    spans = (QuoteSpan(quote=text, char_start=0, char_end=len(text)),)
    item = EvidenceItem(
        evidence_id=evidence_id(
            answer_id=turn.answer.answer_id,
            quote_spans=spans,
            normalized_claim=claim,
            evidence_kind="personal_action",
        ),
        answer_id=turn.answer.answer_id,
        question_id=turn.question.question_id,
        thread_id=thread,
        project_id=project,
        quote_spans=spans,
        normalized_claim=claim,
        evidence_kind="personal_action",
        ownership_scope=ownership,
        specificity=specificity,
        factuality="reported_experience",
        extraction_version="test-v1",
    )
    return EvidenceSource(evidence=item, turn=turn)


def decision(item, relation="new", targets=(), *, episode=None, same=(), proof=None):
    targets = tuple(s.evidence.evidence_id for s in targets)
    same = tuple(s.evidence.evidence_id for s in same)
    if episode is None:
        episode = "new_episode" if relation in {"new", "supports"} and not same else "same_episode"
    if episode == "same_episode":
        same = tuple(sorted(set(same) | set(targets)))
    return RelationDecision(
        evidence_id=item.evidence.evidence_id,
        relation=relation,
        related_evidence_ids=targets,
        independence=episode,
        same_episode_as=same,
        retraction_span=proof,
        concise_rationale="The quoted statements describe this relation.",
    )


def resolve(*pairs):
    return replay_resolution(
        ResolutionHistory(
            interview_id="interview",
            sources=tuple(p[0] for p in pairs),
            decisions=tuple(p[1] for p in pairs),
        )
    )


def context(item):
    return EvaluationInput(
        request_id="request",
        interview_id="interview",
        question=item.turn.question,
        answer=item.turn.answer,
    )


def assessment(key, *sources, criterion="debugging.diagnostic_method", level=3):
    return CriterionAssessment(
        assessment_id=key,
        competency=criterion.split(".")[0],
        criterion_id=criterion,
        rubric_version="1.0.0",
        evidence_ids=tuple(s.evidence.evidence_id for s in sources),
        assigned_level=level,
        matched_anchor_ids=(f"{criterion}.l{level}",),
        decision="included",
        reason_codes=("matched_behavior",),
        concise_rationale="Concrete diagnostic behavior.",
    )
