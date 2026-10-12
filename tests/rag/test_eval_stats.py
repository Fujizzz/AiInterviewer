"""Paired bootstrap, tuning grid bookkeeping and the confidence-eval helpers (no DB)."""

import pytest

# Optional rag-extra dependencies: skip the whole module when they are not installed.
# rag.eval.tune imports rag.index.db, which needs pgvector; the deploy CI env has psycopg only.
pytest.importorskip("psycopg")
pytest.importorskip("pgvector")

from rag.eval import tune
from rag.eval.stats import compare_modes, paired_bootstrap


def test_bootstrap_detects_a_consistent_gain():
    a = {f"q{i}": 0.6 + 0.01 * (i % 3) for i in range(40)}
    b = {q: v - 0.05 for q, v in a.items()}
    r = paired_bootstrap(a, b, n_boot=2000)
    assert (
        r["mean_diff"] == pytest.approx(0.05) and r["lo"] > 0 and r["significant"] and r["n"] == 40
    )


def test_bootstrap_includes_zero_for_noise_and_is_reproducible():
    a = {f"q{i}": (1.0 if i % 2 else 0.0) for i in range(40)}
    b = {f"q{i}": (0.0 if i % 2 else 1.0) if i % 4 else (1.0 if i % 2 else 0.0) for i in range(40)}
    r1, r2 = (
        paired_bootstrap(a, b, n_boot=2000, seed=3),
        paired_bootstrap(a, b, n_boot=2000, seed=3),
    )
    assert r1 == r2
    same = paired_bootstrap(a, dict(a), n_boot=500)
    assert same["mean_diff"] == 0 and not same["significant"]
    with pytest.raises(ValueError):
        paired_bootstrap({"a": 1.0}, {"b": 1.0})


def test_compare_modes_skips_missing_modes():
    pq = {
        "x": {
            "q1": {"ndcg@10": 1.0, "mrr@10": 1.0, "recall@10": 1.0},
            "q2": {"ndcg@10": 0.5, "mrr@10": 0.5, "recall@10": 0.5},
        },
        "y": {
            "q1": {"ndcg@10": 0.5, "mrr@10": 0.5, "recall@10": 0.5},
            "q2": {"ndcg@10": 0.5, "mrr@10": 0.5, "recall@10": 0.5},
        },
    }
    out = compare_modes(pq, [("x", "y"), ("x", "missing")])
    assert list(out) == ["x - y"] and out["x - y"]["ndcg@10"]["mean_diff"] == pytest.approx(0.25)


def test_tuning_grid_is_162_points_and_default_is_the_naive_rrf():
    g = tune.grid()
    assert len(g) == 162 and len(set(g)) == 162
    d = tune.Cfg()
    assert (d.rrf_k, d.w_dense, d.strict, d.ident, d.top_n) == (
        60,
        1.0,
        10.0,
        3.0,
        50,
    )  # pre-tuning default
    assert tune.Cfg().complexity() == (False,) * 6


def test_best_cfg_ties_go_to_the_default_like_config():
    table = {tune.Cfg(): {"q": {"ndcg": 0.5}}, tune.Cfg(rrf_k=10): {"q": {"ndcg": 0.5}}}
    assert tune.best_cfg(table, ["q"]) == tune.Cfg()
    table[tune.Cfg(rrf_k=10)]["q"]["ndcg"] = 0.6
    assert tune.best_cfg(table, ["q"]) == tune.Cfg(rrf_k=10)


def test_negative_file_loads_and_is_off_topic_sized():
    from rag.eval.confidence_eval import load_negatives
    from rag.index import config

    doc = load_negatives(config.DATA_DIR / "eval" / "negative_queries.yaml")
    assert 20 <= len(doc["negative"]) <= 25
    cats = {x["category"] for x in doc["negative"]}
    assert {"cooking", "sports", "travel", "gibberish", "vague"} <= cats
    assert any(not x["query"].isascii() for x in doc["in_domain_non_ascii"])


def test_identifier_slice_file_loads():
    from rag.eval.gold import load_gold
    from rag.index import config

    g = load_gold(config.DATA_DIR / "eval" / "identifier_queries.yaml")
    assert 8 <= len(g.queries) <= 10 and "draft" in g.status
