"""Eval noise (P3 PR 1): how much results move when nothing changes.

- **Generation noise:** the same config run several times, each judged by the pinned judge.
  Which questions change verdict, the accuracy spread, and pairwise fixed / broken counts.
- **Judge noise:** one run's answers judged several times by the same judge (replicates are
  stored under the judge key with "#rN", `frl judge --replicate`). Verdict agreement.
- **Retrieval determinism:** are the retrieved chunk ids, in order, the same across runs?
- **Fact determinism:** is the fact route's output the same across runs?

The numbers set the gate's thresholds: a gate tighter than this noise blocks good changes.
"""

from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import psycopg

from filings_rag.harness.config import from_dict


@dataclass
class RunVerdicts:
    run_id: int
    judge: str
    verdicts: dict[str, str]  # question id -> verdict
    retrieved: dict[str, list[int]] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def correct(self) -> int:
        return sum(v == "correct" for v in self.verdicts.values())


def load(conn: psycopg.Connection[Any], run_id: int, replicate: int = 1) -> RunVerdicts:
    row = conn.execute("SELECT config FROM runs WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"no run {run_id}")
    key = from_dict(row[0]).judge_key
    if key is None:
        raise ValueError(f"run {run_id} has no judge")
    judge = key if replicate == 1 else f"{key}#r{replicate}"
    verdicts = dict(
        conn.execute(
            "SELECT question_id, verdict FROM judgments"
            " WHERE run_id = %s AND judge = %s AND verdict IS NOT NULL",
            (run_id, judge),
        ).fetchall()
    )
    rv = RunVerdicts(run_id, judge, verdicts)
    for qid, retrieved, route in conn.execute(
        "SELECT question_id, retrieved, route FROM run_questions WHERE run_id = %s", (run_id,)
    ):
        rv.retrieved[qid] = [int(h["chunk_id"]) for h in retrieved or []]
        rv.facts[qid] = (route or {}).get("facts")
    return rv


@dataclass(frozen=True)
class Agreement:
    """Verdicts on the same questions from several sources (runs, or judge replicates)."""

    labels: list[str]
    accuracies: list[tuple[int, int]]  # (correct, judged) per source
    unstable: dict[str, list[str]]  # question id -> its verdict from each source
    pairwise: list[tuple[str, str, int, int]]  # (a, b, fixed, broken): b vs a
    common: int  # questions every source judged

    @property
    def agreement(self) -> float:
        return 1 - len(self.unstable) / self.common if self.common else 0.0


def agreement(sources: list[tuple[str, dict[str, str]]]) -> Agreement:
    labels = [name for name, _ in sources]
    common = set.intersection(*(set(v) for _, v in sources)) if sources else set()
    unstable = {
        q: [v[q] for _, v in sources] for q in sorted(common) if len({v[q] for _, v in sources}) > 1
    }
    accuracies = [(sum(x == "correct" for x in v.values()), len(v)) for _, v in sources]
    pairwise = []
    for (na, va), (nb, vb) in combinations(sources, 2):
        fixed = sum(va[q] != "correct" and vb[q] == "correct" for q in common)
        broken = sum(va[q] == "correct" and vb[q] != "correct" for q in common)
        pairwise.append((na, nb, fixed, broken))
    return Agreement(labels, accuracies, unstable, pairwise, len(common))


def identical_share(
    maps: list[dict[str, Any]], skip_empty: bool = False
) -> tuple[int, int, list[str]]:
    """(questions whose value is equal in every map, questions in all maps, the others).

    `skip_empty` leaves out questions that have no value in any map (e.g. no facts)."""
    common = set.intersection(*(set(m) for m in maps)) if maps else set()
    if skip_empty:
        common = {q for q in common if any(m[q] for m in maps)}
    differ = sorted(q for q in common if any(m[q] != maps[0][q] for m in maps[1:]))
    return len(common) - len(differ), len(common), differ


def flip_kinds(unstable: dict[str, list[str]]) -> Counter[str]:
    """How the unstable questions move: e.g. "correct/incorrect", "correct/refused"."""
    return Counter("/".join(sorted(set(vs))) for vs in unstable.values())


Share = tuple[int, int, list[str]]


def render(gen: Agreement, judge: Agreement | None, retrieval: Share, retrieval_sets: Share,
           facts: Share) -> str:  # fmt: skip
    def pct(a: int, b: int) -> str:
        return f"{100 * a / b:.1f}% ({a}/{b})" if b else "–"

    lines = ["# Eval noise", "", "## Generation (same config, separate runs, pinned judge)", ""]
    lines += ["| run | accuracy |", "|---|---|"]
    lines += [
        f"| {lab} | {pct(c, n)} |" for lab, (c, n) in zip(gen.labels, gen.accuracies, strict=True)
    ]
    accs = [c for c, _ in gen.accuracies]
    lines += [
        "",
        f"Spread: {max(accs) - min(accs)} questions. Verdict agreement across all runs:"
        f" {pct(gen.common - len(gen.unstable), gen.common)}; unstable questions:"
        f" {len(gen.unstable)} ({dict(flip_kinds(gen.unstable))}).",
        "",
        "| b vs a | fixed | broken | net |",
        "|---|---|---|---|",
    ]
    lines += [f"| {b} vs {a} | {f} | {br} | {f - br:+d} |" for a, b, f, br in gen.pairwise]
    if judge is not None:
        lines += [
            "",
            "## Judge (same answers, judged again)",
            "",
            "| judging | accuracy |",
            "|---|---|",
        ]
        lines += [
            f"| {lab} | {pct(c, n)} |"
            for lab, (c, n) in zip(judge.labels, judge.accuracies, strict=True)
        ]
        lines += [
            "",
            f"Verdict agreement: {pct(judge.common - len(judge.unstable), judge.common)};"
            f" unstable: {len(judge.unstable)} ({dict(flip_kinds(judge.unstable))}).",
        ]
    same, n, differ = retrieval
    ssame, sn, sdiffer = retrieval_sets
    fsame, fn, fdiffer = facts
    lines += [
        "",
        "## Determinism",
        "",
        f"- Retrieved top-k (chunk ids, in order) identical across runs: {pct(same, n)}"
        + (f"; differ: {', '.join(q[16:] for q in differ[:20])}" if differ else ""),
        f"- Retrieved top-k as a set (order ignored) identical across runs: {pct(ssame, sn)}"
        + (f"; differ: {', '.join(q[16:] for q in sdiffer[:20])}" if sdiffer else ""),
        f"- Fact route output identical across runs (questions that got facts):"
        f" {pct(fsame, fn)}"
        + (f"; differ: {', '.join(q[16:] for q in fdiffer[:20])}" if fdiffer else ""),
        "",
        "## Unstable questions",
        "",
        "| question | generation verdicts | judge verdicts |",
        "|---|---|---|",
    ]
    qs = sorted(set(gen.unstable) | set(judge.unstable if judge else {}))
    for q in qs:
        g = " / ".join(gen.unstable.get(q, [])) or "stable"
        j = " / ".join(judge.unstable.get(q, [])) if judge else "–"
        lines.append(f"| {q[16:]} | {g} | {j or 'stable'} |")
    return "\n".join(lines) + "\n"
