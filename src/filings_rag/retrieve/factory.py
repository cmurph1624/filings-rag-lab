from typing import Any

import psycopg

from filings_rag.embed.base import Embedder
from filings_rag.rerank.base import Reranker
from filings_rag.retrieve.base import Retriever
from filings_rag.retrieve.hybrid import HybridRetriever
from filings_rag.retrieve.rerank import RerankingRetriever
from filings_rag.retrieve.scope import resolve_scope
from filings_rag.retrieve.text import TextRetriever
from filings_rag.retrieve.vector import VectorRetriever

# Every option with its default, so a config's hash covers the settings actually used.
DEFAULTS: dict[str, dict[str, Any]] = {
    "vector": {"k": 10, "search": "exact", "ef_search": 100},
    # Lexical over the chunks' tsv lexemes: Okapi BM25, or Postgres's ts_rank_cd (no IDF).
    "text": {"k": 10, "ranking": "bm25", "k1": 1.2, "b": 0.75},
    # Vector and text top-`candidates` each, fused by reciprocal rank fusion.
    "hybrid": {
        "k": 10,
        "candidates": 50,
        "rrf_k": 60,
        "search": "exact",
        "ef_search": 100,
        "ranking": "bm25",
        "k1": 1.2,
        "b": 0.75,
    },
}
# `rerank = {model, candidates}` on any kind: the base retrieves `candidates`, the reranker
# keeps the best k.
RERANK_DEFAULTS: dict[str, Any] = {"model": "rerank-3", "candidates": 50}


def resolve_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The [retriever] table with defaults filled in and irrelevant options dropped."""
    kind = spec.get("kind")
    if kind not in DEFAULTS:
        raise ValueError(f"unknown retriever kind {kind!r}; known: {sorted(DEFAULTS)}")
    opts = {k: v for k, v in spec.items() if k not in ("kind", "scope", "rerank")}
    unknown = opts.keys() - DEFAULTS[kind].keys()
    if unknown:
        raise ValueError(f"unknown {kind} retriever options: {sorted(unknown)}")
    resolved = DEFAULTS[kind] | opts
    if kind in ("vector", "hybrid") and resolved["search"] == "exact":
        del resolved["ef_search"]  # only the HNSW index reads it
    if kind in ("text", "hybrid") and resolved["ranking"] == "ts_rank_cd":
        del resolved["k1"], resolved["b"]  # BM25 parameters
    scope = resolve_scope(spec.get("scope"))
    if scope is not None:  # no scope adds nothing, so unscoped configs keep their hashes
        resolved["scope"] = scope
    if "rerank" in spec:  # absent adds nothing, so configs without it keep their hashes
        rr = dict(spec["rerank"])
        bad = rr.keys() - RERANK_DEFAULTS.keys()
        if bad:
            raise ValueError(f"unknown rerank options: {sorted(bad)}")
        resolved["rerank"] = RERANK_DEFAULTS | rr
        if resolved["rerank"]["candidates"] < resolved["k"]:
            raise ValueError("rerank candidates must be at least k")
    return {"kind": kind, **resolved}


def make_retriever(
    conn: psycopg.Connection[Any],
    spec: dict[str, Any],
    table: str,
    embedder: Embedder,
    documents: list[int],
    reranker: Reranker | None = None,
) -> Retriever:
    """Build a retriever from a run config's [retriever] table: {kind, ...options}.

    `documents` is the corpus's filings (`corpora.document_ids`). It is required: chunk tables
    hold every fetched filing, so an unfiltered retriever would search more than the config says.
    `reranker` must be given exactly when the spec has `rerank` (`rerank.factory.make_reranker`).
    """
    resolved = resolve_spec(spec)
    rr = resolved.get("rerank")
    if (rr is None) != (reranker is None):
        raise ValueError("pass a reranker exactly when the [retriever] has rerank")
    if rr is not None and reranker is not None:
        if reranker.name != rr["model"]:
            raise ValueError(f"reranker is {reranker.name}, config says {rr['model']}")
        base_spec = {k: v for k, v in spec.items() if k != "rerank"} | {"k": rr["candidates"]}
        base = make_retriever(conn, base_spec, table, embedder, documents)
        return RerankingRetriever(base, reranker, k=resolved["k"])
    opts = {k: v for k, v in resolved.items() if k not in ("kind", "scope")}
    text_opts = {o: opts[o] for o in ("ranking", "k1", "b") if o in opts}
    if resolved["kind"] == "text":
        return TextRetriever(conn, table, k=opts["k"], documents=documents, **text_opts)
    if resolved["kind"] == "hybrid":
        if opts["candidates"] < opts["k"]:
            raise ValueError("hybrid candidates must be at least k")
        vector_opts = {o: opts[o] for o in ("search", "ef_search") if o in opts}
        n = opts["candidates"]
        return HybridRetriever(
            VectorRetriever(conn, table, embedder, k=n, documents=documents, **vector_opts),
            TextRetriever(conn, table, k=n, documents=documents, **text_opts),
            k=opts["k"],
            rrf_k=opts["rrf_k"],
        )
    return VectorRetriever(conn, table, embedder, documents=documents, **opts)
