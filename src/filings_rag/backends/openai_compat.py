"""Any server that speaks OpenAI chat completions: OpenAI itself, or an open model on vLLM.

One backend for both, so a comparison between an API model and a self-hosted one differs only
in config: `base_url`, `model` and where the key comes from (`api_key_env`; a local server
needs none). Usage maps to our token classes: cached prompt tokens are `cache_read_tokens`
and the rest of the prompt is `input_tokens`, so `Usage.prompt_tokens` is the server's
`prompt_tokens`. Reasoning tokens are already inside `completion_tokens`.

Retries: HTTP 408/409/429/5xx and transport errors, with exponential backoff (or the server's
Retry-After). Everything else fails the call with a `BackendError`.

Extra request fields for a specific server go in `extra_body`, e.g. for Qwen on vLLM
`extra_body = { chat_template_kwargs = { enable_thinking = false } }`.
"""

import contextlib
import os
import time
from collections.abc import Callable
from typing import Any

import httpx

from filings_rag.backends.base import BackendError, Result
from filings_rag.pricing import Usage

DEFAULT_BASE_URL = "https://api.openai.com/v1"
RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_STOP = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


class OpenAICompatBackend:
    name = "openai-compatible"

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key_env: str = "OPENAI_API_KEY",
        effort: str | None = None,
        max_tokens: int = 16_000,
        extra_body: dict[str, Any] | None = None,
        timeout: float = 600.0,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.extra_body = extra_body or {}
        self.max_retries = max_retries
        self._sleep = sleep
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        last = ""
        for attempt in range(self.max_retries + 1):
            wait = min(2.0**attempt, 30.0)
            try:
                resp = self._client.post("/chat/completions", json=body)
            except httpx.TransportError as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    if not isinstance(data, dict):
                        raise BackendError(f"unexpected reply: {resp.text[:200]!r}")
                    return data
                last = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code not in RETRY_STATUS:
                    raise BackendError(f"openai-compatible API error: {last}")
                with contextlib.suppress(KeyError, ValueError):
                    wait = min(float(resp.headers["retry-after"]), 120.0)
            if attempt < self.max_retries:
                self._sleep(wait)
        raise BackendError(f"openai-compatible API failed after {self.max_retries} retries: {last}")

    def generate(self, system: str, prompt: str) -> Result:
        body: dict[str, Any] = {
            "model": self.model,
            "max_completion_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if self.effort:
            body["reasoning_effort"] = self.effort
        body |= self.extra_body
        start = time.monotonic()
        data = self._post(body)
        latency = time.monotonic() - start
        try:
            choice = data["choices"][0]
            msg = choice["message"]
            u = data["usage"]
            cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            usage = Usage(
                input_tokens=u["prompt_tokens"] - cached,
                output_tokens=u["completion_tokens"],
                cache_read_tokens=cached,
            )
            finish = choice.get("finish_reason") or "stop"
        except (KeyError, IndexError, TypeError) as e:
            raise BackendError(
                f"malformed reply ({type(e).__name__}: {e}): {str(data)[:200]}"
            ) from e
        refusal = msg.get("refusal")
        return Result(
            text=msg.get("content") or refusal or "",
            usage=usage,
            latency_s=latency,
            model=str(data.get("model") or self.model),
            stop_reason="refusal" if refusal else _STOP.get(finish, finish),
        )
