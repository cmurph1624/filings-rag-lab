"""One interface for every generation (and judge) model."""

from dataclasses import dataclass
from typing import Protocol

from filings_rag.pricing import Usage


@dataclass(frozen=True)
class Result:
    text: str
    usage: Usage
    latency_s: float
    model: str  # the model that actually served the request
    stop_reason: str
    reported_cost_usd: float | None = None  # what the backend itself reports, for cross-checks

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


class BackendError(RuntimeError):
    pass


class ModelBackend(Protocol):
    name: str  # backend kind, e.g. "claude-cli"
    model: str

    def generate(self, system: str, prompt: str) -> Result:
        """Single-turn generation: system prompt + one user message."""
        ...
