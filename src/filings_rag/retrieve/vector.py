"""Pure vector top-k over one pipeline's chunk table, limited to a corpus's documents.

`search` is explicit so the Postgres planner can't change results behind our back:
- "exact": a full scan, true top-k by cosine distance (the default; ~0.1 s at 17K chunks).
- "hnsw": the approximate HNSW index, with `ef_search` candidates. With a document filter,
  pgvector's iterative scan (strict order) keeps fetching until k rows pass it.
"""

from dataclasses import replace
from typing import Any

import psycopg
from psycopg import sql

from filings_rag.embed.base import Embedder, to_pgvector
from filings_rag.retrieve.base import Hit, Retrieval, RetrievalError

_QUERY = """
SELECT c.id, d.doc_name, c.page, c.text, 1 - c.dist AS score, c.section
FROM (SELECT id, document_id, page, text, section, embedding <=> %(q)s::vector AS dist
      FROM {t} {where} ORDER BY embedding <=> %(q)s::vector LIMIT %(k)s) c
JOIN documents d ON d.id = c.document_id
ORDER BY c.dist, c.id
"""
SEARCH = ("exact", "hnsw")
MAX_SCAN_TUPLES = 20_000  # pgvector's default, pinned so a server change can't move results


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
        documents: list[int] | None = None,
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
        self.documents = documents  # None: every document in the table

    def _settings(self) -> list[sql.SQL | sql.Composed]:
        if self.search == "exact":
            return [sql.SQL("SET LOCAL enable_indexscan = off")]
        return [
            sql.SQL("SET LOCAL enable_seqscan = off"),
            sql.SQL("SET LOCAL hnsw.ef_search = {}").format(sql.Literal(self.ef_search)),
            sql.SQL("SET LOCAL hnsw.iterative_scan = strict_order"),
            sql.SQL("SET LOCAL hnsw.max_scan_tuples = {}").format(sql.Literal(MAX_SCAN_TUPLES)),
        ]

    def query(self, vector: list[float], documents: list[int] | None = None) -> list[Hit]:
        docs = self.documents if documents is None else documents
        where = sql.SQL("WHERE document_id = ANY(%(docs)s)" if docs is not None else "")
        with self._conn.transaction():
            for stmt in self._settings():
                self._conn.execute(stmt)
            rows = self._conn.execute(
                sql.SQL(_QUERY).format(t=self._table, where=where),
                {"q": to_pgvector(vector), "k": self.k, "docs": docs},
            ).fetchall()
        return [
            Hit(cid, doc, page, float(score), text, section)
            for cid, doc, page, text, score, section in rows
        ]

    def retrieve(self, query: str, documents: list[int] | None = None) -> Retrieval:
        """`documents` replaces the corpus filter for this query (a scope within the corpus)."""
        out = self._embedder.embed([query], "query")
        # A cached query vector still counts its tokens (a few dozen), so runs compare as
        # deployed systems; the reranker's cache hits are kept apart in its spend instead.
        out = replace(out, tokens=out.tokens + out.cached_tokens)
        filtered = documents is not None or self.documents is not None
        try:
            hits = self.query(out.vectors[0], documents)
        except psycopg.Error as e:
            raise RetrievalError(str(e), out.tokens, self._embedder.name) from e
        if len(hits) < self.k and self.search == "hnsw" and filtered:
            # The filtered iterative scan gave up (max_scan_tuples) before k rows passed.
            raise RetrievalError(
                f"hnsw returned {len(hits)} of {self.k} hits within the corpus filter",
                out.tokens,
                self._embedder.name,
            )
        return Retrieval(hits, out.tokens, self._embedder.name)
