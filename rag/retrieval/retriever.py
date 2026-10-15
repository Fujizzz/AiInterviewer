"""retrieve(intent, role) -> list[Passage]: filter -> dense + lexical -> RRF -> (rerank).

Retrieved text is DATA. Nothing here builds prompts; callers must keep passage
text out of instruction positions (treat it as potentially adversarial).
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import psycopg

from ..index.db import connect
from ..index.embed import Embedder, build_embedding_text
from ..index.encoders import (
    ENCODER_TORCH,
    build_query_encoder,
    encoder_label,
    encoder_settings_from_env,
)
from ..index.load import check_index_meta
from .access import CANDIDATE_NAMESPACE, resolve_namespaces, scope_namespaces
from .confidence import DEFAULT_RULE, ConfidenceRule, RetrievalResult, assess
from .fusion import rrf_fuse
from .intent import RetrievalIntent
from .sql import FETCH_SQL, build_filter_clause, dense_sql, lexical_params, lexical_sql
from .types import Passage

MODES = ("dense", "lexical", "hybrid")

# Final config, chosen on the 40-query draft gold set (see README /
# data/reports/retrieval-tune-*.md):
# dense is weighted 2x in RRF, RRF k=10, identifier ILIKE bonus off, strict-AND bonus kept.
DEFAULT_TOP_N = 50
DEFAULT_RRF_K = 10
DEFAULT_RRF_WEIGHTS = {"dense": 2.0, "lexical": 1.0}
DEFAULT_STRICT_BONUS = 10.0
DEFAULT_IDENT_BONUS = 0.0
DEFAULT_RERANK_N = 10


class Retriever:
    """Holds the DB connection and (lazily) the query embedder / reranker."""

    def __init__(
        self,
        dsn: str | None = None,
        embedder: object | None = None,
        reranker: object | None = None,
        top_n: int = DEFAULT_TOP_N,
        rerank_n: int = DEFAULT_RERANK_N,
        lexical_config: str = "simple",
        rrf_k: int = DEFAULT_RRF_K,
        rrf_weights: dict[str, float] | None = None,
        strict_bonus: float = DEFAULT_STRICT_BONUS,
        ident_bonus: float = DEFAULT_IDENT_BONUS,
        hnsw_ef_search: int = 100,
        confidence_rule: ConfidenceRule = DEFAULT_RULE,
        min_rerank_score: float | None = None,
        planner_overrides: dict[str, str] | None = None,
        exact_dense: bool = True,
        check_model: bool = True,
        embed_kwargs: dict | None = None,
        connection: psycopg.Connection | None = None,
    ):
        self.dsn = dsn
        self._embedder = embedder
        self._reranker = reranker
        self.top_n = top_n
        self.rerank_n = rerank_n
        self.lexical_config = lexical_config
        self.rrf_k = rrf_k
        self.rrf_weights = dict(DEFAULT_RRF_WEIGHTS) if rrf_weights is None else rrf_weights
        self.strict_bonus = strict_bonus
        self.ident_bonus = ident_bonus
        self.hnsw_ef_search = hnsw_ef_search
        self.confidence_rule = confidence_rule
        self.min_rerank_score = min_rerank_score
        # session settings applied inside the dense transaction, e.g. {"enable_seqscan": "off"}
        # (tests / diagnostics only: forces the HNSW plan)
        self.planner_overrides = dict(planner_overrides or {})
        # Dense search is EXACT by default: index scans are disabled inside the dense transaction.
        # The HNSW index (m=16, ef_construction=64, built incrementally) lost recall as the corpus
        # grew past ~60k rows (overlap@10 vs exact fell to 0.93, min 0.2 on short queries at
        # ef_search=100) and the planner started choosing it. An exact scan costs ~90 ms at 64k
        # rows.
        # Ignored when `planner_overrides` is given (tests / diagnostics that force a plan).
        self.exact_dense = exact_dense
        self.check_model = check_model
        self.embed_kwargs = embed_kwargs or {}
        # An injected connection (e.g. checked out of a pool by the RAGPort adapter) is borrowed:
        # it must be autocommit, it is never replaced or closed here, and a Retriever bound to it
        # must not be shared between threads.
        self._borrowed = connection is not None
        self._conn: psycopg.Connection | None = connection
        self._checked = False
        self._iterative_ok: bool | None = None

    # -- lazies ---------------------------------------------------------
    @property
    def conn(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            if self._borrowed:
                raise RuntimeError("the connection injected into this Retriever is closed")
            self._conn = connect(self.dsn)
            self._conn.autocommit = True
            self._checked = False
        return self._conn

    @property
    def embedder(self):
        if self._embedder is None:
            kind, kwargs = encoder_settings_from_env()
            if kind == ENCODER_TORCH:
                self._embedder = Embedder(**self.embed_kwargs)
            else:
                self._embedder = build_query_encoder(kind, **kwargs)
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is None:
            from .rerank import Reranker

            self._reranker = Reranker()
        return self._reranker

    def close(self) -> None:
        if not self._borrowed and self._conn is not None and not self._conn.closed:
            self._conn.close()

    def _ensure_model_matches(self) -> None:
        if self._checked or not self.check_model:
            return
        check_index_meta(self.conn, self.embedder.meta, set_if_empty=False)
        self._checked = True

    # -- stages ---------------------------------------------------------
    def _dense(self, intent: RetrievalIntent, namespaces: Sequence[str], n: int) -> list[tuple]:
        self._ensure_model_matches()
        qvec = self.embedder.encode([intent.dense_query_text()])[0]
        return self._run_dense(intent, namespaces, n, qvec)

    def _run_dense(
        self,
        intent: RetrievalIntent,
        namespaces: Sequence[str],
        n: int,
        qvec,
        explain: bool = False,
    ):
        """Execute (or EXPLAIN) the dense query under the exact production session settings."""
        where, params = build_filter_clause(intent, namespaces)
        params.update(qvec=qvec, top_n=n)
        sql = dense_sql(where)
        with self.conn.transaction():
            self._set_hnsw()
            overrides = self.planner_overrides
            if not overrides and self.exact_dense:
                overrides = {"enable_indexscan": "off", "enable_bitmapscan": "off"}
            for k, v in overrides.items():
                self.conn.execute("SELECT set_config(%s, %s, true)", (k, v))
            if explain:
                return [r[0] for r in self.conn.execute("EXPLAIN " + sql, params)]
            return self.conn.execute(sql, params).fetchall()

    def explain_dense(self, intent: RetrievalIntent, role: str) -> list[str]:
        """EXPLAIN of the dense query for `role`, with the same session settings `retrieve()` uses
        (hnsw.ef_search, iterative_scan, planner overrides). The query vector is a dummy: the plan
        does
        not depend on its values."""
        import numpy as np

        namespaces = scope_namespaces(
            resolve_namespaces(role, intent.namespaces), intent.candidate_id
        )
        qvec = np.full(1024, 1.0 / 32.0, dtype=np.float32)
        return self._run_dense(intent, namespaces, max(self.top_n, intent.k), qvec, explain=True)

    def _set_hnsw(self) -> None:
        self.conn.execute(
            "SELECT set_config('hnsw.ef_search', %s, true)", (str(self.hnsw_ef_search),)
        )
        # filtered ANN: keep scanning until enough rows pass the WHERE (pgvector >= 0.8)
        if self._iterative_ok is not False:
            try:
                with self.conn.transaction():
                    self.conn.execute(
                        "SELECT set_config('hnsw.iterative_scan', 'strict_order', true)"
                    )
                self._iterative_ok = True
            except psycopg.Error:
                self._iterative_ok = False

    def _lexical(self, intent: RetrievalIntent, namespaces: Sequence[str], n: int) -> list[tuple]:
        n_terms, n_ids, params = lexical_params(intent.query)
        if n_terms == 0 and n_ids == 0:
            return []
        where, fparams = build_filter_clause(intent, namespaces)
        params.update(fparams)
        params["top_n"] = n
        sql = lexical_sql(
            where,
            n_terms,
            n_ids,
            self.lexical_config,
            self.strict_bonus,
            self.ident_bonus,
            components=True,
        )
        # rows: (chunk_id, score, rank_cd, strict, idents)
        return self.conn.execute(sql, params).fetchall()

    # -- public ---------------------------------------------------------
    def retrieve(
        self,
        intent: RetrievalIntent,
        role: str,
        mode: str = "hybrid",
        rerank: bool = False,
        timings: dict[str, float] | None = None,
        min_rerank_score: float | None = None,
    ) -> RetrievalResult:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        namespaces = resolve_namespaces(role, intent.namespaces)  # raises on violation
        namespaces = scope_namespaces(namespaces, intent.candidate_id)  # fail closed on candidate
        if not namespaces:
            return RetrievalResult(
                [],
                confidence="low",
                reasons=["no readable namespace: candidate scope (candidate_id) is missing"],
            )
        t0 = time.perf_counter()
        n = max(self.top_n, intent.k)
        dense_rows = self._dense(intent, namespaces, n) if mode in ("dense", "hybrid") else []
        t1 = time.perf_counter()
        lex_rows = self._lexical(intent, namespaces, n) if mode in ("lexical", "hybrid") else []
        t2 = time.perf_counter()

        dense_score = {cid: float(s) for cid, s in dense_rows}
        lex_score = {r[0]: float(r[1]) for r in lex_rows}
        stage_rank: dict[str, dict[str, int]] = {
            "dense": {cid: i + 1 for i, (cid, _) in enumerate(dense_rows)},
            "lexical": {r[0]: i + 1 for i, r in enumerate(lex_rows)},
        }
        if mode == "dense":
            order = [(cid, None) for cid, _ in dense_rows]
        elif mode == "lexical":
            order = [(r[0], None) for r in lex_rows]
        else:
            order = rrf_fuse(
                {"dense": [c for c, _ in dense_rows], "lexical": [r[0] for r in lex_rows]},
                k=self.rrf_k,
                weights=self.rrf_weights,
            )
        rrf_score = {cid: s for cid, s in order if s is not None}

        pool = [cid for cid, _ in order][: (max(self.rerank_n, intent.k) if rerank else intent.k)]
        passages = self._hydrate(pool, namespaces, intent.candidate_id)
        by_id = {p.chunk_id: p for p in passages}
        ordered = [by_id[c] for c in pool if c in by_id]
        for p in ordered:
            p.scores["dense"] = dense_score.get(p.chunk_id)
            p.scores["lexical"] = lex_score.get(p.chunk_id)
            p.scores["rrf"] = rrf_score.get(p.chunk_id)
            p.stage_ranks = {s: r.get(p.chunk_id) for s, r in stage_rank.items()}
        t3 = time.perf_counter()

        max_rerank = None
        if rerank and ordered:
            texts = [build_embedding_text(self._rerank_view(p)) for p in ordered]
            for p, s in zip(ordered, self.reranker.score(intent.query, texts), strict=True):
                p.scores["rerank"] = s
            ordered.sort(
                key=lambda p: (-(p.scores["rerank"] or 0.0), p.stage_ranks.get("dense") or 10**9)
            )
            max_rerank = max(p.scores["rerank"] for p in ordered)
            cutoff = min_rerank_score if min_rerank_score is not None else self.min_rerank_score
            if cutoff is not None:  # optional hard cut-off; may return fewer than k passages
                ordered = [p for p in ordered if p.scores["rerank"] >= cutoff]
        ordered = ordered[: intent.k]
        for i, p in enumerate(ordered, 1):
            p.rank = i
        if timings is not None:
            timings.update(
                dense=t1 - t0, lexical=t2 - t1, hydrate=t3 - t2, total=time.perf_counter() - t0
            )
        # --- confidence signals (see rag/retrieval/confidence.py) ---
        n_terms, n_ids, _ = lexical_params(intent.query)
        lexical_hit = any(r[3] or r[4] for r in lex_rows)
        max_dense = float(dense_rows[0][1]) if dense_rows else None
        if mode == "lexical":
            max_dense = None
        level, reasons = assess(
            len(ordered),
            max_dense,
            max_rerank,
            lexical_hit,
            n_terms + n_ids,
            intent.query.isascii(),
            self.confidence_rule,
        )
        if not intent.query.isascii() and n_terms + n_ids == 0:
            reasons.append("non-ASCII query: lexical search is ASCII-only, dense results only")
        return RetrievalResult(
            ordered,
            max_dense=max_dense,
            max_rerank=max_rerank,
            lexical_hit=lexical_hit,
            confidence=level,
            reasons=reasons,
            encoder=(
                encoder_label(getattr(self._embedder, "meta", None))
                if max_dense is not None
                else None
            ),
        )

    @staticmethod
    def _rerank_view(p: Passage) -> dict:
        return {"title": p.title, "heading_path": p.heading_path, "text": p.text}

    def _hydrate(
        self, ids: list[str], namespaces: Sequence[str], candidate_id: str | None = None
    ) -> list[Passage]:
        if not ids:
            return []
        open_ns = [n for n in namespaces if n != CANDIDATE_NAMESPACE]
        cand = candidate_id if CANDIDATE_NAMESPACE in namespaces else None
        rows = self.conn.execute(FETCH_SQL, {"ids": ids, "ns": open_ns, "cand": cand}).fetchall()
        out = []
        for cid, text, title, hp, url, ver, ns, src, dom, diff, trust, lic, owner in rows:
            # defence in depth; SQL already filtered on both conditions
            if ns not in namespaces:
                continue
            if ns == CANDIDATE_NAMESPACE and (not cand or owner != cand):
                continue
            out.append(
                Passage(
                    chunk_id=cid,
                    text=text,
                    title=title,
                    heading_path=list(hp or []),
                    source_url=url,
                    doc_version=ver,
                    namespace=ns,
                    source_name=src,
                    domain=dom,
                    difficulty=diff,
                    trust_level=trust,
                    license=lic,
                    candidate_id=owner,
                )
            )
        return out


def plan_scan_type(plan_lines: Sequence[str]) -> str:
    """Classify an EXPLAIN of the dense query: 'hnsw' (HNSW index scan), 'seqscan' (Seq Scan + Sort,
    exact), or 'other-exact' (btree/bitmap scan + Sort, exact)."""
    text = "\n".join(plan_lines)
    if "chunks_embedding_hnsw" in text:
        return "hnsw"
    if "Seq Scan on chunks" in text:
        return "seqscan"
    return "other-exact"


_default: Retriever | None = None


def get_default_retriever() -> Retriever:
    global _default
    if _default is None:
        _default = Retriever()
    return _default


def retrieve(
    intent: RetrievalIntent,
    role: str,
    rerank: bool = False,
    mode: str = "hybrid",
    retriever: Retriever | None = None,
    min_rerank_score: float | None = None,
) -> RetrievalResult:
    """Module-level convenience: retrieve(intent, role) -> RetrievalResult (a list[Passage] plus
    confidence signals). Callers must treat `result.low_confidence` as "no evidence"."""
    return (retriever or get_default_retriever()).retrieve(
        intent, role, mode=mode, rerank=rerank, min_rerank_score=min_rerank_score
    )
