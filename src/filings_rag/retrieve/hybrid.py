"""Hybrid retrieval: vector and full-text candidates fused by reciprocal rank fusion (RRF).

Each side returns its top `candidates`; a chunk scores sum(1 / (rrf_k + rank)) over the lists
it appears in (rank from 1), and the top k by that score are returned. RRF uses ranks only, so
the two sides' incomparable scores (cosine similarity, ts_rank_cd) never need calibrating.
"""

from collections.abc import Sequence

from filings_rag.retrieve.base import Hit, Retrieval, RetrievalError
from filings_rag.retrieve.text import TextRetriever
from filings_rag.retrieve.vector import VectorRetriever


def rrf(lists: Sequence[Sequence[Hit]], k: int, rrf_k: int = 60) -> list[Hit]:
    """Fuse ranked hit lists. Ties break on chunk id, so the order is deterministic."""
    scores: dict[int, float] = {}
    first: dict[int, Hit] = {}
    for hits in lists:
        for rank, h in enumerate(hits, 1):
            scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            first.setdefault(h.chunk_id, h)
    best = sorted(scores, key=lambda cid: (-scores[cid], cid))[:k]
    return [
        Hit(cid, first[cid].doc_name, first[cid].page, scores[cid], first[cid].text) for cid in best
    ]


class HybridRetriever:
    def __init__(
        self,
        vector: VectorRetriever,
        text: TextRetriever,
        *,
        k: int = 10,
        rrf_k: int = 60,
    ) -> None:
        if k < 1:
            raise ValueError("k must be at least 1")
        if vector.k < k or text.k < k:
            raise ValueError("each side's candidates must be at least k")
        self._vector = vector
        self._text = text
        self.k = k
        self.rrf_k = rrf_k

    def retrieve(self, query: str, documents: list[int] | None = None) -> Retrieval:
        dense = self._vector.retrieve(query, documents)  # embeds the query; raises with its spend
        try:
            lexical = self._text.retrieve(query, documents)
        except RetrievalError as e:  # keep the query-embedding spend on the failure
            raise RetrievalError(str(e), dense.embed_tokens, dense.embedder) from e
        fused = rrf([dense.hits, lexical.hits], self.k, self.rrf_k)
        return Retrieval(fused, dense.embed_tokens, dense.embedder)
