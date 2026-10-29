from typing import Any

import psycopg

from filings_rag.embed.base import Embedder
from filings_rag.retrieve.base import Retriever
from filings_rag.retrieve.scope import resolve_scope
from filings_rag.retrieve.vector import VectorRetriever

# Every option with its default, so a config's hash covers the settings actually used.
DEFAULTS: dict[str, dict[str, Any]] = {"vector": {"k": 10, "search": "exact", "ef_search": 100}}


def resolve_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The [retriever] table with defaults filled in and irrelevant options dropped."""
    kind = spec.get("kind")
    if kind not in DEFAULTS:
        raise ValueError(f"unknown retriever kind {kind!r}; known: {sorted(DEFAULTS)}")
    opts = {k: v for k, v in spec.items() if k not in ("kind", "scope")}
    unknown = opts.keys() - DEFAULTS[kind].keys()
    if unknown:
        raise ValueError(f"unknown {kind} retriever options: {sorted(unknown)}")
    resolved = DEFAULTS[kind] | opts
    if kind == "vector" and resolved["search"] == "exact":
        del resolved["ef_search"]  # only the HNSW index reads it
    scope = resolve_scope(spec.get("scope"))
    if scope is not None:  # no scope adds nothing, so unscoped configs keep their hashes
        resolved["scope"] = scope
    return {"kind": kind, **resolved}


def make_retriever(
    conn: psycopg.Connection[Any],
    spec: dict[str, Any],
    table: str,
    embedder: Embedder,
    documents: list[int],
) -> Retriever:
    """Build a retriever from a run config's [retriever] table: {kind, ...options}.

    `documents` is the corpus's filings (`corpora.document_ids`). It is required: chunk tables
    hold every fetched filing, so an unfiltered retriever would search more than the config says.
    """
    opts = {k: v for k, v in resolve_spec(spec).items() if k not in ("kind", "scope")}
    return VectorRetriever(conn, table, embedder, documents=documents, **opts)
