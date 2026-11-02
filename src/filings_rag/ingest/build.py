"""Chunk → embed → index for one pipeline version. Resumable per document."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from filings_rag.embed.base import Embedder, to_pgvector
from filings_rag.extract.html import EXTRACTOR
from filings_rag.ingest.base import Doc, IngestPipeline, chunk_table
from filings_rag.retrieve.bm25 import ensure_postings, postings_tables


@dataclass
class BuildStats:
    documents: int = 0
    empty: int = 0
    chunks: int = 0
    embed_tokens: int = 0  # billed
    cached_chunks: int = 0  # chunks whose vector came from the embedding cache
    cached_tokens: int = 0  # what those cost when first embedded


def register(conn: psycopg.Connection[Any], p: IngestPipeline, e: Embedder) -> str:
    """Insert the pipeline row, or verify an existing one matches exactly. Returns its table."""
    table = chunk_table(p.name, p.version)
    want = (p.params, e.name, e.dims, EXTRACTOR, table)
    row = conn.execute(
        "SELECT params, embedder, dims, extractor, chunk_table FROM pipelines"
        " WHERE name = %s AND version = %s",
        (p.name, p.version),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO pipelines (name, version, params, embedder, dims, extractor, chunk_table)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (p.name, p.version, Jsonb(p.params), e.name, e.dims, EXTRACTOR, table),
        )
    elif tuple(row) != want:
        raise ValueError(
            f"{p.name}@{p.version} was built with {tuple(row)}, now {want}; bump the version"
        )
    conn.execute(
        sql.SQL(
            """CREATE TABLE IF NOT EXISTS {t} (
                id          bigserial PRIMARY KEY,
                document_id int  NOT NULL REFERENCES documents (id),
                ord         int  NOT NULL,
                char_start  int  NOT NULL,
                char_end    int  NOT NULL,
                page        int  NOT NULL,
                text        text NOT NULL,
                section     text,
                embedding   vector({dims}) NOT NULL,
                tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
                UNIQUE (document_id, ord)
            )"""
        ).format(t=sql.Identifier(table), dims=sql.Literal(e.dims))
    )
    conn.commit()
    return table


def build(
    conn: psycopg.Connection[Any],
    p: IngestPipeline,
    e: Embedder,
    *,
    docs: list[str] | None = None,
    log: Callable[[str], None] = print,
) -> BuildStats:
    table = register(conn, p, e)
    t = sql.Identifier(table)
    stats = BuildStats()
    rows = conn.execute(
        sql.SQL(
            """SELECT d.id, d.doc_name, d.company, d.body, d.page_offsets FROM documents d
               WHERE d.extractor = %s AND (%s::text[] IS NULL OR d.doc_name = ANY(%s))
                 AND NOT EXISTS (SELECT 1 FROM {t} c WHERE c.document_id = d.id)
               ORDER BY d.id"""
        ).format(t=t),
        (EXTRACTOR, docs, docs),
    ).fetchall()
    conn.commit()  # close the read's implicit transaction so each document commits on its own
    for doc_id, doc_name, company, body, pages in rows:
        meta = {"doc_name": doc_name, "company": company}
        chunks = p.chunk(Doc(doc_id, body, list(pages), meta))
        if not chunks:  # nothing to embed; would otherwise be retried on every run
            stats.empty += 1
            log(f"  {doc_name:40} no text, skipped")
            continue
        out = e.embed([c.text for c in chunks], "document")
        # Record spend first, in its own commit: tokens count as spent even if the insert fails.
        conn.execute(
            "UPDATE pipelines SET embed_tokens = embed_tokens + %s,"
            " embed_cached_tokens = embed_cached_tokens + %s WHERE name = %s AND version = %s",
            (out.tokens, out.cached_tokens, p.name, p.version),
        )
        conn.commit()
        # A real transaction per document: a crash resumes cleanly.
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                sql.SQL(
                    "INSERT INTO {t} (document_id, ord, char_start, char_end, page, text,"
                    " section, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)"
                ).format(t=t),
                [
                    (
                        doc_id,
                        c.ord,
                        c.char_start,
                        c.char_end,
                        c.page,
                        c.text,
                        c.section,
                        to_pgvector(v),
                    )  # fmt: skip
                    for c, v in zip(chunks, out.vectors, strict=True)
                ],
            )
        stats.documents += 1
        stats.chunks += len(chunks)
        stats.embed_tokens += out.tokens
        stats.cached_chunks += out.cached
        stats.cached_tokens += out.cached_tokens
        cached = f" ({out.cached:,} cached)" if out.cached else ""
        log(f"  {doc_name:40} {len(chunks):>5} chunks {out.tokens:>9,} tokens{cached}")
    conn.execute(
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS {i} ON {t} USING hnsw (embedding vector_cosine_ops)"
        ).format(i=sql.Identifier(f"{table}_hnsw"), t=t)
    )
    conn.execute(
        sql.SQL("CREATE INDEX IF NOT EXISTS {i} ON {t} USING gin (tsv)").format(
            i=sql.Identifier(f"{table}_tsv"), t=t
        )
    )
    conn.commit()
    added = ensure_postings(conn, table)  # BM25 term statistics for the new chunks
    if added:
        log(f"  bm25: indexed {added:,} chunks")
    return stats


def drop(conn: psycopg.Connection[Any], p: IngestPipeline) -> bool:
    """Remove a pipeline version's build: chunk table, BM25 tables, labels and registry row.
    For CI, where a PR's new pipeline is rebuilt on every push. Returns whether it existed."""
    table = chunk_table(p.name, p.version)
    with conn.transaction():
        conn.execute(
            "DELETE FROM evidence_labels WHERE pipeline = %s AND version = %s",
            (p.name, p.version),
        )
        for t in (*postings_tables(table), table):
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {t}").format(t=sql.Identifier(t)))
        cur = conn.execute(
            "DELETE FROM pipelines WHERE name = %s AND version = %s", (p.name, p.version)
        )
    return cur.rowcount > 0
