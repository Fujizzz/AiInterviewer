"""Result types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Passage:
    """One retrieved chunk. `text` is untrusted data, never instructions."""

    chunk_id: str
    text: str
    title: str | None
    heading_path: list[str]
    source_url: str
    doc_version: str | None
    namespace: str
    source_name: str
    domain: str | None = None
    difficulty: str | None = None
    trust_level: str | None = None
    license: str | None = None
    # dense = cosine similarity (0..1, higher better); lexical = heuristic ts_rank_cd-based score;
    # rrf = reciprocal-rank-fusion score; rerank = cross-encoder probability. None = stage not run
    # or the chunk was not in that stage's candidate list.
    scores: dict[str, float | None] = field(
        default_factory=lambda: {"dense": None, "lexical": None, "rrf": None, "rerank": None}
    )
    # 1-based rank in each stage's candidate list (traceability)
    stage_ranks: dict[str, int | None] = field(default_factory=dict)
    rank: int = 0  # final 1-based rank in the returned list
    # Retrieved text is third-party data (may contain prompt injection). Always True; see
    # rag.retrieval.format.format_passages_for_prompt for a safe way to put it in a prompt.
    untrusted: bool = True

    def to_dict(self) -> dict:
        return asdict(self)
