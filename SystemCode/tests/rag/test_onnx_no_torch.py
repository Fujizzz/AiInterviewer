"""The onnx-int8 query path must never import torch, transformers or sentence-transformers.

Checked in a fresh interpreter (this process may already have imported them for other tests) with
the committed tiny fixture, so no real model is needed. Needs numpy, onnxruntime and tokenizers;
the retrieval-chain test also needs psycopg and pgvector.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

for _dep in ("numpy", "onnxruntime", "tokenizers"):
    pytest.importorskip(_dep)

REPO = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "tiny_encoder"
FORBIDDEN = ("torch", "transformers", "sentence_transformers", "tensorflow", "huggingface_hub")


def _run(body: str, check: bool = True) -> subprocess.CompletedProcess:
    code = textwrap.dedent(body) + (_CHECK if check else "")
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        timeout=120,
    )


_CHECK = f"""
import sys
forbidden = {FORBIDDEN!r}
bad = sorted(m for m in sys.modules if m.split(".")[0] in forbidden)
assert not bad, "forbidden modules imported: " + ", ".join(bad)
print("clean", len(sys.modules))
"""


def test_the_encoder_module_and_a_real_session_import_no_torch():
    code = f"""
    import numpy
    from pathlib import Path
    from rag.index.encoders import build_query_encoder  # the factory
    from rag.index import onnx_encoder as OE
    manifest = OE.load_manifest(Path({str(FIXTURE / "manifest.json")!r}))
    enc = OE.OnnxQueryEncoder(Path({str(FIXTURE)!r}), manifest=manifest, threads=1)
    assert enc.encode(["hello world"]).shape == (1, 8)
    """
    done = _run(code)
    assert done.returncode == 0, done.stderr[-800:]
    assert "clean" in done.stdout


def test_settings_adapter_and_pool_import_no_torch():
    code = """
    import rag.settings, rag.adapter, rag.retrieval.pool, rag.retrieval.lazy
    from rag.index.encoders import build_query_encoder
    from rag.retrieval.pool import _build_embedder
    """
    done = _run(code)
    assert done.returncode == 0, done.stderr[-800:]


@pytest.mark.skipif(
    any(importlib.util.find_spec(m) is None for m in ("psycopg", "psycopg_pool", "pgvector")),
    reason="retrieval chain needs psycopg, psycopg_pool and pgvector",
)
def test_the_whole_retrieval_chain_imports_no_torch():
    code = f"""
    import rag.retrieval.retriever, rag.index.load, rag.retrieval.pool, rag.adapter
    from rag.retrieval.pool import _build_embedder
    from pathlib import Path
    from rag.index import onnx_encoder as OE
    m = OE.load_manifest(Path({str(FIXTURE / "manifest.json")!r}))
    OE.OnnxQueryEncoder(Path({str(FIXTURE)!r}), manifest=m, threads=1)
    """
    done = _run(code)
    assert done.returncode == 0, done.stderr[-800:]


def test_the_check_itself_catches_a_forbidden_import():
    done = _run("import json\nimport sys\nsys.modules['torch'] = json\n")
    assert done.returncode != 0 and "forbidden modules imported: torch" in done.stderr
