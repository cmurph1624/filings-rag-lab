"""Retrievers turn a question into ranked chunks from one pipeline's index."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    doc_name: str
    page: int
    score: float
    text: str


@dataclass(frozen=True)
class Retrieval:
    hits: list[Hit]  # best first
    embed_tokens: int  # query-embedding tokens spent
    embedder: str  # price-table key for those tokens
    # Spend of other models in this retrieval, by component (e.g. "rerank": {model, tokens,
    # cost_usd}); recorded in run_questions.aux.
    aux: dict[str, dict[str, Any]] = field(default_factory=dict)


class RetrievalError(RuntimeError):
    """Retrieval failed after spending query-embedding tokens, which must still be recorded."""

    def __init__(self, message: str, embed_tokens: int, embedder: str) -> None:
        super().__init__(message)
        self.embed_tokens = embed_tokens
        self.embedder = embedder


class Retriever(Protocol):
    def retrieve(self, query: str, documents: list[int] | None = None) -> Retrieval:
        """Top hits for `query`; `documents` narrows the corpus further (query scoping)."""
        ...
