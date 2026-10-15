"""Gold-set loading, matcher resolution and metrics."""

import pytest
import yaml

from rag.eval import gold as G
from rag.eval.metrics import hit_at_k, mrr_at_k, ndcg_at_k, percentile, recall_at_k
from rag.index import config

GOLD = config.DATA_DIR / "eval" / "gold_queries.yaml"


def test_gold_file_is_valid_draft_with_40_queries():
    gold = G.load_gold(GOLD)
    assert "draft" in gold.status and "human review" in gold.status
    assert len(gold.queries) == 40
    assert {q.category for q in gold.queries} == G.CATEGORIES
    assert len({q.id for q in gold.queries}) == 40


@pytest.mark.skipif(not list(config.CHUNKS_DIR.glob("*.jsonl")), reason="chunk files not present")
def test_every_gold_query_resolves_to_real_chunks():
    gold = G.load_gold(GOLD)
    chunks = G.load_all_chunks()
    rel = G.resolve_gold(gold, chunks)
    assert [q for q, ids in rel.items() if not ids] == []
    ns = {c["chunk_id"]: c["namespace"] for c in chunks}
    for q in gold.queries:
        if q.category == "question":
            assert any(ns[i] == "question" for i in rel[q.id]), q.id
        assert all(ns[i] in ("technical", "question") for i in rel[q.id])


def test_matcher_semantics():
    c = {
        "chunk_id": "a-1",
        "source_name": "a",
        "source_url": "http://x/y.md",
        "heading_path": ["Top", "Sub Section"],
        "text": "Hello select_related world",
    }
    assert G.Matcher(source="a", heading="sub section").matches(c)  # case-insensitive
    assert G.Matcher(source="a", text=["hello", "select_related"]).matches(c)
    assert not G.Matcher(source="a", text=["hello", "missing"]).matches(c)  # ALL text regexes
    assert not G.Matcher(source="b").matches(c)
    assert G.Matcher(url=r"y\.md$").matches(c)
    assert G.Matcher(chunk_id="a-1").matches(c) and not G.Matcher(chunk_id="a-2").matches(c)
    assert G.Matcher(heading="Top > Sub").matches(c)


def write(tmp_path, queries):
    p = tmp_path / "g.yaml"
    p.write_text(yaml.safe_dump({"status": "draft", "queries": queries}))
    return p


def test_loader_rejects_bad_files(tmp_path):
    ok = {"id": "q1", "query": "x", "category": "conceptual", "relevant": [{"source": "a"}]}
    assert len(G.load_gold(write(tmp_path, [ok])).queries) == 1
    for bad in (
        {**ok, "category": "weird"},
        {**ok, "relevant": []},
        {**ok, "relevant": [{"nope": 1}]},
        {**ok, "query": " "},
    ):
        with pytest.raises(ValueError):
            G.load_gold(write(tmp_path, [bad]))
    with pytest.raises(ValueError):
        G.load_gold(write(tmp_path, [ok, ok]))


def test_resolve_unions_matchers():
    gold = G.GoldSet(
        "draft",
        [
            G.GoldQuery(
                "q",
                "x",
                "conceptual",
                [G.Matcher(source="a", heading="one"), G.Matcher(source="b")],
            )
        ],
    )
    chunks = [
        {
            "chunk_id": "a1",
            "source_name": "a",
            "source_url": "",
            "heading_path": ["one"],
            "text": "t",
        },
        {
            "chunk_id": "a2",
            "source_name": "a",
            "source_url": "",
            "heading_path": ["two"],
            "text": "t",
        },
        {"chunk_id": "b1", "source_name": "b", "source_url": "", "heading_path": [], "text": "t"},
    ]
    assert G.resolve_gold(gold, chunks) == {"q": {"a1", "b1"}}


def test_metrics():
    ranked = ["x", "r1", "y", "r2", "z"]
    rel = {"r1", "r2", "r3"}
    assert recall_at_k(ranked, rel, 5) == pytest.approx(2 / 3)
    assert recall_at_k(ranked, rel, 2) == pytest.approx(1 / 2)  # denominator capped at k
    assert recall_at_k(["r1", "r2"], {"r1", "r2"}, 5) == 1.0
    assert recall_at_k(ranked, set(), 5) == 0.0
    assert mrr_at_k(ranked, rel) == 0.5 and mrr_at_k(["x"], rel) == 0.0
    assert hit_at_k(ranked, rel, 1) == 0.0 and hit_at_k(ranked, rel, 2) == 1.0
    assert ndcg_at_k(["r1", "r2", "r3"], rel, 10) == pytest.approx(1.0)
    import math

    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    ideal = 1 + 1 / math.log2(3) + 1 / math.log2(4)
    assert ndcg_at_k(ranked, rel, 10) == pytest.approx(dcg / ideal)
    assert percentile([1, 2, 3, 4], 0.5) == 2.5 and percentile([], 0.5) == 0.0
