"""Pure vector top-k over one pipeline's chunk table: no filters, no reranking (the P0 baseline).

`search` is explicit so the Postgres planner can't change results behind our back:
- "exact": a full scan, true top-k by cosine distance (the default; ~0.1 s at 17K chunks).
- "hnsw": the approximate HNSW index, with `ef_search` candidates.
"""

from typing import Any

import psycopg
from psycopg import sql

from filings_rag.embed.base import Embedder, to_pgvector
from filings_rag.retrieve.base import Hit, Retrieval, RetrievalError

_QUERY = """
SELECT c.id, d.doc_name, c.page, c.text, 1 - c.dist AS score
FROM (SELECT id, document_id, page, text, embedding <=> %(q)s::vector AS dist
      FROM {t} ORDER BY embedding <=> %(q)s::vector LIMIT %(k)s) c
JOIN documents d ON d.id = c.document_id
ORDER BY c.dist, c.id
"""
SEARCH = ("exact", "hnsw")


class VectorRetriever:
    def __init__(
        self,
        conn: psycopg.Connection[Any],
        table: str,
        embedder: Embedder,
        *,
        k: int = 10,
        search: str = "exact",
        ef_search: int = 100,
    ) -> None:
        if k < 1:
            raise ValueError("k must be at least 1")
        if search not in SEARCH:
            raise ValueError(f"search must be one of {SEARCH}")
        self._conn = conn
        self._table = sql.Identifier(table)
        self._embedder = embedder
        self.k = k
        self.search = search
        self.ef_search = max(ef_search, k)  # HNSW returns at most ef_search rows

    def _settings(self) -> list[sql.SQL | sql.Composed]:
        if self.search == "exact":
            return [sql.SQL("SET LOCAL enable_indexscan = off")]
        return [
            sql.SQL("SET LOCAL enable_seqscan = off"),
            sql.SQL("SET LOCAL hnsw.ef_search = {}").format(sql.Literal(self.ef_search)),
        ]

    def query(self, vector: list[float]) -> list[Hit]:
        with self._conn.transaction():
            for stmt in self._settings():
                self._conn.execute(stmt)
            rows = self._conn.execute(
                sql.SQL(_QUERY).format(t=self._table), {"q": to_pgvector(vector), "k": self.k}
            ).fetchall()
        return [Hit(cid, doc, page, float(score), text) for cid, doc, page, text, score in rows]

    def retrieve(self, query: str) -> Retrieval:
        out = self._embedder.embed([query], "query")
        try:
            hits = self.query(out.vectors[0])
        except psycopg.Error as e:
            raise RetrievalError(str(e), out.tokens, self._embedder.name) from e
        return Retrieval(hits, out.tokens, self._embedder.name)
