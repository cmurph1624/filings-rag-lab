import json

import httpx
import pytest

from filings_rag.embed.voyage import VoyageEmbedder, batches


def test_batches_respect_item_and_token_caps() -> None:
    texts = ["x" * 30] * 10  # ~11 est tokens each
    assert [len(b) for b in batches(texts, max_items=4, max_est_tokens=1000)] == [4, 4, 2]
    assert [len(b) for b in batches(texts, max_items=100, max_est_tokens=25)] == [2] * 5
    assert list(batches([], 4, 100)) == []
    assert [len(b) for b in batches(["y" * 3000], 4, 10)] == [1]  # oversize item still sent alone


def _handler(calls: list[dict[str, object]], fail_first: bool = False):  # type: ignore[no-untyped-def]
    def handle(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body)
        if fail_first and len(calls) == 1:
            return httpx.Response(429)
        n = len(body["input"])
        data = [{"index": i, "embedding": [float(i)] * 4} for i in reversed(range(n))]
        return httpx.Response(200, json={"data": data, "usage": {"total_tokens": 7 * n}})

    return handle


def test_embed_batches_orders_by_index_and_sums_tokens() -> None:
    calls: list[dict[str, object]] = []
    e = VoyageEmbedder("k", dims=4, transport=httpx.MockTransport(_handler(calls)), max_items=2)
    out = e.embed(["a", "b", "c"], "document")
    assert [v[0] for v in out.vectors] == [0.0, 1.0, 0.0]
    assert out.tokens == 21
    assert [c["input"] for c in calls] == [["a", "b"], ["c"]]
    assert all(c["input_type"] == "document" and c["truncation"] is False for c in calls)


def test_embed_retries_rate_limit() -> None:
    calls: list[dict[str, object]] = []
    e = VoyageEmbedder(
        "k", dims=4, transport=httpx.MockTransport(_handler(calls, True)), sleep=lambda s: None
    )
    assert len(e.embed(["a"], "query").vectors) == 1 and len(calls) == 2


def test_wrong_dims_and_missing_key_raise() -> None:
    e = VoyageEmbedder("k", dims=8, transport=httpx.MockTransport(_handler([])))
    with pytest.raises(ValueError):
        e.embed(["a"], "query")
    with pytest.raises(ValueError):
        VoyageEmbedder("")
