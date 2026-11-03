"""The retrieval gate (P3 PR 3): compare a candidate retrieval-only run with a pinned baseline.

`config/gate.toml` names the gated configs, each with its baseline run, and the allowed drop
per metric. The candidate is the same config, optionally with another pipeline, run at the
current code. Metrics are computed over the baseline's labelled evidence passages. A passage
the candidate's pipeline can't label (its chunks don't hold that evidence any more) counts as
not found, and so does a question with none left: dropping them instead would let a broken
chunker raise recall by shrinking the pool.

recall@k favours bigger chunks (more text per chunk holds evidence more often), so the verdict
also shows how many words of context each run retrieves, and flags a candidate that retrieves
much more: its recall gain may not be a better system, and it needs an answer eval.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from filings_rag.eval import retrieval as rmetrics
from filings_rag.eval.routes import RouteLabel
from filings_rag.harness.config import from_dict
from filings_rag.harness.run import pipeline_index
from filings_rag.route import evaluate as route_eval

METRICS = ("recall_at_10", "hit_at_10", "mrr", "fact_correct")
MORE_CONTEXT = 1.25  # flag a candidate retrieving this much more text than the baseline


@dataclass(frozen=True)
class Check:
    config: Path
    baseline: int


@dataclass(frozen=True)
class GateConfig:
    corpus: str
    checks: list[Check]
    allowed_drop: dict[str, float]


def load_config(path: Path, root: Path) -> GateConfig:
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    allowed = dict(raw["allowed_drop"])
    if bad := allowed.keys() - set(METRICS):
        raise ValueError(f"[allowed_drop] has unknown metrics {sorted(bad)}; known: {METRICS}")
    if missing := set(METRICS) - allowed.keys():
        raise ValueError(f"[allowed_drop] is missing {sorted(missing)}")
    checks = [Check(root / c["config"], int(c["baseline"])) for c in raw["check"]]
    if not checks:
        raise ValueError("no [[check]] in the gate config")
    return GateConfig(raw["corpus"], checks, {k: float(v) for k, v in allowed.items()})


@dataclass
class Measured:
    run_id: int
    pipeline: str
    retrieval: dict[str, rmetrics.QuestionRetrieval]
    facts: dict[str, bool] | None  # question id -> judge-free fact check ok; None: no router
    context_words: float = 0.0  # mean words retrieved per question
    questions: str = "financebench-open"  # the question set the run used


def measure(conn: psycopg.Connection[Any], run_id: int, labels: dict[str, RouteLabel]) -> Measured:
    row = conn.execute("SELECT config FROM runs WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"no run {run_id}")
    cfg = from_dict(row[0])
    errors = conn.execute(
        "SELECT count(*) FROM run_questions WHERE run_id = %s AND error IS NOT NULL", (run_id,)
    ).fetchone()
    if errors and errors[0]:
        raise ValueError(f"run {run_id} has {errors[0]} failed questions")
    scored, _ = rmetrics.load(conn, run_id, cfg.pipeline)
    facts = None
    if cfg.router is not None:
        facts = {c.question_id: c.ok for c in route_eval.fact_route_accuracy(conn, run_id, labels)}
    table = pipeline_index(conn, cfg.pipeline)[0]
    words = conn.execute(
        sql.SQL(
            r"""SELECT avg(w) FROM (
                 SELECT r.question_id,
                        sum(array_length(regexp_split_to_array(btrim(c.text), '\s+'), 1)) w
                 FROM run_questions r CROSS JOIN jsonb_array_elements(r.retrieved) h
                 JOIN {t} c ON c.id = (h->>'chunk_id')::bigint
                 WHERE r.run_id = %s GROUP BY 1) x"""
        ).format(t=sql.Identifier(table)),
        (run_id,),
    ).fetchone()
    context = float(words[0]) if words and words[0] is not None else 0.0
    return Measured(
        run_id, cfg.pipeline, {q.question_id: q for q in scored}, facts, context, cfg.questions
    )


@dataclass(frozen=True)
class Row:
    metric: str
    baseline: float
    candidate: float
    allowed_drop: float

    @property
    def ok(self) -> bool:
        return self.baseline - self.candidate <= self.allowed_drop + 1e-9


@dataclass
class CheckResult:
    name: str
    baseline: Measured
    candidate: Measured
    rows: list[Row]
    lost: list[tuple[str, int | None, int | None]] = field(default_factory=list)  # qid, ranks
    unscored: list[str] = field(default_factory=list)  # scored in the baseline, not candidate
    passages_lost: int = 0  # baseline-labelled passages the candidate couldn't label
    facts_lost: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.rows)


def compare(name: str, base: Measured, cand: Measured, allowed: dict[str, float]) -> CheckResult:
    qids = sorted(base.retrieval)
    unscored = [q for q in qids if q not in cand.retrieval]
    b = [base.retrieval[q] for q in qids]
    c = [_align(base.retrieval[q], cand.retrieval.get(q)) for q in qids]
    passages_lost = sum(
        len(x.found_at) - _labelled(x, cand.retrieval.get(x.question_id)) for x in b
    )
    rows = [
        Row("recall_at_10", rmetrics.recall_at(b, 10), rmetrics.recall_at(c, 10),
            allowed["recall_at_10"]),
        Row("hit_at_10", rmetrics.hit_at(b, 10), rmetrics.hit_at(c, 10), allowed["hit_at_10"]),
        Row("mrr", rmetrics.mrr(b), rmetrics.mrr(c), allowed["mrr"]),
    ]  # fmt: skip
    lost = [
        (q, x.first_rank, y.first_rank)
        for q, x, y in zip(qids, b, c, strict=True)
        if (x.first_rank or 99) <= 10 < (y.first_rank or 99)
    ]
    facts_lost: list[str] = []
    if base.facts is not None:
        cf = cand.facts or {}
        facts_lost = sorted(q for q, ok in base.facts.items() if ok and not cf.get(q, False))
        b_ok = sum(base.facts.values())
        c_ok = sum(cf.get(q, False) for q in base.facts)
        rows.append(Row("fact_correct", b_ok, c_ok, allowed["fact_correct"]))
    return CheckResult(
        name, base, cand, rows, lost=lost, unscored=unscored, passages_lost=passages_lost,
        facts_lost=facts_lost,
    )  # fmt: skip


def _align(
    base: rmetrics.QuestionRetrieval, cand: rmetrics.QuestionRetrieval | None
) -> rmetrics.QuestionRetrieval:
    """The candidate's ranks for the baseline's passages, None where it has no label."""
    if cand is None:
        return rmetrics.QuestionRetrieval(base.question_id, [None] * len(base.found_at))
    if base.passages and cand.passages:
        rank = dict(zip(cand.passages, cand.found_at, strict=True))
        return rmetrics.QuestionRetrieval(
            base.question_id, [rank.get(i) for i in base.passages], base.passages
        )
    pad = max(0, len(base.found_at) - len(cand.found_at))  # no passage ids: by position
    return rmetrics.QuestionRetrieval(base.question_id, cand.found_at + [None] * pad)


def _labelled(base: rmetrics.QuestionRetrieval, cand: rmetrics.QuestionRetrieval | None) -> int:
    """How many of the baseline's passages the candidate has labels for."""
    if cand is None:
        return 0
    if base.passages and cand.passages:
        return len(set(base.passages) & set(cand.passages))
    return min(len(base.found_at), len(cand.found_at))


