"""index_meta compatibility rules for query encoders (no database: a scripted connection).

A derived (onnx-int8) query encoder is accepted when it comes from the same base model, revision,
dim, max_seq_length and format as the index; a different model is refused; it can never define
index_meta. Needs psycopg + pgvector only because rag.index.load imports the DB helpers.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("psycopg")
pytest.importorskip("pgvector")

from rag.index import config  # noqa: E402
from rag.index import embed as E  # noqa: E402
from rag.index.load import IndexError_, check_index_meta  # noqa: E402


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0]

    def __iter__(self):
        return iter(self._rows)


class ScriptedConn:
    """Just enough of psycopg.Connection for check_index_meta."""

    def __init__(self, stored: dict[str, str], n_rows: int = 16379, models=("BAAI/bge-m3",)):
        self.stored, self.n_rows, self.models = dict(stored), n_rows, list(models)
        self.writes: list[tuple] = []

    def execute(self, sql: str, params=None):
        if "FROM index_meta" in sql:
            return _Result(list(self.stored.items()))
        if "count(*)" in sql:
            return _Result([(self.n_rows,)])
        if "DISTINCT embedding_model" in sql:
            return _Result([(m,) for m in self.models])
        if sql.lstrip().startswith("INSERT INTO index_meta"):
            self.writes.append(params)
            return _Result([])
        raise AssertionError(sql)


def index_identity() -> dict[str, str]:
    m = E.make_meta()
    return {k: str(m[k]) for k in ("model", "revision", "dim", "max_seq_length", "format_version")}


def onnx_meta(**overrides) -> dict:
    meta = {**E.make_meta(), "encoder": "onnx-int8", "encoder_variant": "int8-pc"}
    meta.update(overrides)
    return meta


def test_torch_meta_is_still_accepted_and_a_different_model_still_refused():
    conn = ScriptedConn(index_identity())
    check_index_meta(conn, {**E.make_meta(), "encoder": "torch"}, set_if_empty=False)
    check_index_meta(conn, E.make_meta(), set_if_empty=False)  # caches written before this change
    with pytest.raises(IndexError_, match="model"):
        check_index_meta(conn, E.make_meta(model="BAAI/bge-small-en-v1.5"), set_if_empty=False)


def test_onnx_int8_from_the_same_base_is_accepted_for_queries():
    conn = ScriptedConn(index_identity())
    check_index_meta(conn, onnx_meta(), set_if_empty=False)
    assert conn.writes == []


@pytest.mark.parametrize(
    ("override", "key"),
    [
        ({"model": "BAAI/bge-small-en-v1.5"}, "model"),
        ({"model": "intfloat/multilingual-e5-large"}, "model"),
        ({"revision": "0" * 40}, "revision"),
        ({"dim": 768}, "dim"),
        ({"max_seq_length": 512}, "max_seq_length"),
        ({"format_version": 2}, "format_version"),
    ],
)
def test_onnx_int8_from_a_different_base_is_refused(override, key):
    conn = ScriptedConn(index_identity())
    with pytest.raises(IndexError_, match=key) as info:
        check_index_meta(conn, onnx_meta(**override), set_if_empty=False)
    assert "build-onnx" in str(info.value)  # tells the operator how to fix the artifact


def test_a_derived_encoder_can_never_define_the_index():
    conn = ScriptedConn({}, n_rows=0, models=())
    with pytest.raises(IndexError_, match="cannot define or write the index"):
        check_index_meta(conn, onnx_meta(), set_if_empty=True)
    assert conn.writes == []
    # torch may initialise an empty index, as before
    check_index_meta(conn, E.make_meta(), set_if_empty=True)
    assert len(conn.writes) == 5


def test_unknown_encoder_names_are_refused():
    with pytest.raises(IndexError_, match="unknown query encoder"):
        check_index_meta(ScriptedConn(index_identity()), onnx_meta(encoder="fp8"), False)


def test_empty_index_meta_with_foreign_vectors_is_still_refused_for_onnx():
    conn = ScriptedConn({}, models=("some/other-model",))
    with pytest.raises(IndexError_, match="refusing to mix"):
        check_index_meta(conn, onnx_meta(), set_if_empty=False)
    check_index_meta(ScriptedConn({}), onnx_meta(), set_if_empty=False)  # same model: fine


def test_the_committed_manifest_is_accepted_by_the_indexed_identity():
    from rag.index.onnx_encoder import load_manifest

    m = load_manifest()
    meta = {
        "model": m.base_model,
        "revision": m.base_revision,
        "dim": m.dim,
        "max_seq_length": m.max_seq_length,
        "format_version": m.format_version,
        "encoder": "onnx-int8",
        "encoder_variant": m.variant,
    }
    check_index_meta(ScriptedConn(index_identity()), meta, set_if_empty=False)
    assert meta["revision"] == config.EMBED_REVISION


def test_a_fixture_manifest_with_another_revision_is_refused():
    from pathlib import Path

    from rag.index.onnx_encoder import load_manifest

    m = load_manifest(Path(__file__).parent / "fixtures" / "tiny_encoder" / "manifest.json")
    meta = {
        "model": m.base_model,
        "revision": m.base_revision,  # "fixture" != the indexed revision
        "dim": m.dim,
        "max_seq_length": m.max_seq_length,
        "format_version": m.format_version,
        "encoder": "onnx-int8",
    }
    with pytest.raises(IndexError_):
        check_index_meta(ScriptedConn(index_identity()), meta, set_if_empty=False)
