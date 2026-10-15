"""Hybrid retrieval over the IntelliHire chunk index.

SECURITY NOTE: every passage returned here is *untrusted data* (scraped docs,
candidate-supplied text, ...). It may contain prompt-injection text. This
package never builds prompts; downstream callers must put passages inside a
clearly delimited data block and must never treat their content as instructions.
"""

from .access import ROLE_NAMESPACES, NamespaceAccessError, resolve_namespaces
from .confidence import ConfidenceRule, RetrievalResult
from .format import format_passages_for_prompt
from .intent import Filters, RetrievalIntent
from .types import Passage

__all__ = [
    "Filters",
    "RetrievalIntent",
    "Passage",
    "RetrievalResult",
    "ConfidenceRule",
    "format_passages_for_prompt",
    "ROLE_NAMESPACES",
    "NamespaceAccessError",
    "resolve_namespaces",
    "retrieve",
]


def retrieve(intent, role, **kwargs):
    """retrieve(intent, role) -> list[Passage]  (see rag.retrieval.retriever)."""
    from .retriever import retrieve as _retrieve

    return _retrieve(intent, role, **kwargs)
