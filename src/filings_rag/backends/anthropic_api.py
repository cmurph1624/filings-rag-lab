"""Claude via the Anthropic API (official SDK). Required for the public deployment.

Server-side refusal fallbacks are deliberately off: in a benchmark, a fallback would let a
different model answer under this run's model label. Refusals are recorded instead
(`Result.stop_reason == "refusal"`) and `Result.model` is the model that served the call.
"""

import time
from typing import Any

import anthropic

from filings_rag.backends.base import BackendError, Result
from filings_rag.pricing import Usage


class AnthropicApiBackend:
    name = "anthropic-api"

    def __init__(
        self,
        model: str,
        *,
        effort: str | None = None,
        max_tokens: int = 16_000,
        client: Any = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = client or anthropic.Anthropic()

    def generate(self, system: str, prompt: str) -> Result:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        start = time.monotonic()
        try:
            msg = self._client.messages.create(**kwargs)
        except anthropic.APIError as e:
            raise BackendError(f"anthropic API error: {e}") from e
        latency = time.monotonic() - start
        u = msg.usage
        writes = getattr(u, "cache_creation_input_tokens", 0) or 0
        split = getattr(u, "cache_creation", None)
        writes_1h = (getattr(split, "ephemeral_1h_input_tokens", 0) or 0) if split else 0
        return Result(
            text="".join(b.text for b in msg.content if b.type == "text"),
            usage=Usage(
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=writes - writes_1h,
                cache_write_1h_tokens=writes_1h,
            ),
            latency_s=latency,
            model=msg.model,
            stop_reason=str(msg.stop_reason),
        )
