"""Rerank a retriever's top `candidates` with a cross-encoder and keep the best k.

The base retriever (any kind, within the corpus and scope) runs with k = candidates; the
reranker reads each candidate with the query and reorders them. Hit scores become the
reranker's relevance. Spend lands in `Retrieval.aux["rerank"]` (model, tokens, cost_usd),
which the harness records in `run_questions.aux` and the report counts in answering cost.
"""

from dataclasses import replace

from filings_rag.pricing import rerank_cost
from filings_rag.rerank.base import Reranker
from filings_rag.retrieve.base import Retrieval, RetrievalError, Retriever


class RerankingRetriever:
    def __init__(self, base: Retriever, reranker: Reranker, *, k: int = 10) -> None:
        if k < 1:
            raise ValueError("k must be at least 1")
        rerank_cost(reranker.name, 0)  # fail at startup, not mid-run, if it isn't priced
        self._base = base
        self._reranker = reranker
        self.k = k

    def retrieve(self, query: str, documents: list[int] | None = None) -> Retrieval:
        first = self._base.retrieve(query, documents)
        if not first.hits:
            return first
        try:
            out = self._reranker.rerank(query, [h.text for h in first.hits], self.k)
        except Exception as e:  # keep the query-embedding spend on the failed row
            raise RetrievalError(f"rerank: {e}", first.embed_tokens, first.embedder) from e
        hits = [replace(first.hits[i], score=score) for i, score in out.order[: self.k]]
        spend = {
            "model": self._reranker.name,
            "tokens": out.tokens,
            "cost_usd": rerank_cost(self._reranker.name, out.tokens),
        }
        if out.cached_tokens:  # a cache hit: tokens and cost are 0, the report prices it
            spend["cached_tokens"] = out.cached_tokens
        return Retrieval(hits, first.embed_tokens, first.embedder, {**first.aux, "rerank": spend})
