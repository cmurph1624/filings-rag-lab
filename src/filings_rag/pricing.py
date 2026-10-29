"""API-equivalent cost from list prices in config/prices.toml."""

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

PRICES_PATH = Path(__file__).resolve().parents[2] / "config" / "prices.toml"
PER_TOKEN = 1 / 1_000_000


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0  # 5-minute cache writes
    cache_write_1h_tokens: int = 0  # 1-hour cache writes (claude -p uses these)

    @property
    def prompt_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.cache_write_1h_tokens
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.cache_write_1h_tokens + other.cache_write_1h_tokens,
        )


@lru_cache
def load_prices(path: Path = PRICES_PATH) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def model_cost(model: str, usage: Usage, prices: dict[str, Any] | None = None) -> float:
    """USD at list price. Unknown models raise rather than silently costing $0."""
    table = (prices or load_prices())["models"]
    if model not in table:
        raise KeyError(f"no price for model {model!r}; add it to config/prices.toml")
    p = table[model]
    long = "long_context_threshold" in p and usage.prompt_tokens > p["long_context_threshold"]
    rate_in = p["long_input"] if long else p["input"]
    rate_out = p["long_output"] if long else p["output"]
    return float(
        PER_TOKEN
        * (
            usage.input_tokens * rate_in
            + usage.output_tokens * rate_out
            + usage.cache_read_tokens * p.get("cache_read", rate_in)
            + usage.cache_write_tokens * p.get("cache_write", rate_in)
            + usage.cache_write_1h_tokens * p.get("cache_write_1h", rate_in)
        )
    )


def embedding_cost(model: str, tokens: int, prices: dict[str, Any] | None = None) -> float:
    table = (prices or load_prices())["embeddings"]
    if model not in table:
        raise KeyError(f"no price for embedding model {model!r}; add it to config/prices.toml")
    return float(PER_TOKEN * tokens * table[model]["input"])
