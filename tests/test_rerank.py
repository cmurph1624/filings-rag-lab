import json

import httpx
import pytest

from filings_rag.config import Settings
from filings_rag.pricing import rerank_cost
from filings_rag.rerank.base import Reranked
from filings_rag.rerank.factory import make_reranker
from filings_rag.rerank.voyage import VoyageReranker
from filings_rag.retrieve.base import Hit, Retrieval, RetrievalError
from filings_rag.retrieve.factory import make_retriever, resolve_spec
from filings_rag.retrieve.rerank import RerankingRetriever


def _client(handler, **kw):  # type: ignore[no-untyped-def]
    return VoyageReranker(
        "k", "rerank-3", transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw
    )


def test_voyage_request_and_parsing() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        data = [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.4}]
        return httpx.Response(200, json={"data": data, "usage": {"total_tokens": 321}})

    out = _client(handler).rerank("capex?", ["a", "b", "c"], top_k=2)
    assert out == Reranked([(2, 0.9), (0, 0.4)], 321)
    assert seen == {
        "query": "capex?", "documents": ["a", "b", "c"], "model": "rerank-3", "top_k": 2,
        "truncation": False,
    }  # fmt: skip


def test_voyage_retries_then_succeeds_and_rejects_bad_indexes() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(
            200, json={"data": [{"index": 5, "relevance_score": 1}], "usage": {"total_tokens": 1}}
        )

    with pytest.raises(ValueError, match="out of range"):
        _client(handler).rerank("q", ["a"], 1)
    assert len(calls) == 2


def test_voyage_skips_the_call_for_no_candidates() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    assert _client(handler).rerank("q", [], 10) == Reranked([], 0)


def test_factory_and_prices() -> None:
    assert make_reranker("rerank-3-lite", Settings(voyage_api_key="k")).name == "rerank-3-lite"
    with pytest.raises(ValueError, match="unknown reranker"):
        make_reranker("rerank-9", Settings(voyage_api_key="k"))
    assert rerank_cost("rerank-3", 1_000_000) == pytest.approx(0.05)
    assert rerank_cost("rerank-3-lite", 1_000_000) == pytest.approx(0.02)
    with pytest.raises(KeyError):
        rerank_cost("rerank-9", 1)


def _hits(*ids: int) -> list[Hit]:
    return [Hit(i, f"D{i}", 1, 1.0 / i, f"text {i}") for i in ids]


class FakeBase:
    def __init__(self, hits: list[Hit]) -> None:
        self.hits = hits
        self.calls: list[list[int] | None] = []

    def retrieve(self, query: str, documents: list[int] | None = None) -> Retrieval:
        self.calls.append(documents)
        return Retrieval(self.hits, 7, "voyage-finance-2")


class FakeReranker:
    name = "rerank-3"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def rerank(self, query: str, texts: list[str], top_k: int) -> Reranked:
        if self.fail:
            raise RuntimeError("API down")
        order = sorted(range(len(texts)), key=lambda i: -int(texts[i].split()[1]))  # biggest id
        return Reranked([(i, 0.1 * (len(texts) - r)) for r, i in enumerate(order)][:top_k], 1000)


def test_reranking_retriever_reorders_keeps_k_and_records_spend() -> None:
    base = FakeBase(_hits(1, 2, 3, 4))
    r = RerankingRetriever(base, FakeReranker(), k=2).retrieve("q", [9])
    assert [h.chunk_id for h in r.hits] == [4, 3] and r.hits[0].score == pytest.approx(0.4)
    assert base.calls == [[9]]  # the scope reaches the base retriever
    assert (r.embed_tokens, r.embedder) == (7, "voyage-finance-2")
    assert r.aux == {
        "rerank": {"model": "rerank-3", "tokens": 1000, "cost_usd": pytest.approx(5e-5)}
    }


def test_reranker_failure_keeps_the_embedding_spend() -> None:
    with pytest.raises(RetrievalError, match="rerank: API down") as e:
        RerankingRetriever(FakeBase(_hits(1, 2)), FakeReranker(fail=True)).retrieve("q")
    assert e.value.embed_tokens == 7


def test_unpriced_reranker_fails_at_startup() -> None:
    bad = FakeReranker()
    bad.name = "rerank-9"
    with pytest.raises(KeyError):
        RerankingRetriever(FakeBase([]), bad)


def test_rerank_spec_resolution() -> None:
    base = {"kind": "vector", "k": 10}
    assert "rerank" not in resolve_spec(base)  # absent: hashes unchanged
    assert resolve_spec({**base, "rerank": {}})["rerank"] == {"model": "rerank-3", "candidates": 50}
    with pytest.raises(ValueError, match="at least k"):
        resolve_spec({**base, "rerank": {"candidates": 5}})
    with pytest.raises(ValueError, match="rerank options"):
        resolve_spec({**base, "rerank": {"top_n": 5}})
    with pytest.raises(ValueError, match="exactly when"):
        make_retriever(None, {**base, "rerank": {}}, "t", None, [])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="config says"):
        lite = FakeReranker()
        lite.name = "rerank-3-lite"
        make_retriever(None, {**base, "rerank": {}}, "t", None, [], lite)  # type: ignore[arg-type]
