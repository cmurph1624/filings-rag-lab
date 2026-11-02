"""Caches for embeddings and reranks, in Postgres (migrations/012_caches.sql).

Both wrap a real client and keep its interface. A hit bills nothing: `tokens` counts only
what this call paid for, and `cached_tokens` what the hits cost when they were first made, so
spend stays honest and cached work is visible. Caching makes retrieval repeatable: the
reranker's scores vary in precision between calls (docs/results/p3-noise.md).

The caches write through their own autocommit connection, so they never join or break a
caller's transaction, and a lock makes them safe for the harness's worker threads.
"""

import hashlib
import json
import threading
from typing import Any

import psycopg

from filings_rag.embed.base import Embedder, Embeddings, InputType, to_pgvector
from filings_rag.rerank.base import Reranked, Reranker


def _sha(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


def connect(database_url: str) -> psycopg.Connection[Any]:
    return psycopg.connect(database_url, autocommit=True)


def share_tokens(total: int, texts: list[str]) -> list[int]:
    """A batch's tokens shared out by text length (the API reports one total per batch)."""
    weights = [len(t) + 1 for t in texts]
    shares = [total * w // sum(weights) for w in weights]
    if shares:
        shares[-1] += total - sum(shares)
    return shares


class CachedEmbedder:
    def __init__(self, inner: Embedder, conn: psycopg.Connection[Any]) -> None:
        self.name = inner.name
        self.dims = inner.dims
        self._inner = inner
        self._conn = conn
        self._lock = threading.Lock()

    def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        keys = [_sha(t) for t in texts]
        key_sql = (self.name, self.dims, input_type)
        with self._lock:
            found: dict[bytes, tuple[list[float], int]] = {
                k: (json.loads(v), tok)
                for k, v, tok in self._conn.execute(
                    "SELECT text_sha256, embedding::text, tokens FROM embedding_cache"
                    " WHERE embedder = %s AND dims = %s AND input_type = %s"
                    " AND text_sha256 = ANY(%s)",
                    (*key_sql, list(set(keys))),
                )
            }
        hits = sum(k in found for k in keys)
        cached_tokens = sum(found[k][1] for k in keys if k in found)
        missing: dict[bytes, str] = {}
        for k, t in zip(keys, texts, strict=True):
            if k not in found:
                missing.setdefault(k, t)  # a repeated text embeds once
        billed = 0
        if missing:
            out = self._inner.embed(list(missing.values()), input_type)
            billed = out.tokens
            # Round-trip through the stored precision, so a miss and a later hit are equal.
            fresh = [json.loads(to_pgvector(v)) for v in out.vectors]
            shares = share_tokens(out.tokens, list(missing.values()))
            rows = list(zip(missing, fresh, shares, strict=True))
            with self._lock, self._conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO embedding_cache"
                    " (embedder, dims, input_type, text_sha256, embedding, tokens)"
                    " VALUES (%s, %s, %s, %s, %s::vector, %s) ON CONFLICT DO NOTHING",
                    [(*key_sql, k, to_pgvector(v), tok) for k, v, tok in rows],
                )
            found |= {k: (v, tok) for k, v, tok in rows}
        return Embeddings([found[k][0] for k in keys], billed, hits, cached_tokens)


class CachedReranker:
    def __init__(self, inner: Reranker, conn: psycopg.Connection[Any]) -> None:
        self.name = inner.name
        self._inner = inner
        self._conn = conn
        self._lock = threading.Lock()

    def rerank(self, query: str, texts: list[str], top_k: int) -> Reranked:
        if not texts:
            return self._inner.rerank(query, texts, top_k)
        key = (self.name, _sha(query), _sha(json.dumps(texts)), top_k)
        with self._lock:
            row = self._conn.execute(
                "SELECT result, tokens FROM rerank_cache WHERE model = %s AND query_sha256 = %s"
                " AND documents_sha256 = %s AND top_k = %s",
                key,
            ).fetchone()
        if row is not None:
            return Reranked([(int(i), float(s)) for i, s in row[0]], 0, int(row[1]))
        out = self._inner.rerank(query, texts, top_k)
        with self._lock:
            self._conn.execute(
                "INSERT INTO rerank_cache"
                " (model, query_sha256, documents_sha256, top_k, result, tokens)"
                " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (*key, json.dumps(out.order), out.tokens),
            )
        return out
