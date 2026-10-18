"""Optional cross-encoder rerank (BAAI/bge-reranker-v2-m3)."""

from __future__ import annotations

from collections.abc import Sequence

from ..index import config
from ..index.embed import pick_device


class Reranker:
    def __init__(
        self,
        model: str = config.RERANK_MODEL,
        revision: str = config.RERANK_REVISION,
        device: str | None = None,
        max_length: int = 512,
        batch_size: int = 16,
        fp16: bool = True,
    ):
        from sentence_transformers import CrossEncoder

        self.model_name, self.revision = model, revision
        self.batch_size = batch_size
        # fp16: scores differ from fp32 by <0.004 (Pearson 0.9999997, same top-1 on 40/40 gold
        # queries), 2.4x faster on MPS
        kwargs = {}
        if fp16:
            import torch

            kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
        self.model = CrossEncoder(
            model, revision=revision, device=pick_device(device), max_length=max_length, **kwargs
        )

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        """Relevance in (0, 1) (sigmoid of the model logit)."""
        if not texts:
            return []
        out = self.model.predict(
            [(query, t) for t in texts], batch_size=self.batch_size, show_progress_bar=False
        )
        return [float(x) for x in out]
