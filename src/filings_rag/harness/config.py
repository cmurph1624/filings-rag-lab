"""Run configs: one TOML file names everything that varies between experiments."""

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filings_rag import corpora
from filings_rag.retrieve.factory import resolve_spec

REQUIRED = {"name", "pipeline", "retriever"}
# No [generation] means a retrieval-only run: retrieval is scored, no model is called.
OPTIONAL = {"questions", "corpus", "execution", "generation", "judge"}
NOT_HASHED = {"name", "execution"}  # labels and how-to-run settings don't change results
EXECUTION_DEFAULTS: dict[str, int] = {"concurrency": 2, "max_consecutive_failures": 5}


@dataclass(frozen=True)
class RunConfig:
    name: str
    pipeline: str  # name@version
    corpus: str  # name@version of the filings searched
    retriever: dict[str, Any]
    generation: dict[str, Any] | None  # {backend, model, prompt, effort?}; None: retrieval only
    judge: dict[str, Any] | None  # {backend, model, prompt, effort?}; pinned per run
    questions: str
    execution: dict[str, int]
    raw: dict[str, Any] = field(repr=False)

    @property
    def hash(self) -> str:
        shaped = {k: v for k, v in self.raw.items() if k not in NOT_HASHED}
        shaped["questions"] = self.questions
        # The default corpus is what every config without the key searched, so leaving it out
        # keeps those (P0) hashes unchanged; any other corpus is hashed.
        shaped.pop("corpus", None)
        if self.corpus != corpora.DEFAULT:
            shaped["corpus"] = self.corpus
        shaped["retriever"] = resolve_spec(self.retriever)  # code defaults count too
        blob = json.dumps(shaped, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    @property
    def backend_spec(self) -> dict[str, Any] | None:
        if self.generation is None:
            return None
        return {k: v for k, v in self.generation.items() if k != "prompt"}

    @property
    def judge_spec(self) -> dict[str, Any] | None:
        if self.judge is None:
            return None
        return {k: v for k, v in self.judge.items() if k != "prompt"}

    @property
    def judge_key(self) -> str | None:
        """Identifies the judge's verdicts: backend, model, effort if set, and prompt."""
        j = self.judge
        if j is None:
            return None
        effort = f":{j['effort']}" if j.get("effort") else ""
        return f"{j['backend']}:{j['model']}{effort}:{j['prompt']}"


def from_dict(raw: dict[str, Any]) -> RunConfig:
    missing = REQUIRED - raw.keys()
    if missing:
        raise ValueError(f"run config is missing {sorted(missing)}")
    unknown = raw.keys() - REQUIRED - OPTIONAL
    if unknown:
        raise ValueError(f"run config has unknown keys {sorted(unknown)}")
    if ("judge" in raw) != ("generation" in raw):  # an unjudged run's spend could never be scored
        raise ValueError("[generation] and [judge] go together; omit both for a retrieval-only run")
    for table in ("generation", "judge"):
        for key in ("backend", "model", "prompt"):
            if table in raw and key not in raw[table]:
                raise ValueError(f"[{table}] needs {key!r}")
    resolve_spec(raw["retriever"])  # fail on a bad [retriever] at load time, not mid-run
    corpus = raw.get("corpus", corpora.DEFAULT)
    corpora.parse_ref(corpus)
    execution = dict(raw.get("execution", {}))
    bad = execution.keys() - EXECUTION_DEFAULTS.keys()
    if bad:
        raise ValueError(f"[execution] has unknown keys {sorted(bad)}")
    return RunConfig(
        name=raw["name"],
        pipeline=raw["pipeline"],
        corpus=corpus,
        retriever=dict(raw["retriever"]),
        generation=dict(raw["generation"]) if "generation" in raw else None,
        judge=dict(raw["judge"]) if "judge" in raw else None,
        questions=raw.get("questions", "financebench-open"),
        execution=EXECUTION_DEFAULTS | execution,
        raw=raw,
    )


def load(path: Path) -> RunConfig:
    with path.open("rb") as fh:
        return from_dict(tomllib.load(fh))
