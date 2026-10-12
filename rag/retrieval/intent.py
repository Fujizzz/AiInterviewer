"""Structured retrieval request (proposal §5.4-5.5)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

StrOrList = str | Sequence[str] | None


def clean_text(v: str | None) -> str | None:
    """Drop NUL bytes: PostgreSQL text parameters cannot contain them (psycopg raises otherwise)."""
    return v.replace("\x00", "") if isinstance(v, str) else v


def _as_list(v: StrOrList) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [clean_text(v)]
    return [clean_text(str(x)) for x in v]


@dataclass
class Filters:
    """Metadata filters. Empty list = no constraint. Values within one field are OR-ed;
    fields are AND-ed. `related_technologies` matches rows sharing >= 1 tag (lower-cased)."""

    domain: list[str] = field(default_factory=list)
    source_name: list[str] = field(default_factory=list)
    related_technologies: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.domain = _as_list(self.domain)
        self.source_name = _as_list(self.source_name)
        self.related_technologies = [t.lower() for t in _as_list(self.related_technologies)]


@dataclass
class RetrievalIntent:
    """What the calling agent wants to know -- not how to search for it.

    query                 natural-language / keyword query (required)
    namespaces            optional narrowing; must be within the role's allowed set
    target_competency     competency being probed/scored (BARS). Applied to rows that carry a
                          competency (see strict_attrs); unlabeled rows are not excluded
    difficulty            wanted difficulty, e.g. "Easy|Medium|Hard"; same semantics as competency
    purpose               free-text tag for traceability, e.g. "question_generation",
                          "answer_evaluation", "followup_probe", "claim_verification"
    candidate_claim       something the candidate asserted; if given it is appended to the *dense*
                          query text (lexical search stays on `query` -- claims are long and noisy)
    filters               domain / source_name / related_technologies
    k                     passages to return
    strict_attrs          True => difficulty/competency become hard equality filters
                          (rows without the attribute are excluded)
    candidate_id          owner scope for the `candidate` namespace. Candidate chunks are returned
                          ONLY when their candidate_id equals this value; when it is missing or
                          blank the `candidate` namespace is dropped (fail closed). It has no effect
                          on other namespaces.
    """

    query: str
    namespaces: Sequence[str] | None = None
    target_competency: str | None = None
    difficulty: str | None = None
    purpose: str | None = None
    candidate_claim: str | None = None
    filters: Filters = field(default_factory=Filters)
    k: int = 5
    strict_attrs: bool = False
    candidate_id: str | None = None

    def __post_init__(self) -> None:
        self.query = clean_text(self.query)
        for f in ("target_competency", "difficulty", "purpose", "candidate_claim", "candidate_id"):
            setattr(self, f, clean_text(getattr(self, f)))
        if self.namespaces is not None:
            self.namespaces = [
                clean_text(n)
                for n in (
                    [self.namespaces] if isinstance(self.namespaces, str) else self.namespaces
                )
            ]
        if self.candidate_id is not None:
            self.candidate_id = self.candidate_id.strip() or None
        if not self.query or not self.query.strip():
            raise ValueError("RetrievalIntent.query must be non-empty")
        if not (1 <= self.k <= 100):
            raise ValueError("k must be in 1..100")
        if self.namespaces is not None:
            self.namespaces = tuple(self.namespaces)
        if isinstance(self.filters, dict):
            self.filters = Filters(**self.filters)

    def dense_query_text(self) -> str:
        if self.candidate_claim and self.candidate_claim.strip():
            return f"{self.query.strip()}\n{self.candidate_claim.strip()}"
        return self.query.strip()
