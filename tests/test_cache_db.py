import os

import psycopg
import pytest

from filings_rag import cache
from filings_rag.embed.base import Embeddings, InputType
from filings_rag.rerank.base import Reranked

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL"),
]


class Embed:
    name = "zz-cache-embed"
    dims = 3

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        self.calls.append((texts, input_type))
        # a value that changes between calls, like a nondeterministic API
        n = len(self.calls)
        return Embeddings([[0.1 * n, 0.2, len(t)] for t in texts], tokens=10 * len(texts))


class Rerank:
    name = "zz-cache-rerank"

    def __init__(self) -> None:
        self.calls = 0

    def rerank(self, query: str, texts: list[str], top_k: int) -> Reranked:
        self.calls += 1
        order = [(i, 0.9 - 0.1 * i + 0.001 * self.calls) for i in range(len(texts))]
        return Reranked(order[:top_k], 100)


@pytest.fixture
def cc():  # type: ignore[no-untyped-def]
    from filings_rag import db

    db.migrate(os.environ["DATABASE_URL"])
    with cache.connect(os.environ["DATABASE_URL"]) as c:
        yield c
        c.execute("DELETE FROM embedding_cache WHERE embedder = 'zz-cache-embed'")
        c.execute("DELETE FROM rerank_cache WHERE model = 'zz-cache-rerank'")


def test_embedder_reuses_vectors_and_reports_them_as_cached(cc: psycopg.Connection) -> None:  # type: ignore[type-arg]
    inner = Embed()
    e = cache.CachedEmbedder(inner, cc)
    first = e.embed(["a", "bb"], "document")
    assert (first.tokens, first.cached, first.cached_tokens) == (20, 0, 0)
    again = e.embed(["bb", "a", "ccc", "ccc"], "document")
    assert inner.calls[1] == (["ccc"], "document")  # only the new text, once
    assert again.vectors[:2] == [first.vectors[1], first.vectors[0]]
    assert (again.tokens, again.cached, again.cached_tokens) == (10, 2, 20)
    assert again.vectors[2] == again.vectors[3]


def test_embedder_keys_on_input_type(cc: psycopg.Connection) -> None:  # type: ignore[type-arg]
    inner = Embed()
    e = cache.CachedEmbedder(inner, cc)
    e.embed(["a"], "document")
    q = e.embed(["a"], "query")
    assert len(inner.calls) == 2 and q.cached == 0


def test_reranker_is_repeatable_and_bills_hits_as_cached(cc: psycopg.Connection) -> None:  # type: ignore[type-arg]
    inner = Rerank()
    r = cache.CachedReranker(inner, cc)
    first = r.rerank("q", ["x", "y", "z"], 2)
    again = r.rerank("q", ["x", "y", "z"], 2)
    assert inner.calls == 1
    assert again == Reranked(first.order, 0, 100)
    r.rerank("q", ["x", "y", "z"], 3)  # different top_k
    r.rerank("q", ["y", "x", "z"], 2)  # different candidate order
    r.rerank("q2", ["x", "y", "z"], 2)  # different query
    assert inner.calls == 4


def test_share_tokens_sums_to_total() -> None:
    assert sum(cache.share_tokens(101, ["a", "bbbb", "cc"])) == 101
    assert cache.share_tokens(0, []) == []
