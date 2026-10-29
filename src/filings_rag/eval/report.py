"""Markdown report for one run, or several side by side (`frl report A --vs B`)."""

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import psycopg

from filings_rag.eval import retrieval as rmetrics
from filings_rag.harness.config import from_dict
from filings_rag.pricing import Usage, batch_api_cost, load_prices, plain_api_cost
from filings_rag.retrieve.factory import resolve_spec

KS = (1, 3, 5, 10)
COST_VIEWS = ("as recorded", "plain API", "Batch API")


@dataclass
class RunSummary:
    run_id: int
    name: str
    config_hash: str
    git: str
    pipeline: str
    retriever: str
    generation: str
    judge: str
    total: int  # questions in the set
    answered: int = 0
    errors: int = 0
    verdicts: Counter[str] = field(default_factory=Counter)
    by_type: dict[str, Counter[str]] = field(default_factory=dict)
    retrieval: list[rmetrics.QuestionRetrieval] = field(default_factory=list)
    unlabelled: int = 0
    gen_cost: dict[str, float] = field(default_factory=lambda: dict.fromkeys(COST_VIEWS, 0.0))
    judge_cost: dict[str, float] = field(default_factory=lambda: dict.fromkeys(COST_VIEWS, 0.0))
    embed_cost: float = 0.0
    retrieve_s: list[float] = field(default_factory=list)
    generate_s: list[float] = field(default_factory=list)

    @property
    def correct(self) -> int:
        return self.verdicts["correct"]

    def system_cost(self, view: str) -> float:
        """What answering cost: generation plus query embedding. The judge is eval overhead."""
        return self.gen_cost[view] + self.embed_cost


def _views(model: str | None, usage: Usage, recorded: float) -> dict[str, float]:
    if model is None:
        return dict.fromkeys(COST_VIEWS, 0.0)
    return {
        "as recorded": recorded,
        "plain API": plain_api_cost(model, usage),
        "Batch API": batch_api_cost(model, usage),
    }


def _add(into: dict[str, float], more: dict[str, float]) -> None:
    for k, v in more.items():
        into[k] += v


_TOKENS = (
    "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cache_write_1h_tokens"
)


