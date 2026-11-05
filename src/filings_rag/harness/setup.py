"""Build a run's retrieval side from its config: retriever (with the caches), scoper and router.

Shared by `frl run` and `frl gate`, so a gate run retrieves exactly as a benchmark run does.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from filings_rag import cache, corpora
from filings_rag.config import Settings
from filings_rag.embed.factory import make_embedder
from filings_rag.harness.config import RunConfig
from filings_rag.harness.run import pipeline_index
from filings_rag.rerank.factory import make_reranker
from filings_rag.retrieve.base import Retriever
from filings_rag.retrieve.factory import make_retriever, resolve_spec
from filings_rag.retrieve.scope import Scoper, make_scoper
from filings_rag.route.router import Router, make_router
from filings_rag.trace import TracedEmbedder, TracedReranker, Tracer


@dataclass
class RetrievalSide:
    retriever: Retriever
    scoper: Scoper | None
    router: Router | None


def retrieval_side(
    conn: psycopg.Connection[Any],
    cfg: RunConfig,
    settings: Settings,
    companies: Path,
    cache_conn: psycopg.Connection[Any] | None,
    tracer: Tracer | None = None,
) -> RetrievalSide:
    """`cache_conn` None calls the embedder and reranker every time (`--no-cache`). A `tracer`
    (the serving app's) records each embed and rerank call as a step."""
    table, embedder_name, dims = pipeline_index(conn, cfg.pipeline)
    corpus_ids = corpora.document_ids(conn, cfg.corpus, table)
    rr = resolve_spec(cfg.retriever).get("rerank")
    embedder = make_embedder(embedder_name, dims, settings)
    reranker = make_reranker(rr["model"], settings) if rr else None
    if cache_conn is not None:
        embedder = cache.CachedEmbedder(embedder, cache_conn)
        reranker = cache.CachedReranker(reranker, cache_conn) if reranker else None
    if tracer is not None and tracer.enabled:
        embedder = TracedEmbedder(embedder, tracer)
        reranker = TracedReranker(reranker, tracer) if reranker else None
    retriever = make_retriever(conn, cfg.retriever, table, embedder, corpus_ids, reranker)
    scoper = make_scoper(conn, cfg.retriever.get("scope"), corpus_ids, companies)
    router = make_router(conn, cfg.router)
    if router is not None and scoper is None:
        raise ValueError("[router] needs [retriever.scope]: facts anchor on the scope")
    return RetrievalSide(retriever, scoper, router)
