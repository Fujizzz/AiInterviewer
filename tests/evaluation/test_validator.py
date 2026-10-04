import pytest

from evaluation.contracts import QuoteSpan
from evaluation.validator import normalize_claim, validate_evidence
from tests.evaluation.resolver_helpers import source


def test_normalization_preserves_semantics_and_raw_evidence():
    assert normalize_claim("  Cafe\u0301\n  latency  10ms. ") == "Café latency 10ms."
    for left, right in [
        ("I did fix it.", "I did not fix it."),
        ("10 ms", "1.0 ms"),
        ("I used C.", "I used c."),
        ("C++ implementation", "C implementation"),
    ]:
        assert normalize_claim(left) != normalize_claim(right)
    item = source("a", "序🙂 我排查\n 日志。", claim="  我排查\n  日志。")
    validated = validate_evidence(item.evidence, item.turn, interview_id="interview")
    assert validated == item.evidence
    assert validated.quote_spans[0].quote == item.turn.answer.text


@pytest.mark.parametrize("claim", ["", "  ", "...", "🙂"])
def test_normalization_rejects_empty_claim(claim):
    with pytest.raises(ValueError):
        normalize_claim(claim)


@pytest.mark.parametrize(
    "problem",
    [
        "quote",
        "offset",
        "overlap",
        "answer",
        "question",
        "thread",
        "project",
        "interview",
        "label",
        "unknown",
        "refusal",
        "joined_non_answers",
        "meaningless_claim",
        "label_claim",
    ],
)
def test_hard_validation_rejects_bypassed_models(problem):
    item = source("a")
    evidence, turn, interview_id = item.evidence, item.turn, "interview"
    if problem in {"quote", "offset", "overlap"}:
        span = evidence.quote_spans[0]
        if problem == "quote":
            span = QuoteSpan(
                quote=turn.question.text, char_start=0, char_end=len(turn.question.text)
            )
        elif problem == "offset":
            span = span.model_copy(update={"char_start": 1, "char_end": span.char_end + 1})
        evidence = evidence.model_copy(
            update={
                "quote_spans": (span, span) if problem == "overlap" else (span,),
            }
        )
    elif problem in {"answer", "question", "thread", "project"}:
        evidence = evidence.model_copy(update={f"{problem}_id": "other"})
    elif problem == "interview":
        interview_id = "foreign-interview"
    elif problem in {"meaningless_claim", "label_claim"}:
        evidence = evidence.model_copy(
            update={
                "normalized_claim": "..." if problem == "meaningless_claim" else "CNN",
            }
        )
    else:
        text = {
            "label": "CNN",
            "unknown": "I don't know",
            "refusal": "拒绝回答",
            "joined_non_answers": "yes. OK",
        }[problem]
        item = source("b", text)
        turn, evidence = item.turn, item.evidence
        if problem == "joined_non_answers":
            evidence = evidence.model_copy(
                update={
                    "normalized_claim": "I investigated the cache.",
                    "quote_spans": (
                        QuoteSpan(quote="yes", char_start=0, char_end=3),
                        QuoteSpan(quote="OK", char_start=5, char_end=7),
                    ),
                }
            )
    with pytest.raises(ValueError):
        validate_evidence(evidence, turn, interview_id=interview_id)
