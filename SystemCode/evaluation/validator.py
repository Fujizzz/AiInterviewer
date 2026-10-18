"""Hard provenance gates shared by extraction and evidence resolution."""

import re
import unicodedata

from evaluation.analyzer import is_bare_label, non_answer_status
from evaluation.contracts import EvidenceItem
from evaluation.inputs import ThreadTurn

CLAIM_NORMALIZATION_VERSION = "claim-nfc-whitespace-1"


def normalize_claim(claim: str) -> str:
    """A comparison key, never a replacement for a quote or the v1 identity input.

    Preserve case, punctuation, numbers and negation: C != c and 10 != 1.0.
    Semantic equivalence is the resolver's task, not a fuzzy string threshold.
    """
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", claim)).strip()
    if not normalized or not any(character.isalnum() for character in normalized):
        raise ValueError("a claim must contain meaningful text")
    return normalized


def validate_evidence(item: EvidenceItem, turn: ThreadTurn, *, interview_id: str) -> EvidenceItem:
    """Revalidate even model_construct/copy values; never repair source text."""
    item = EvidenceItem.model_validate(item.model_dump())
    turn = ThreadTurn.model_validate(turn.model_dump())
    if turn.answer.interview_id != interview_id:
        raise ValueError("evidence must belong to the current interview")
    if (item.thread_id, item.project_id) != (turn.question.thread_id, turn.question.project_id):
        raise ValueError("evidence thread/project must match its immutable question")
    item.validate_answer(turn.answer.as_candidate_answer())
    for text in (
        turn.answer.text,
        " ".join(span.quote for span in item.quote_spans),
        item.normalized_claim,
    ):
        if non_answer_status(text) or is_bare_label(text):
            raise ValueError("a non-answer or bare label cannot support a claim")
    if all(non_answer_status(span.quote) or is_bare_label(span.quote) for span in item.quote_spans):
        raise ValueError("non-answer fragments cannot be combined into evidence")
    normalize_claim(item.normalized_claim)
    return item
