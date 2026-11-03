"""Model calls for `synth-prose@1`: one reply per sampled chunk, kept in an append-only file so a
paused or interrupted generation resumes without paying twice."""

import json
from collections.abc import Callable
from pathlib import Path

from filings_rag.backends.base import ModelBackend, Result
from filings_rag.benchmark.synth import ProseCandidate
from filings_rag.eval.judge import JudgeStats, call_all
from filings_rag.harness.prompts import SynthPrompt
from filings_rag.pricing import model_cost


def read_raw(path: Path) -> dict[int, dict[str, object]]:
    """chunk id -> the stored reply record."""
    if not path.exists():
        return {}
    rows = (json.loads(ln) for ln in path.read_text().splitlines() if ln.strip())
    return {int(r["chunk_id"]): r for r in rows}


def generate(
    cands: list[ProseCandidate],
    raw_path: Path,
    backend: ModelBackend,
    prompt: SynthPrompt,
    *,
    form: str = "10-K",
    concurrency: int = 1,
    log: Callable[[str], None] = print,
) -> JudgeStats:
    """Ask for a question for each candidate without a stored reply. A usage pause stops
    cleanly (`stats.paused`); a failed call is counted and tried again next time."""
    done = read_raw(raw_path)
    by_id = {c.chunk_id: c for c in cands}
    todo = [c for c in cands if c.chunk_id not in done]
    stats = JudgeStats(skipped=len(cands) - len(todo))
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    jobs = [
        (
            str(c.chunk_id),
            prompt.system,
            prompt.render(c.company, form, c.year, c.section, c.text),
        )
        for c in todo
    ]

    def record(key: str, result: Result | None, error: str | None) -> None:
        if result is None:
            stats.failed += 1
            log(f"chunk {key}: {error}")
            return
        cost = model_cost(result.model, result.usage)
        stats.judged += 1
        stats.cost += cost
        row = {
            "chunk_id": int(key),
            "doc_name": by_id[int(key)].doc_name,
            "reply": result.text,
            "model": result.model,
            "cost_usd": cost,
        }
        with raw_path.open("a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if stats.judged % 10 == 0:
            log(f"{stats.judged}/{len(todo)} written (${stats.cost:.3f})")

    call_all(jobs, backend, record, stats, concurrency)
    return stats
