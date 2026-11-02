import json
from typing import Any

import httpx
import pytest

from filings_rag.backends.base import BackendError
from filings_rag.backends.factory import make_backend
from filings_rag.backends.openai_compat import OpenAICompatBackend
from filings_rag.eval.report import RunSummary
from filings_rag.pricing import Usage, load_prices, model_cost, price_key

OK = {
    "model": "gpt-6.1-sol-2026-10-01",
    "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "391"}}],
    "usage": {
        "prompt_tokens": 1000,
        "completion_tokens": 7,
        "prompt_tokens_details": {"cached_tokens": 400},
    },
}


def backend(
    handler: Any, sleeps: list[float] | None = None, model: str = "gpt-6.1-sol", **kw: Any
) -> OpenAICompatBackend:
    return OpenAICompatBackend(
        model,
        transport=httpx.MockTransport(handler),
        sleep=(sleeps if sleeps is not None else []).append,
        **kw,
    )


def test_request_and_usage_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=OK)

    b = backend(handler, effort="low", max_tokens=50, extra_body={"temperature": 0})
    r = b.generate("SYS", "Q?")
    req = seen[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-test"
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "Q?"},
    ]
    assert body["model"] == "gpt-6.1-sol" and body["max_completion_tokens"] == 50
    assert body["reasoning_effort"] == "low" and body["temperature"] == 0
    assert r.text == "391" and r.model == "gpt-6.1-sol-2026-10-01" and r.stop_reason == "end_turn"
    # cached prompt tokens are cache reads; the rest of the prompt is plain input
    assert r.usage == Usage(input_tokens=600, output_tokens=7, cache_read_tokens=400)
    assert r.usage.prompt_tokens == 1000 and not r.refused
    # the dated snapshot is priced as the model we asked for: 600*2 + 400*0.1 + 7*10 per M
    assert model_cost(r.model, r.usage) == pytest.approx((1200 + 40 + 70) / 1e6)


def test_local_server_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={**OK, "model": "Qwen/Qwen3.6-27B"})

    b = backend(handler, model="Qwen/Qwen3.6-27B", base_url="http://127.0.0.1:8000/v1/")
    r = b.generate("s", "p")
    assert str(seen[0].url) == "http://127.0.0.1:8000/v1/chat/completions"
    assert "authorization" not in seen[0].headers
    assert model_cost(r.model, Usage(5000, 500)) == 0.0  # self-hosted: no token price


def test_stop_reasons_and_refusal() -> None:
    def reply(choice: dict[str, Any]) -> Any:
        return lambda req: httpx.Response(200, json={**OK, "choices": [choice]})

    cut = {"finish_reason": "length", "message": {"content": None}}
    r = backend(reply(cut)).generate("s", "p")
    assert r.stop_reason == "max_tokens" and r.text == ""
    refused = {"finish_reason": "stop", "message": {"content": None, "refusal": "I can't."}}
    r = backend(reply(refused)).generate("s", "p")
    assert r.refused and r.text == "I can't."
    filtered = {"finish_reason": "content_filter", "message": {"content": ""}}
    assert backend(reply(filtered)).generate("s", "p").refused


def test_retries_then_succeeds() -> None:
    codes = iter([429, 503, 200])
    sleeps: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        code = next(codes)
        if code == 200:
            return httpx.Response(200, json=OK)
        return httpx.Response(code, headers={"retry-after": "3"} if code == 429 else {})

    r = backend(handler, sleeps).generate("s", "p")
    assert r.text == "391" and sleeps == [3.0, 2.0]  # Retry-After, then exponential backoff


def test_transport_errors_retry_and_give_up() -> None:
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("refused")

    with pytest.raises(BackendError, match="after 2 retries"):
        backend(handler, max_retries=2).generate("s", "p")
    assert len(calls) == 3


def test_client_errors_are_not_retried() -> None:
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(BackendError, match="HTTP 401"):
        backend(handler).generate("s", "p")
    assert len(calls) == 1


def test_malformed_reply() -> None:
    with pytest.raises(BackendError, match="malformed"):
        backend(lambda req: httpx.Response(200, json={"choices": []})).generate("s", "p")


def test_factory_builds_it_and_keeps_gpu_rate_out() -> None:
    b = make_backend(
        {
            "backend": "openai-compatible",
            "model": "Qwen/Qwen3.6-27B",
            "base_url": "http://127.0.0.1:8000/v1",
            "gpu_usd_per_hour": 0.4,
            "effort": "low",
        }
    )
    assert isinstance(b, OpenAICompatBackend) and b.effort == "low"
    assert b.model == "Qwen/Qwen3.6-27B"


def test_price_key_matches_snapshots_by_longest_alias() -> None:
    table = load_prices()["models"]
    assert price_key("gpt-6.1-sol", table) == "gpt-6.1-sol"
    assert price_key("gpt-6.1-sol-2026-10-01", table) == "gpt-6.1-sol"
    with pytest.raises(KeyError):
        price_key("gpt-6.1-solar", table)
    with pytest.raises(KeyError):
        price_key("gpt-9", table)


def test_gpu_cost_is_rate_times_wall_time() -> None:
    s = RunSummary(
        run_id=1, name="n", config_hash="h", git="g", pipeline="p", corpus="c", retriever="r",
        scope="s", rerank="x", generation="g", judge="j", total=150,
    )  # fmt: skip
    assert s.gpu_cost is None and s.gpu_system_cost() is None
    s.gpu_rate, s.wall_s = 0.40, 5400.0  # 1.5 h
    s.embed_cost = 0.01
    s.aux_cost["plain API"] = 0.02
    assert s.gpu_cost == pytest.approx(0.60)
    assert s.gpu_system_cost() == pytest.approx(0.63)
    s.wall_s = None  # unfinished run: no GPU figure
    assert s.gpu_cost is None


def test_non_json_200_is_retried_then_fails() -> None:
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, text="<html>bad gateway page</html>")

    with pytest.raises(BackendError, match="non-JSON"):
        backend(handler, max_retries=1).generate("s", "p")
    assert len(calls) == 2


def test_unknown_served_name_falls_back_to_the_configured_model() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**OK, "model": "/models/snapshots/abc123"})

    r = backend(handler).generate("s", "p")
    assert r.model == "gpt-6.1-sol"
