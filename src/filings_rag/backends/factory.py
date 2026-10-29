from typing import Any

from filings_rag.backends.anthropic_api import AnthropicApiBackend
from filings_rag.backends.base import ModelBackend
from filings_rag.backends.claude_cli import ClaudeCliBackend


def make_backend(spec: dict[str, Any]) -> ModelBackend:
    """Build a backend from a run-config entry: {backend, model, effort?}."""
    kind = spec["backend"]
    opts = {k: v for k, v in spec.items() if k not in ("backend", "model")}
    if kind == "claude-cli":
        return ClaudeCliBackend(spec["model"], **opts)
    if kind == "anthropic-api":
        return AnthropicApiBackend(spec["model"], **opts)
    raise ValueError(f"unknown backend {kind!r}")
