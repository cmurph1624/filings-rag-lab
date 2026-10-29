"""Okapi BM25 inside Postgres, over the lexemes of a chunk table's `tsv` column.

Postgres's own ranking (`ts_rank_cd`) has no inverse document frequency, so in long questions
generic words ("capital", "based", "relevant") outweigh the company name and lexical search
returns the wrong filings. BM25 needs per-term document frequencies, which `tsvector` doesn't
keep, so each chunk table gets two derived tables, built from `tsv` (same english stemming):

- `{table}_postings (lexeme, chunk_id, document_id, tf)`: one row per distinct lexeme per chunk;
- `{table}_lengths (chunk_id, document_id, len)`: lexeme occurrences per chunk.

They are derived indexes, like the HNSW and GIN indexes: `frl ingest build` brings them up to
date (appended chunks only), and they never change a pipeline's chunks. Statistics (N, average
length, document frequency) are computed over the run's corpus, not the whole table.
"""

import math
from typing import Any

import psycopg
from psycopg import sql

from filings_rag.retrieve.base import Hit

K1 = 1.2
B = 0.75


def postings_tables(table: str) -> tuple[str, str]:
    return f"{table}_postings", f"{table}_lengths"


def ensure_postings(conn: psycopg.Connection[Any], table: str) -> int:
    """Create the BM25 tables if needed and add any chunks not yet indexed. Returns how many."""
    postings, lengths = postings_tables(table)
    t, p, ln = sql.Identifier(table), sql.Identifier(postings), sql.Identifier(lengths)
    conn.execute(
        sql.SQL(
            """CREATE TABLE IF NOT EXISTS {p} (
                lexeme      text   NOT NULL,
                chunk_id    bigint NOT NULL,
                document_id int    NOT NULL,
                tf          int    NOT NULL,
                PRIMARY KEY (lexeme, chunk_id)
            )"""
        ).format(p=p)
    )
    conn.execute(
        sql.SQL(
            """CREATE TABLE IF NOT EXISTS {ln} (
                chunk_id    bigint PRIMARY KEY,
                document_id int    NOT NULL,
                len         int    NOT NULL
            )"""
        ).format(ln=ln)
    )
    # Positions are capped at 256 per lexeme, far above any count in a 350-word chunk.
    conn.execute(
        sql.SQL(
            """INSERT INTO {p} (lexeme, chunk_id, document_id, tf)
               SELECT u.lexeme, c.id, c.document_id, coalesce(array_length(u.positions, 1), 1)
               FROM {t} c CROSS JOIN LATERAL unnest(c.tsv) u
               WHERE NOT EXISTS (SELECT 1 FROM {ln} l WHERE l.chunk_id = c.id)"""
        ).format(p=p, t=t, ln=ln)
    )
    added = conn.execute(
        sql.SQL(
            """INSERT INTO {ln} (chunk_id, document_id, len)
               SELECT c.id, c.document_id,
                      coalesce((SELECT sum(coalesce(array_length(u.positions, 1), 1))
                                FROM unnest(c.tsv) u), 0)
               FROM {t} c
               WHERE NOT EXISTS (SELECT 1 FROM {ln} l WHERE l.chunk_id = c.id)"""
        ).format(t=t, ln=ln)
    ).rowcount
    conn.commit()
    return added


_QUERY = """
SELECT p.chunk_id, d.doc_name, c.page, c.text,
       sum(q.idf * p.tf * (%(k1)s + 1)
           / (p.tf + %(k1)s * (1 - %(b)s + %(b)s * l.len / %(avgdl)s))) AS score
FROM unnest(%(lexemes)s::text[], %(idfs)s::float8[]) AS q(lexeme, idf)
JOIN {p} p ON p.lexeme = q.lexeme
JOIN {ln} l ON l.chunk_id = p.chunk_id
JOIN {t} c ON c.id = p.chunk_id
JOIN documents d ON d.id = p.document_id
WHERE p.document_id = ANY(%(docs)s)
GROUP BY p.chunk_id, d.doc_name, c.page, c.text
ORDER BY score DESC, p.chunk_id
LIMIT %(k)s
"""


class BM25:
    """Corpus statistics plus the scoring query, for one chunk table and one corpus."""

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        table: str,
        corpus: list[int],
        *,
        k1: float = K1,
        b: float = B,
    ) -> None:
        self._conn = conn
        self._table = table
        self._postings, self._lengths = postings_tables(table)
        self.corpus = corpus
        self.k1 = k1
        self.b = b
        row = conn.execute(
            sql.SQL("SELECT count(*), avg(len) FROM {ln} WHERE document_id = ANY(%s)").format(
                ln=sql.Identifier(self._lengths)
            ),
            (corpus,),
        ).fetchone()
        conn.commit()
        self.n = int(row[0]) if row else 0
        if self.n == 0:
            raise ValueError(f"no BM25 postings for this corpus in {table}; run `frl ingest build`")
        self.avgdl = float(row[1]) if row and row[1] else 1.0
        self._df: dict[str, int] = {}

    def lexemes(self, text: str) -> list[str]:
        row = self._conn.execute(
            "SELECT tsvector_to_array(to_tsvector('english', %s))", (text,)
        ).fetchone()
        return sorted(row[0]) if row and row[0] else []

    def idf(self, lexemes: list[str]) -> list[float]:
        """Okapi BM25 IDF, ln(1 + (N - df + 0.5) / (df + 0.5)); df cached per retriever."""
        missing = [x for x in lexemes if x not in self._df]
        if missing:
            counts = dict(
                self._conn.execute(
                    sql.SQL(
                        "SELECT lexeme, count(*) FROM {p}"
                        " WHERE lexeme = ANY(%s) AND document_id = ANY(%s) GROUP BY lexeme"
                    ).format(p=sql.Identifier(self._postings)),
                    (missing, self.corpus),
                ).fetchall()
            )
            for x in missing:
                self._df[x] = int(counts.get(x, 0))
        return [math.log(1 + (self.n - self._df[x] + 0.5) / (self._df[x] + 0.5)) for x in lexemes]

    def search(self, text: str, k: int, documents: list[int] | None = None) -> list[Hit]:
        """Top k chunks by BM25 within `documents` (default: the corpus)."""
        with self._conn.transaction():
            lexemes = self.lexemes(text)
            if not lexemes:
                return []
            rows = self._conn.execute(
                sql.SQL(_QUERY).format(
                    p=sql.Identifier(self._postings),
                    ln=sql.Identifier(self._lengths),
                    t=sql.Identifier(self._table),
                ),
                {
                    "lexemes": lexemes,
                    "idfs": self.idf(lexemes),
                    "k1": self.k1,
                    "b": self.b,
                    "avgdl": self.avgdl,
                    "docs": self.corpus if documents is None else documents,
                    "k": k,
                },
            ).fetchall()
        return [Hit(cid, doc, page, float(score), body) for cid, doc, page, body, score in rows]
