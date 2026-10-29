"""Retrievers turn a question into ranked chunks from one pipeline's index."""

from dataclasses import dataclass
from typing import Protocol


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


class Retriever(Protocol):
    def retrieve(self, query: str) -> Retrieval: ...
