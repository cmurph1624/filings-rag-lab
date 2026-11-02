"""Rerankers reorder a retriever's candidates with a cross-encoder that reads query and text
together. Like embedders, each has a price-table name and reports the tokens it used."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Reranked:
    order: list[tuple[int, float]]  # (index into the input texts, relevance), best first
    tokens: int  # billed tokens, from the API's usage
    cached_tokens: int = 0  # a cache hit: what it cost first time (tokens is then 0)


class Reranker(Protocol):
    name: str  # price-table key in config/prices.toml [rerankers.*]

    def rerank(self, query: str, texts: list[str], top_k: int) -> Reranked: ...
