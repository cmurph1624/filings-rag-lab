"""The serving config: a benchmark run config with [generation] swapped, plus [serve].

`base` names the run config whose pipeline is served, so the deployed system can't drift from
the benchmarked one: only the answer model's backend changes (claude-cli is for local
benchmark runs; the public deployment uses the Anthropic API).
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filings_rag.harness.answer import REPO_ROOT
from filings_rag.harness.config import RunConfig, from_dict
from filings_rag.serve.limits import LIMIT_DEFAULTS

SERVE_DEFAULTS: dict[str, Any] = {
    "max_question_chars": 500,
    "refuse_unscoped": True,  # no company found: refuse without a model call
} | LIMIT_DEFAULTS
LOCAL_ONLY_BACKENDS = {"claude-cli"}


@dataclass(frozen=True)
class ServeConfig:
    run: RunConfig
    base: str
    serve: dict[str, Any] = field(default_factory=dict)

    @property
    def max_question_chars(self) -> int:
        return int(self.serve["max_question_chars"])

    @property
    def refuse_unscoped(self) -> bool:
        return bool(self.serve["refuse_unscoped"])


def from_dict_serve(raw: dict[str, Any], root: Path = REPO_ROOT) -> ServeConfig:
    unknown = raw.keys() - {"base", "generation", "serve"}
    if unknown:
        raise ValueError(f"serve config has unknown keys {sorted(unknown)}")
    if "base" not in raw or "generation" not in raw:
        raise ValueError("serve config needs `base` (a run config) and [generation]")
    with (root / raw["base"]).open("rb") as fh:
        base = tomllib.load(fh)
    if "generation" not in base:
        raise ValueError(f"{raw['base']} is retrieval-only; serve an answering config")
    gen = dict(raw["generation"])
    if gen.get("backend") in LOCAL_ONLY_BACKENDS:
        raise ValueError(f"backend {gen['backend']!r} is for local benchmark runs, not serving")
    serve = dict(raw.get("serve", {}))
    bad = serve.keys() - SERVE_DEFAULTS.keys()
    if bad:
        raise ValueError(f"[serve] has unknown keys {sorted(bad)}")
    run = from_dict(base | {"generation": gen, "name": base["name"] + "+serve"})
    return ServeConfig(run, raw["base"], SERVE_DEFAULTS | serve)


def load(path: Path, root: Path = REPO_ROOT) -> ServeConfig:
    with path.open("rb") as fh:
        return from_dict_serve(tomllib.load(fh), root)
