from typing import Any

import psycopg

from filings_rag.embed.base import Embedder
from filings_rag.retrieve.base import Retriever
from filings_rag.retrieve.vector import VectorRetriever

KINDS: dict[str, set[str]] = {"vector": {"k", "search", "ef_search"}}


def make_retriever(
    conn: psycopg.Connection[Any], spec: dict[str, Any], table: str, embedder: Embedder
) -> Retriever:
    """Build a retriever from a run config's [retriever] table: {kind, ...options}."""
    kind = spec.get("kind")
    if kind not in KINDS:
        raise ValueError(f"unknown retriever kind {kind!r}; known: {sorted(KINDS)}")
    opts = {k: v for k, v in spec.items() if k != "kind"}
    unknown = opts.keys() - KINDS[kind]
    if unknown:
        raise ValueError(f"unknown {kind} retriever options: {sorted(unknown)}")
    return VectorRetriever(conn, table, embedder, **opts)
