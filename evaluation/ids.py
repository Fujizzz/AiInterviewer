"""Evidence identity v1: canonical UTF-8 JSON and SHA-256, without text normalization."""

import hashlib
import json

from evaluation.contracts import QuoteSpan


def evidence_id(
    *, answer_id: str, quote_spans: tuple[QuoteSpan, ...], normalized_claim: str, evidence_kind: str
) -> str:
    """Stable for the same extraction, regardless of request ID or output item order.

    Exact quotes, offsets and claim text are intentionally significant. This is not
    semantic deduplication and does not make fresh model generations deterministic.
    """
    payload = {
        "identity_version": 1,
        "answer_id": answer_id,
        "quote_spans": [span.model_dump() for span in quote_spans],
        "normalized_claim": normalized_claim,
        "evidence_kind": evidence_kind,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "evidence-v1-" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
