"""Run configs: one TOML file names everything that varies between experiments."""

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED = {"name", "pipeline", "retriever", "generation", "judge"}
OPTIONAL = {"questions", "execution"}
NOT_HASHED = {"name", "execution"}  # labels and how-to-run settings don't change results
EXECUTION_DEFAULTS: dict[str, int] = {"concurrency": 2, "max_consecutive_failures": 5}


@dataclass(frozen=True)
class RunConfig:
    name: str
    pipeline: str  # name@version
    retriever: dict[str, Any]
    generation: dict[str, Any]  # {backend, model, prompt, effort?}
    judge: dict[str, Any]  # pinned per run; used by the eval step
    questions: str
    execution: dict[str, int]
    raw: dict[str, Any] = field(repr=False)

    @property
    def hash(self) -> str:
        shaped = {k: v for k, v in self.raw.items() if k not in NOT_HASHED}
        shaped.setdefault("questions", self.questions)
        blob = json.dumps(shaped, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    @property
    def backend_spec(self) -> dict[str, Any]:
        return {k: v for k, v in self.generation.items() if k != "prompt"}


def from_dict(raw: dict[str, Any]) -> RunConfig:
    missing = REQUIRED - raw.keys()
    if missing:
        raise ValueError(f"run config is missing {sorted(missing)}")
    unknown = raw.keys() - REQUIRED - OPTIONAL
    if unknown:
        raise ValueError(f"run config has unknown keys {sorted(unknown)}")
    for key in ("backend", "model", "prompt"):
        if key not in raw["generation"]:
            raise ValueError(f"[generation] needs {key!r}")
    for key in ("backend", "model"):
        if key not in raw["judge"]:
            raise ValueError(f"[judge] needs {key!r}")
    execution = dict(raw.get("execution", {}))
    bad = execution.keys() - EXECUTION_DEFAULTS.keys()
    if bad:
        raise ValueError(f"[execution] has unknown keys {sorted(bad)}")
    return RunConfig(
        name=raw["name"],
        pipeline=raw["pipeline"],
        retriever=dict(raw["retriever"]),
        generation=dict(raw["generation"]),
        judge=dict(raw["judge"]),
        questions=raw.get("questions", "financebench-open"),
        execution=EXECUTION_DEFAULTS | execution,
        raw=raw,
    )


def load(path: Path) -> RunConfig:
    with path.open("rb") as fh:
        return from_dict(tomllib.load(fh))