def _short(question_id: str) -> str:
    return question_id.removeprefix("financebench_id_")


def _set_line(questions: str) -> str:
    if questions == "financebench-open":
        return "FinanceBench open subset (150 questions)."
    return f"Synthetic set `{questions}` (`frl bench`), kept apart from the open subset."


def _fmt(metric: str, v: float) -> str:
    return f"{v:.0f}" if metric == "fact_correct" else f"{v:.3f}"


def render(results: list[CheckResult], corpus: str) -> str:
    ok = all(r.ok for r in results)
    lines = [
        f"## Retrieval gate: {'PASS' if ok else 'FAIL'}",
        "",
        f"Retrieval-only, corpus `{corpus}`."
        " Thresholds are in `config/gate.toml`, from docs/results/p3-noise.md.",
    ]
    for r in results:
        lines += [
            "",
            f"### {r.name}: {'pass' if r.ok else 'FAIL'}",
            "",
            _set_line(r.baseline.questions),
            "",
            f"Baseline run {r.baseline.run_id} (`{r.baseline.pipeline}`), candidate run"
            f" {r.candidate.run_id} (`{r.candidate.pipeline}`), over the baseline's"
            f" {len(r.baseline.retrieval)} scored questions.",
            "",
            "| metric | baseline | candidate | change | allowed drop | |",
            "|---|---|---|---|---|---|",
        ]
        for row in r.rows:
            d = row.candidate - row.baseline
            change = f"{d:+.0f}" if row.metric == "fact_correct" else f"{d:+.3f}"
            lines.append(
                f"| {row.metric} | {_fmt(row.metric, row.baseline)} |"
                f" {_fmt(row.metric, row.candidate)} | {change} |"
                f" {_fmt(row.metric, row.allowed_drop)} | {'ok' if row.ok else '**breach**'} |"
            )
        bw, cw = r.baseline.context_words, r.candidate.context_words
        lines += [
            "",
            f"Context retrieved per question: baseline {bw:,.0f} words, candidate {cw:,.0f}.",
        ]
        if bw and cw > MORE_CONTEXT * bw:
            lines.append(
                f"The candidate retrieves {cw / bw:.1f}× the text. recall@k favours bigger"
                " chunks, so a recall gain here may not mean better answers: run the answer"
                " eval (`eval:full`) before trusting it."
            )
        if r.passages_lost:
            n = sum(len(q.found_at) for q in r.baseline.retrieval.values())
            lines += [
                "",
                f"{r.passages_lost} of the baseline's {n} labelled evidence passages have no"
                " label in the candidate's pipeline (counted as not found).",
            ]
        if r.unscored:
            lines += [
                "",
                f"{len(r.unscored)} questions have no labelled evidence in the candidate's"
                " pipeline (counted as not found): "
                + ", ".join(_short(q) for q in r.unscored[:30]),
            ]
        if r.lost:
            lines += [
                "",
                f"Lost evidence from the top 10 ({len(r.lost)}):"
                " question (baseline rank → candidate rank, – = not in the top 10): "
                + ", ".join(f"{_short(q)} ({x} → {y or '–'})" for q, x, y in r.lost[:30]),
            ]
        if r.facts_lost:
            lines += ["", "Fact checks lost: " + ", ".join(_short(q) for q in r.facts_lost)]
    return "\n".join(lines) + "\n"
