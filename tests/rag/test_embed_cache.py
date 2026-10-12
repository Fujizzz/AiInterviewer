"""Embedding cache incrementality, with a fake encoder (no model download)."""

import json

import pytest

# Optional rag-extra dependencies: skip the whole module when they are not installed.
np = pytest.importorskip("numpy")

from rag.index import embed as E  # noqa: E402

DIM = 8
META = E.make_meta(model="fake", revision="r1", dim=DIM, max_seq_length=64)


class FakeEncoder:
    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        out = []
        for t in texts:
            rng = np.random.default_rng(abs(hash(t)) % (2**32))
            v = rng.normal(size=DIM)
            out.append(v / np.linalg.norm(v))
        return np.array(out, dtype=np.float32)


def chunk(cid, text, title="T", heading=("A", "B")):
    return {
        "chunk_id": cid,
        "text": text,
        "title": title,
        "heading_path": list(heading),
        "token_count": 10,
    }


def test_embedding_text_format():
    c = chunk("x", "body", title="B", heading=("A", "B"))
    assert E.build_embedding_text(c) == "A > B\n\nbody"  # title == last heading -> not repeated
    c = chunk("x", "body", title="Other", heading=("A", "B"))
    assert E.build_embedding_text(c) == "Other\nA > B\n\nbody"
    assert E.build_embedding_text(chunk("x", "body", title="", heading=())) == "body"


def test_hash_covers_title_and_heading_not_only_text():
    a = E.chunk_hash(chunk("x", "same", heading=("A",)))
    b = E.chunk_hash(chunk("x", "same", heading=("A", "B")))
    assert a != b and a == E.chunk_hash(chunk("x", "same", heading=("A",)))


def run(tmp_path, chunks, enc, meta=META, save_every=1024):
    return E.embed_source("src", chunks, enc, meta, tmp_path, save_every=save_every)


def test_first_run_embeds_all_and_roundtrips(tmp_path):
    enc = FakeEncoder()
    chunks = [chunk(f"c{i}", f"text {i}") for i in range(5)]
    st = run(tmp_path, chunks, enc)
    assert (st.embedded, st.reused, st.dropped) == (5, 0, 0)
    cache = E.load_cache("src", tmp_path)
    assert cache.ids == [f"c{i}" for i in range(5)]
    assert cache.vectors.shape == (5, DIM) and cache.vectors.dtype == np.float32
    assert cache.hashes["c0"] == E.chunk_hash(chunks[0])
    doc = json.loads((tmp_path / "src.ids.json").read_text())
    assert doc["meta"]["model"] == "fake" and doc["meta"]["revision"] == "r1"


def test_rerun_is_a_noop(tmp_path):
    chunks = [chunk(f"c{i}", f"text {i}") for i in range(4)]
    run(tmp_path, chunks, FakeEncoder())
    enc2 = FakeEncoder()
    st = run(tmp_path, chunks, enc2)
    assert enc2.calls == [] and (st.embedded, st.reused) == (0, 4)


def test_only_changed_and_new_are_reembedded_and_deleted_dropped(tmp_path):
    chunks = [chunk(f"c{i}", f"text {i}") for i in range(5)]
    run(tmp_path, chunks, FakeEncoder())
    before = E.load_cache("src", tmp_path)
    v_c0 = before.vectors[before.row_of()["c0"]].copy()

    new = [
        chunk("c0", "text 0"),  # unchanged
        chunk("c1", "text 1 EDITED"),  # changed
        chunk("c2", "text 2"),  # unchanged
        chunk("c5", "brand new"),
    ]  # new ; c3, c4 deleted
    enc = FakeEncoder()
    st = run(tmp_path, new, enc)
    assert sorted(t for call in enc.calls for t in call) == sorted(
        [E.build_embedding_text(new[1]), E.build_embedding_text(new[3])]
    )
    assert (st.embedded, st.reused, st.dropped) == (2, 2, 2)
    after = E.load_cache("src", tmp_path)
    assert after.ids == ["c0", "c1", "c2", "c5"]
    assert set(after.hashes) == {"c0", "c1", "c2", "c5"}
    np.testing.assert_array_equal(after.vectors[after.row_of()["c0"]], v_c0)  # reused untouched
    assert not np.allclose(
        after.vectors[after.row_of()["c1"]], before.vectors[before.row_of()["c1"]]
    )


def test_pure_deletion_rewrites_cache_without_encoding(tmp_path):
    chunks = [chunk(f"c{i}", f"t{i}") for i in range(3)]
    run(tmp_path, chunks, FakeEncoder())
    enc = FakeEncoder()
    st = run(tmp_path, chunks[:2], enc)
    assert enc.calls == [] and st.dropped == 1
    assert E.load_cache("src", tmp_path).ids == ["c0", "c1"]


@pytest.mark.parametrize(
    "field,value",
    [("model", "other"), ("revision", "r2"), ("format_version", 99), ("max_seq_length", 128)],
)
def test_meta_change_forces_full_rebuild(tmp_path, field, value):
    chunks = [chunk(f"c{i}", f"t{i}") for i in range(3)]
    run(tmp_path, chunks, FakeEncoder())
    meta2 = {**META, field: value}
    enc = FakeEncoder()
    st = run(tmp_path, chunks, enc, meta=meta2)
    assert st.full_rebuild and st.embedded == 3 and st.reused == 0
    assert E.load_cache("src", tmp_path).meta[field] == value


def test_informational_meta_keys_do_not_invalidate(tmp_path):
    chunks = [chunk("c0", "t")]
    run(tmp_path, chunks, FakeEncoder(), meta={**META, "fp16": True, "device": "mps"})
    st = run(tmp_path, chunks, FakeEncoder(), meta={**META, "fp16": False, "device": "cpu"})
    assert st.embedded == 0 and not st.full_rebuild


def test_partial_progress_is_saved_and_resumed(tmp_path):
    chunks = [chunk(f"c{i}", f"t{i}") for i in range(6)]

    class Boom(FakeEncoder):
        def __call__(self, texts):
            if len(self.calls) == 2:
                raise RuntimeError("interrupted")
            return super().__call__(texts)

    with pytest.raises(RuntimeError):
        run(tmp_path, chunks, Boom(), save_every=2)
    partial = E.load_cache("src", tmp_path)
    assert partial.ids == ["c0", "c1", "c2", "c3"]
    enc = FakeEncoder()
    st = run(tmp_path, chunks, enc, save_every=2)
    assert st.embedded == 2 and st.reused == 4


def test_corrupt_or_torn_cache_is_ignored(tmp_path):
    chunks = [chunk("c0", "t0"), chunk("c1", "t1")]
    run(tmp_path, chunks, FakeEncoder())
    (tmp_path / "src.npy").write_bytes(b"garbage")
    assert E.load_cache("src", tmp_path) is None
    st = run(tmp_path, chunks, FakeEncoder())
    assert st.embedded == 2


def test_encoder_shape_and_nan_guards(tmp_path):
    chunks = [chunk("c0", "t0")]
    with pytest.raises(ValueError):
        run(tmp_path, chunks, lambda texts: np.zeros((len(texts), DIM + 1), np.float32))
    with pytest.raises(ValueError):
        run(tmp_path, chunks, lambda texts: np.full((len(texts), DIM), np.nan, np.float32))
    with pytest.raises(ValueError):
        run(tmp_path, [chunk("a", "x"), chunk("a", "y")], FakeEncoder())
