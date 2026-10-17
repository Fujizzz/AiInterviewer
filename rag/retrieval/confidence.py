"""Confidence / abstention signals for a retrieval call.

Why: the retriever always returns k passages, even for "sourdough". Callers (agents) must be able to
tell "found evidence" from "nothing relevant in the corpus". Dense cosine alone cannot do it:
off-topic
queries score 0.41-0.60 while a hard in-corpus paraphrase scores 0.53, so the rule combines signals.

Signals (all computed per call, see RetrievalResult):
  max_dense     top dense cosine over the dense candidate list (None if dense did not run)
  max_rerank    best cross-encoder score in the reranked pool (None unless rerank=True)
  lexical_hit   True if some chunk contains ALL informative query terms (strict AND, after dropping
                stop words), or contains a query identifier verbatim. Only ASCII word/identifier
                tokens
                are indexed for lexical search, so a pure non-ASCII (e.g. Chinese) query always has
                lexical_hit=False -- dense results only.
  informative   number of query terms left after stop-word removal (+ identifiers)

Rule (thresholds calibrated on the 40 draft gold queries + data/eval/negative_queries.yaml, see
README):
  LOW     no results; or an ASCII query with no informative terms ("how does it work", "???");
          or max_dense < DENSE_LOW (0.52); or (rerank ran and max_rerank < RERANK_LOW (0.30))
  HIGH    lexical_hit and max_dense >= DENSE_HIGH (0.62) [and, if rerank ran, max_rerank >=
  RERANK_HIGH (0.80)]
  MEDIUM  everything else (usable, but corroborate / expect partial answers)
  Lexical-only mode (no dense score): MEDIUM if lexical_hit else LOW.

`low_confidence` == (confidence == "low"): agents must treat it as "no evidence", not as a weak
answer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceRule:
    dense_low: float = 0.52
    dense_high: float = 0.62
    rerank_low: float = 0.30
    rerank_high: float = 0.80


DEFAULT_RULE = ConfidenceRule()


def assess(
    n_passages: int,
    max_dense: float | None,
    max_rerank: float | None,
    lexical_hit: bool,
    informative_terms: int,
    ascii_query: bool,
    rule: ConfidenceRule = DEFAULT_RULE,
) -> tuple[str, list[str]]:
    """Return (level, reasons)."""
    if n_passages == 0:
        return "low", ["no passages returned"]
    if ascii_query and informative_terms == 0:
        return "low", ["query has no informative terms (only stop words / punctuation)"]
    if max_dense is None:  # lexical-only mode
        return (
            ("medium", ["lexical-only: lexical_hit"])
            if lexical_hit
            else ("low", ["lexical-only: no strict/identifier match"])
        )
    reasons: list[str] = []
    if max_dense < rule.dense_low:
        reasons.append(f"max_dense {max_dense:.3f} < {rule.dense_low}")
    if max_rerank is not None and max_rerank < rule.rerank_low:
        reasons.append(f"max_rerank {max_rerank:.3f} < {rule.rerank_low}")
    if reasons:
        return "low", reasons
    if (
        lexical_hit
        and max_dense >= rule.dense_high
        and (max_rerank is None or max_rerank >= rule.rerank_high)
    ):
        return "high", [
            "lexical match and strong dense" + (" and rerank" if max_rerank is not None else "")
        ]
    why = []
    if not lexical_hit:
        why.append(
            "no lexical corroboration"
            + ("" if ascii_query else " (non-ASCII query: lexical search not applicable)")
        )
    if max_dense < rule.dense_high:
        why.append(f"max_dense {max_dense:.3f} < {rule.dense_high}")
    if max_rerank is not None and max_rerank < rule.rerank_high:
        why.append(f"max_rerank {max_rerank:.3f} < {rule.rerank_high}")
    return "medium", why


class RetrievalResult(list):
    """list[Passage] (fully backward compatible) plus confidence signals.

    Agents MUST check `low_confidence` and treat True as "no evidence found" -- do not answer from
    the
    passages, say that the knowledge base has nothing reliable.
    """

    max_dense: float | None = None
    max_rerank: float | None = None
    lexical_hit: bool = False
    confidence: str = "low"
    low_confidence: bool = True
    reasons: list[str]
    encoder: str | None = None  # query-encoder trace label, e.g. "torch" or "onnx-int8/int8-pc"

    def __init__(
        self,
        passages=(),
        *,
        max_dense=None,
        max_rerank=None,
        lexical_hit=False,
        confidence="low",
        reasons=None,
        encoder=None,
    ):
        super().__init__(passages)
        self.encoder = encoder
        self.max_dense = max_dense
        self.max_rerank = max_rerank
        self.lexical_hit = lexical_hit
        self.confidence = confidence
        self.low_confidence = confidence == "low"
        self.reasons = list(reasons or [])

    def _like(self, items) -> RetrievalResult:
        """New result over `items` that keeps this result's confidence signals."""
        return RetrievalResult(
            items,
            max_dense=self.max_dense,
            max_rerank=self.max_rerank,
            lexical_hit=self.lexical_hit,
            confidence=self.confidence,
            reasons=self.reasons,
            encoder=self.encoder,
        )

    def __getitem__(self, i):
        out = super().__getitem__(i)
        return self._like(out) if isinstance(i, slice) else out  # res[:3] keeps low_confidence

    def copy(self) -> RetrievalResult:
        return self._like(list(self))

    def __add__(self, other):
        return self._like(list(self) + list(other))  # keeps the LEFT operand's signals

    def signals(self) -> dict:
        return {
            "max_dense": self.max_dense,
            "max_rerank": self.max_rerank,
            "lexical_hit": self.lexical_hit,
            "confidence": self.confidence,
            "low_confidence": self.low_confidence,
            "reasons": self.reasons,
            "encoder": self.encoder,
        }
