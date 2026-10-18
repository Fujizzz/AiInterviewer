"""The study inputs stay consistent (no database needed)."""

import importlib
import json

import pytest
import yaml

from rag.eval.gold import load_gold
from rag.index import config

EVAL = config.DATA_DIR / "eval"


def test_gold_v2_is_the_original_plus_extra_rules():
    base = load_gold(EVAL / "gold_queries.yaml")
    v2 = load_gold(EVAL / "gold_queries_v2.yaml")
    extra = yaml.safe_load((EVAL / "gold_extra_labels.yaml").read_text(encoding="utf-8"))
    assert [q.id for q in v2.queries] == [q.id for q in base.queries]
    assert set(extra) <= {q.id for q in base.queries}
    n_base = {q.id: len(q.relevant) for q in base.queries}
    for q in v2.queries:
        assert len(q.relevant) == n_base[q.id] + len(extra.get(q.id, []))
    assert "draft" in v2.status


def test_rare_token_set_is_a_list_of_distinct_identifiers():
    toks = json.loads((EVAL / "rare_token_queries.json").read_text(encoding="utf-8"))
    assert len(toks) == len(set(toks)) >= 100
    assert all(isinstance(t, str) and len(t) >= 7 for t in toks)


@pytest.mark.parametrize(
    "mod",
    [
        "common",
        "fusion_fix_eval",
        "serving_latency_matrix",
        "unlabeled_top10_review",
        "build_gold_v2",
    ],
)
def test_study_modules_import(mod):
    # the deploy lock environment has neither package; the studies need them (rag.index.db)
    pytest.importorskip("pgvector")
    pytest.importorskip("psycopg_pool")
    importlib.import_module(f"rag.eval.studies.{mod}")


def test_random_term_candidates_are_distinct_and_unlabelled_by_corpus():
    lines = (EVAL / "random_terms_candidates_300.txt").read_text(encoding="utf-8").splitlines()
    terms = [x.strip() for x in lines if x.strip() and not x.startswith("#")]
    assert len(terms) == len(set(terms)) == 300