def summarize(conn: psycopg.Connection[Any], run_id: int) -> RunSummary:
    row = conn.execute(
        "SELECT name, config, config_hash, git_sha, git_dirty FROM runs WHERE id = %s", (run_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no run {run_id}")
    name, raw, config_hash, sha, dirty = row
    cfg = from_dict(raw)
    r = resolve_spec(cfg.retriever)
    g = cfg.generation
    total_row = conn.execute(
        "SELECT count(*) FROM questions WHERE benchmark = %s", (cfg.questions,)
    ).fetchone()
    s = RunSummary(
        run_id=run_id,
        name=name,
        config_hash=config_hash,
        git=sha[:10] + (" (dirty)" if dirty else ""),
        pipeline=cfg.pipeline,
        retriever=" ".join([r["kind"]] + [f"{k}={v}" for k, v in r.items() if k != "kind"]),
        generation=f"{g['backend']} {g['model']} {g['prompt']}",
        judge=cfg.judge_key,
        total=int(total_row[0]) if total_row else 0,
    )
    for qtype, n in conn.execute(
        "SELECT question_type, count(*) FROM questions WHERE benchmark = %s GROUP BY 1",
        (cfg.questions,),
    ):
        s.by_type[qtype] = Counter(n=n)  # like accuracy, by type counts every question in the set
    for _qtype, err, model, recorded, emb_cost, ret_s, gen_s, *tok in conn.execute(
        f"SELECT q.question_type, r.error, r.model, r.gen_cost_usd, r.embed_cost_usd,"
        f" r.retrieve_s, r.generate_s, {', '.join('r.' + t for t in _TOKENS.split(', '))}"
        " FROM run_questions r JOIN questions q ON q.id = r.question_id WHERE r.run_id = %s",
        (run_id,),
    ):
        if err is None:
            s.answered += 1
        else:
            s.errors += 1
        _add(s.gen_cost, _views(model, Usage(*tok), recorded))
        s.embed_cost += emb_cost
        if ret_s is not None and err is None:
            s.retrieve_s.append(ret_s)
        if gen_s is not None and err is None:
            s.generate_s.append(gen_s)
    for qtype, verdict, model, recorded, *tok in conn.execute(
        f"SELECT q.question_type, j.verdict, j.model, j.cost_usd, {_TOKENS}"
        " FROM judgments j JOIN questions q ON q.id = j.question_id"
        " WHERE j.run_id = %s AND j.judge = %s",
        (run_id, cfg.judge_key),
    ):
        if verdict is not None:
            s.verdicts[verdict] += 1
            s.by_type[qtype][verdict] += 1
        _add(s.judge_cost, _views(model, Usage(*tok), recorded))
    s.retrieval, s.unlabelled = rmetrics.load(conn, run_id, cfg.pipeline)
    conn.commit()
    return s


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for no data."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}% ({n}/{d})" if d else "–"


def _usd(x: float | None) -> str:
    return "–" if x is None else f"${x:.4f}" if x < 1 else f"${x:.2f}"


def _sec(x: float | None) -> str:
    return "–" if x is None else f"{x:.1f}s"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def render(runs: list[RunSummary]) -> str:
    heads = [""] + [f"run {s.run_id}" for s in runs]

    def rows(*spec: tuple[str, Any]) -> list[list[str]]:
        return [[label] + [fn(s) for s in runs] for label, fn in spec]

    title = runs[0].name if len(runs) == 1 else " vs ".join(s.name for s in runs)
    out = [
        f"# Run report: {title}",
        "",
        f"FinanceBench **open subset** ({runs[0].total} questions). These are not full-set "
        "FinanceBench results.",
        "",
        _table(
            heads,
            rows(
                ("config", lambda s: f"{s.name} `{s.config_hash}`"),
                ("git", lambda s: f"`{s.git}`"),
                ("pipeline", lambda s: s.pipeline),
                ("retriever", lambda s: s.retriever),
                ("generation", lambda s: s.generation),
                ("judge", lambda s: s.judge),
                ("answered", lambda s: f"{s.answered}/{s.total} ({s.errors} errors)"),
            ),
        ),
        "",
        "## Answers",
        "",
        "Accuracy counts every question in the set; unanswered or unjudged ones are not correct.",
        "",
        _table(
            heads,
            rows(
                ("accuracy", lambda s: _pct(s.correct, s.total)),
                ("incorrect", lambda s: str(s.verdicts["incorrect"])),
                ("refused", lambda s: str(s.verdicts["refused"])),
                ("not judged", lambda s: str(s.total - sum(s.verdicts.values()))),
            ),
        ),
        "",
        "By question type:",
        "",
    ]
    types = sorted({t for s in runs for t in s.by_type})
    out.append(
        _table(
            ["question type"] + heads[1:],
            [
                [t]
                + [
                    _pct(s.by_type.get(t, Counter())["correct"], s.by_type.get(t, Counter())["n"])
                    for s in runs
                ]
                for t in types
            ],
        )
    )
    out += [
        "",
        "## Retrieval",
        "",
        "Against FinanceBench evidence passages mapped to the pipeline's chunks. recall@k: share "
        "of a question's evidence passages found in the top k. hit@k: any passage found.",
        "",
        _table(
            heads,
            rows(
                *[
                    (f"recall@{k}", lambda s, k=k: f"{rmetrics.recall_at(s.retrieval, k):.3f}")
                    for k in KS
                ],
                ("hit@10", lambda s: f"{rmetrics.hit_at(s.retrieval, 10):.3f}"),
                ("MRR", lambda s: f"{rmetrics.mrr(s.retrieval):.3f}"),
                (
                    "questions scored",
                    lambda s: f"{len(s.retrieval)} ({s.unlabelled} without labels)",
                ),
            ),
        ),
        "",
        "## Cost",
        "",
        f"USD at list prices (`config/prices.toml`, as of {load_prices()['as_of']}), whoever "
        "paid. *As recorded* prices the tokens the backend actually used: `claude -p` writes "
        "every prompt to the 1-hour cache at 2× input. *Plain API* is the same tokens as an "
        "uncached API call; *Batch API* is that at the batch discount. Answering cost is "
        "generation plus query embedding; the judge is eval overhead and is shown on its own.",
        "",
    ]
    cost_rows: list[tuple[str, Any]] = []
    for v in COST_VIEWS:
        cost_rows += [
            (f"{v}: answering, total", lambda s, v=v: _usd(s.system_cost(v))),
            (
                f"{v}: per question",
                lambda s, v=v: _usd(s.system_cost(v) / s.answered if s.answered else None),
            ),
            (
                f"{v}: per correct answer",
                lambda s, v=v: _usd(s.system_cost(v) / s.correct if s.correct else None),
            ),
            (f"{v}: judge, total", lambda s, v=v: _usd(s.judge_cost[v])),
        ]
    out += [
        _table(heads, rows(*cost_rows)),
        "",
        "## Latency",
        "",
        _table(
            heads,
            rows(
                ("retrieval p50", lambda s: _sec(percentile(s.retrieve_s, 50))),
                ("retrieval p95", lambda s: _sec(percentile(s.retrieve_s, 95))),
                ("generation p50", lambda s: _sec(percentile(s.generate_s, 50))),
                ("generation p95", lambda s: _sec(percentile(s.generate_s, 95))),
            ),
        ),
        "",
    ]
    return "\n".join(out)
