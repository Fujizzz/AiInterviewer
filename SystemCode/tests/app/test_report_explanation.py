"""Public explanations must refer only to effective, score-producing source text."""

from types import SimpleNamespace

import pytest

from app.reporting.explanation import build_explanation
from app.reporting.final_report import build_final_report
from evaluation.aggregator import aggregate_scores
from evaluation.publication import formal_publication
from tests.evaluation.resolver_helpers import assessment, decision, resolve, source
from tests.evaluation.scoring_helpers import scoring_input
from tests.evaluation.test_publication import example


def explain(context, record):
    publication = formal_publication(context, [record])
    return build_explanation(context, [record], publication)


def replace_aggregation(record, resolution, *assessments):
    record.scored.aggregation = aggregate_scores(scoring_input(resolution, *assessments))


def test_selected_sources_are_exact_and_private_scoring_instructions_stay_internal():
    context, record = example(complete=True)
    item = record.scored.aggregation.inputs.history.sources[0]
    context.question_history = [SimpleNamespace(answer=item.turn.answer)]
    original = record.scored.aggregation.model_dump_json()
    result = explain(context, record)
    for dimension in result.competencies.values():
        assert dimension.selected_evidence_count == 1
        evidence = dimension.examples[0]
        assert evidence.quotes == [item.turn.answer.text]
        assert evidence.round_number == 1
        assert evidence.question == item.turn.question.text
        assert evidence.levels == [4]
    text = result.model_dump_json()
    for private in (
        "matched_anchor",
        "criterion_id",
        "rubric_version",
        "effective_weight",
        "concise_rationale",
    ):
        assert private not in text
    assert record.scored.aggregation.model_dump_json() == original


def test_duplicate_sources_are_not_presented_as_independent_scoring_evidence():
    context, record = example()
    first, duplicate = source("a"), source("b")
    replace_aggregation(
        record,
        resolve((first, decision(first)), (duplicate, decision(duplicate, "duplicate", (first,)))),
        assessment("first", first, duplicate),
    )
    detail = explain(context, record).competencies["debugging"]
    assert detail.selected_evidence_count == 1
    assert len(detail.examples) == 1
    selected = {w.evidence_id for w in record.scored.aggregation.weights if w.selected}
    assert {
        s.evidence.answer_id
        for s in record.scored.aggregation.inputs.history.sources
        if s.evidence.evidence_id in selected
    } == {detail.examples[0].answer_id}


def test_unresolved_conflicts_do_not_publish_positive_excerpts():
    context, record = example()
    first, counter = source("a"), source("b", "I never profiled the incident.")
    replace_aggregation(
        record,
        resolve((first, decision(first)), (counter, decision(counter, "contradicts", (first,)))),
        assessment("conflict", first, counter),
    )
    assert formal_publication(context, [record])["overall_score"] is None
    assert not any(c.examples for c in explain(context, record).competencies.values())


def test_failed_turn_keeps_only_supported_partial_explanations():
    context, record = example(complete=True)
    context.processed_feedback_ids.append("failed-turn")
    record.scored.aggregation = aggregate_scores(
        record.scored.aggregation.inputs.model_copy(
            update={"evaluation_failure_codes": ("unassessed_feedback:failed-turn",)}
        )
    )
    result = explain(context, record)
    assert result.answer_count == 2
    assert all(len(c.examples) == 1 for c in result.competencies.values())
    assert all("evaluation_incomplete" in c.limitation_codes for c in result.competencies.values())


def test_excerpt_count_is_bounded_and_omissions_are_explicit():
    context, record = example()
    sources = [source(str(i), f"I used profiler {i} to locate contention.") for i in range(6)]
    replace_aggregation(
        record,
        resolve(*[(s, decision(s)) for s in sources]),
        *[assessment(str(i), s) for i, s in enumerate(sources)],
    )
    context.question_history = [SimpleNamespace(answer=s.turn.answer) for s in sources]
    detail = explain(context, record).competencies["debugging"]
    assert [e.round_number for e in detail.examples] == [1, 2, 3]
    assert detail.selected_evidence_count == 6
    assert len(detail.examples) == 3
    assert detail.omitted_evidence_count == 3


def test_corrupt_imported_source_is_not_quoted():
    context, record = example()
    aggregation = record.scored.aggregation
    item = aggregation.inputs.history.sources[0]
    bad = item.model_copy(
        update={
            "turn": item.turn.model_copy(
                update={"answer": item.turn.answer.model_copy(update={"text": "unrelated"})}
            )
        }
    )
    record.scored.aggregation = aggregation.model_copy(
        update={
            "inputs": aggregation.inputs.model_copy(
                update={
                    "history": aggregation.inputs.history.model_copy(update={"sources": (bad,)})
                }
            )
        }
    )
    detail = explain(context, record).competencies["ownership"]
    assert detail.source_unavailable_count == 1
    assert detail.examples == []


@pytest.mark.asyncio
async def test_final_report_includes_explanations_without_a_model_call():
    context, record = example(complete=True)
    context.question_history = []
    context.evidence_records = []

    def forbidden(*args):
        raise AssertionError("The report must not request a narrative model")

    result = await build_final_report(context, [], formal_records=[record], llm=forbidden)
    assert result.explanation.competencies["ownership"].examples
    assert result.overall_score == 4
    assert result.score_status == "published"
    empty = await build_final_report(context, [], formal_records=[], llm=forbidden)
    assert empty.overall_score is None
    assert empty.explanation.competencies == {}
