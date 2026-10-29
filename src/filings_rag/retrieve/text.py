"""Lexical retrieval over a chunk table's `tsv` lexemes (english stemming, stopwords dropped).

`ranking`:
- "bm25" (default): Okapi BM25 with corpus statistics (`retrieve/bm25.py`).
- "ts_rank_cd": Postgres's own full-text ranking over an OR of the question's lexemes. It has
  no IDF, so generic question words outweigh the company name; kept for comparison.

On fb-distractors@1 (open subset) neither helps much on its own: recall@10 is 0.037 for
ts_rank_cd and 0.117 for BM25, against 0.527 for vector search. FinanceBench questions name
concepts ("capital expenditure") that statements spell differently ("purchases of property,
plant and equipment"), and their instruction text ("Answer in USD millions") adds rare terms.

Lexical search catches what embeddings blur: tickers, exact figures, "Item 7", fiscal years.
"""

from typing import Any

import psycopg
from psycopg import sql

from filings_rag.retrieve.base import Hit, Retrieval, RetrievalError
from filings_rag.retrieve.bm25 import BM25

RANKINGS = ("bm25", "ts_rank_cd")

# OR of the query's lexemes: plainto_tsquery ANDs them, which almost never matches a question.
_TS_RANK_CD = """
WITH q AS (
    SELECT replace(plainto_tsquery('english', %(text)s)::text, '&', '|')::tsquery AS query
)
SELECT c.id, d.doc_name, c.page, c.text, ts_rank_cd(c.tsv, q.query) AS score
FROM {t} c CROSS JOIN q
JOIN documents d ON d.id = c.document_id
WHERE c.tsv @@ q.query {docs}
ORDER BY score DESC, c.id
LIMIT %(k)s
"""


class TextRetriever:
    def __init__(
        self,
        conn: psycopg.Connection[Any],
        table: str,
        *,
        k: int = 10,
        ranking: str = "bm25",
        k1: float = 1.2,
        b: float = 0.75,
        documents: list[int] | None = None,
    ) -> None:
        if k < 1:
            raise ValueError("k must be at least 1")
        if ranking not in RANKINGS:
            raise ValueError(f"ranking must be one of {RANKINGS}")
        self._conn = conn
        self._table = table
        self.k = k
        self.ranking = ranking
        self.documents = documents  # None: every document in the table (ts_rank_cd only)
        self._bm25: BM25 | None = None
        if ranking == "bm25":
            if documents is None:
                raise ValueError("bm25 needs the corpus's documents for its statistics")
            self._bm25 = BM25(conn, table, documents, k1=k1, b=b)

    def _ts_rank_cd(self, text: str, docs: list[int] | None) -> list[Hit]:
        filt = sql.SQL("AND c.document_id = ANY(%(docs)s)" if docs is not None else "")
        with self._conn.transaction():
            rows = self._conn.execute(
                sql.SQL(_TS_RANK_CD).format(t=sql.Identifier(self._table), docs=filt),
                {"text": text, "k": self.k, "docs": docs},
            ).fetchall()
        return [Hit(cid, doc, page, float(score), body) for cid, doc, page, body, score in rows]

    def query(self, text: str, documents: list[int] | None = None) -> list[Hit]:
        if self._bm25 is not None:
            return self._bm25.search(text, self.k, documents)
        return self._ts_rank_cd(text, self.documents if documents is None else documents)

    def retrieve(self, query: str, documents: list[int] | None = None) -> Retrieval:
        try:
            hits = self.query(query, documents)
        except psycopg.Error as e:
            raise RetrievalError(str(e), 0, "") from e
        return Retrieval(hits, 0, "")
