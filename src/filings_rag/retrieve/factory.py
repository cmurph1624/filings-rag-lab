from typing import Any

import psycopg

from filings_rag.embed.base import Embedder
from filings_rag.retrieve.base import Retriever
from filings_rag.retrieve.vector import VectorRetriever

# Every option with its default, so a config's hash covers the settings actually used.
DEFAULTS: dict[str, dict[str, Any]] = {"vector": {"k": 10, "search": "exact", "ef_search": 100}}


def resolve_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The [retriever] table with defaults filled in and irrelevant options dropped."""
    kind = spec.get("kind")
    if kind not in DEFAULTS:
        raise ValueError(f"unknown retriever kind {kind!r}; known: {sorted(DEFAULTS)}")
    opts = {k: v for k, v in spec.items() if k != "kind"}
    unknown = opts.keys() - DEFAULTS[kind].keys()
    if unknown:
        raise ValueError(f"unknown {kind} retriever options: {sorted(unknown)}")
    resolved = DEFAULTS[kind] | opts
    if kind == "vector" and resolved["search"] == "exact":
        del resolved["ef_search"]  # only the HNSW index reads it
    return {"kind": kind, **resolved}


def make_retriever(
    conn: psycopg.Connection[Any], spec: dict[str, Any], table: str, embedder: Embedder
) -> Retriever:
    """Build a retriever from a run config's [retriever] table: {kind, ...options}."""
    opts = {k: v for k, v in resolve_spec(spec).items() if k != "kind"}
    return VectorRetriever(conn, table, embedder, **opts)
