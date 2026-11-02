"""Markdown report for one run, or several side by side (`frl report A --vs B`)."""

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

from filings_rag.eval import faithful, periods
from filings_rag.eval import retrieval as rmetrics
from filings_rag.eval.routes import VERSION as ROUTES_VERSION
from filings_rag.eval.routes import RouteLabel
from filings_rag.harness.config import from_dict
from filings_rag.harness.prompts import citations
from filings_rag.ingest.registry import get_pipeline
from filings_rag.pricing import Usage, batch_api_cost, load_prices, plain_api_cost, rerank_cost
from filings_rag.retrieve.factory import resolve_spec

KS = (1, 3, 5, 10)
COMPANIES = Path(__file__).resolve().parents[3] / "corpus" / "companies.csv"
COST_VIEWS = ("as recorded", "plain API", "Batch API")


@dataclass
class RunSummary:
    run_id: int
    name: str
    config_hash: str
    git: str
    pipeline: str
    corpus: str
    retriever: str
    scope: str  # "none", or the mode and its options
    rerank: str  # "none", or model and candidates
    generation: str  # "–" for a retrieval-only run
    judge: str
    total: int  # questions in the set
    generated: bool = True
    answered: int = 0
    errors: int = 0
    verdicts: Counter[str] = field(default_factory=Counter)
    by_type: dict[str, Counter[str]] = field(default_factory=dict)
    verdict_of: dict[str, str] = field(default_factory=dict)  # question id -> judged verdict
    retrieval: list[rmetrics.QuestionRetrieval] = field(default_factory=list)
    unlabelled: int = 0
    gen_cost: dict[str, float] = field(default_factory=lambda: dict.fromkeys(COST_VIEWS, 0.0))
    judge_cost: dict[str, float] = field(default_factory=lambda: dict.fromkeys(COST_VIEWS, 0.0))
    embed_cost: float = 0.0
    aux_cost: dict[str, float] = field(default_factory=lambda: dict.fromkeys(COST_VIEWS, 0.0))
    component_cost: dict[str, float] = field(default_factory=dict)  # plain API, by component
    scopes: list[tuple[bool, str, int]] = field(default_factory=list)  # gold in, level, filings
    citing: int = 0  # answers that cite at least one excerpt
    cited: int = 0  # cited excerpts, over questions with labelled evidence
    cited_labelled: int = 0  # of those, excerpts that hold labelled evidence
    retrieve_s: list[float] = field(default_factory=list)
    generate_s: list[float] = field(default_factory=list)
    period_of: dict[str, str] = field(default_factory=dict)  # question id -> right/wrong/none
    faithful_judge: str = "–"
    faith: dict[str, tuple[int, int, int]] = field(default_factory=dict)  # sup, unsup, contra
    wall_s: float | None = None  # started_at to finished_at, for a run that has finished
    gpu_rate: float | None = None  # `gpu_usd_per_hour` in [generation], for a self-hosted model

    @property
    def correct(self) -> int:
        return self.verdicts["correct"]

    def system_cost(self, view: str) -> float:
        """What answering cost: generation, query embedding and other models (the scope
        model). The judge is eval overhead."""
        return self.gen_cost[view] + self.embed_cost + self.aux_cost[view]

    @property
    def gpu_cost(self) -> float | None:
        """GPU rental for the run's wall time. None unless the config gives a rate."""
        if self.gpu_rate is None or self.wall_s is None:
            return None
        return self.gpu_rate * self.wall_s / 3600

    def gpu_system_cost(self) -> float | None:
        """Answering cost with generation costed as GPU time; the other models at plain API."""
        gpu = self.gpu_cost
        return None if gpu is None else gpu + self.embed_cost + self.aux_cost["plain API"]


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


def citation_counts(
    answers: list[tuple[str, str, list[dict[str, Any]]]], labelled: dict[str, set[int]]
) -> tuple[int, int, int]:
    """(answers citing an excerpt, cited excerpts over labelled questions, of those labelled).

    `answers`: (question_id, answer, retrieved hits in rank order) per answered question."""
    citing = cited = good = 0
    for qid, answer, retrieved in answers:
        ks = citations(answer, len(retrieved))
        citing += bool(ks)
        if qid in labelled:
            cited += len(ks)
            good += sum(retrieved[k - 1]["chunk_id"] in labelled[qid] for k in ks)
    return citing, cited, good


def summarize(
    conn: psycopg.Connection[Any],
    run_id: int,
    routes: dict[str, RouteLabel] | None = None,
    companies_csv: Path = COMPANIES,
) -> RunSummary:
    """`routes` (hand route labels) adds fiscal-period correctness."""
    row = conn.execute(
        "SELECT name, config, config_hash, git_sha, git_dirty, started_at, finished_at"
        " FROM runs WHERE id = %s",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no run {run_id}")
    name, raw, config_hash, sha, dirty, started, finished = row
    cfg = from_dict(raw)
    r = resolve_spec(cfg.retriever)
    sc = r.pop("scope", None)
    rr = r.pop("rerank", None)
    g = cfg.generation
    generated = g is not None
    total_row = conn.execute(
        "SELECT count(*) FROM questions WHERE benchmark = %s", (cfg.questions,)
    ).fetchone()
    s = RunSummary(
        run_id=run_id,
        name=name,
        config_hash=config_hash,
        git=sha[:10] + (" (dirty)" if dirty else ""),
        pipeline=cfg.pipeline,
        corpus=cfg.corpus,
        retriever=" ".join([r["kind"]] + [f"{k}={v}" for k, v in r.items() if k != "kind"]),
        scope=" ".join([sc["mode"]] + [f"{k}={v}" for k, v in sc.items() if k != "mode"])
        if sc
        else "none",
        rerank=f"{rr['model']} over {rr['candidates']} candidates" if rr else "none",
        generation=f"{g['backend']} {g['model']} {g['prompt']}" if g else "– (retrieval only)",
        judge=cfg.judge_key or "–",
        generated=generated,
        total=int(total_row[0]) if total_row else 0,
    )
    if g and g.get("gpu_usd_per_hour") is not None:
        s.gpu_rate = float(g["gpu_usd_per_hour"])
        s.wall_s = (finished - started).total_seconds() if finished else None
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
    for qid, qtype, verdict, model, recorded, *tok in conn.execute(
        f"SELECT q.id, q.question_type, j.verdict, j.model, j.cost_usd, {_TOKENS}"
        " FROM judgments j JOIN questions q ON q.id = j.question_id"
        " WHERE j.run_id = %s AND j.judge = %s",
        (run_id, cfg.judge_key or ""),
    ):
        if verdict is not None:
            s.verdicts[verdict] += 1
            s.by_type[qtype][verdict] += 1
            s.verdict_of[qid] = verdict
        _add(s.judge_cost, _views(model, Usage(*tok), recorded))
    for gold, scope, aux in conn.execute(
        "SELECT q.doc_name, r.scope, r.aux FROM run_questions r"
        " JOIN questions q ON q.id = r.question_id WHERE r.run_id = %s",
        (run_id,),
    ):
        for component, a in aux.items():  # spend is recorded on failed rows too
            cost = float(a["cost_usd"])
            # Model calls get the three views; a reranker bills tokens at one rate in all views.
            # A rerank served from the cache billed nothing (as recorded) but is priced in the
            # comparable views, so runs compare as deployed systems.
            if "usage" in a:
                views = _views(a["model"], Usage(**a["usage"]), cost)
            else:
                full = rerank_cost(a["model"], a["tokens"] + a.get("cached_tokens", 0))
                views = {**dict.fromkeys(COST_VIEWS, full), "as recorded": cost}
            _add(s.aux_cost, views)
            s.component_cost[component] = s.component_cost.get(component, 0.0) + views["plain API"]
        if scope is not None:
            level = scope["level"]
            s.scopes.append((level == "corpus" or gold in scope["docs"], level, len(scope["docs"])))
    if generated:
        p = get_pipeline(cfg.pipeline)
        labelled: dict[str, set[int]] = {}
        for qid, cid in conn.execute(
            "SELECT l.question_id, l.chunk_id FROM evidence_labels l"
            " JOIN run_questions r ON r.question_id = l.question_id AND r.run_id = %s"
            " WHERE l.pipeline = %s AND l.version = %s",
            (run_id, p.name, p.version),
        ):
            labelled.setdefault(qid, set()).add(cid)
        answers = conn.execute(
            "SELECT question_id, answer, retrieved FROM run_questions"
            " WHERE run_id = %s AND error IS NULL AND answer IS NOT NULL",
            (run_id,),
        ).fetchall()
        s.citing, s.cited, s.cited_labelled = citation_counts(answers, labelled)
        if routes:
            expected = periods.expectations(conn, [a[0] for a in answers], routes, companies_csv)
            s.period_of = {
                qid: periods.classify(answer, expected[qid])
                for qid, answer, _ in answers
                if qid in expected and s.verdict_of.get(qid) != "refused"
            }
        if cfg.judge_key is not None:
            s.faithful_judge = faithful.judge_key(cfg)
            for qid, *n in conn.execute(
                "SELECT question_id, supported, unsupported, contradicted FROM faithfulness"
                " WHERE run_id = %s AND judge = %s AND claims IS NOT NULL",
                (run_id, s.faithful_judge),
            ):
                if s.verdict_of.get(qid) != "refused":  # a refusal has no claims to check
                    s.faith[qid] = (n[0], n[1], n[2])
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


def _hours(x: float | None) -> str:
    return "–" if x is None else f"{x / 3600:.2f} h"


def _div(x: float | None, n: int) -> float | None:
    return x / n if x is not None and n else None


def _sec(x: float | None) -> str:
    return "–" if x is None else f"{x:.1f}s"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def render(
    runs: list[RunSummary],
    *,
    retrieval_only: bool = False,
    routes: dict[str, RouteLabel] | None = None,
) -> str:
    """Markdown for one or more runs. `retrieval_only` keeps just identity, retrieval, latency.
    `routes` (hand route labels) adds accuracy by route and by multi-period."""
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
                ("corpus", lambda s: s.corpus),
                ("retriever", lambda s: s.retriever),
                ("scope", lambda s: s.scope),
                ("rerank", lambda s: s.rerank),
                ("generation", lambda s: s.generation),
                ("judge", lambda s: s.judge),
                ("answered", lambda s: f"{s.answered}/{s.total} ({s.errors} errors)"),
            ),
        ),
        "",
    ]
    if not retrieval_only:
        out += _answers(runs, heads, rows)
        if routes:
            out += _by_route(runs, heads, routes)
        out += _generation(heads, rows)
    out += _retrieval(runs, heads, rows)
    if any(s.scopes for s in runs):
        out += _scoping(heads, rows)
    if not retrieval_only:
        out += _costs(runs, heads, rows)
    latency: list[tuple[str, Any]] = [
        ("retrieval p50", lambda s: _sec(percentile(s.retrieve_s, 50))),
        ("retrieval p95", lambda s: _sec(percentile(s.retrieve_s, 95))),
    ]
    if not retrieval_only:
        latency += [
            ("generation p50", lambda s: _sec(percentile(s.generate_s, 50))),
            ("generation p95", lambda s: _sec(percentile(s.generate_s, 95))),
        ]
    out += ["## Latency", "", _table(heads, rows(*latency)), ""]
    return "\n".join(out)


Rows = Any  # the `rows` helper inside render


def _gen(fn: Any) -> Any:
    """Answer and cost cells read "–" for a retrieval-only run instead of zeros."""
    return lambda s: fn(s) if s.generated else "–"


def _answers(runs: list[RunSummary], heads: list[str], rows: Rows) -> list[str]:
    out = [
        "## Answers",
        "",
        "Accuracy counts every question in the set; unanswered or unjudged ones are not correct.",
        "",
        _table(
            heads,
            rows(
                ("accuracy", _gen(lambda s: _pct(s.correct, s.total))),
                ("incorrect", _gen(lambda s: str(s.verdicts["incorrect"]))),
                ("refused", _gen(lambda s: str(s.verdicts["refused"]))),
                ("not judged", _gen(lambda s: str(s.total - sum(s.verdicts.values())))),
                ("answers citing excerpts", _gen(lambda s: _pct(s.citing, s.answered))),
                (
                    "citation precision",
                    _gen(lambda s: _pct(s.cited_labelled, s.cited) if s.cited else "–"),
                ),
            ),
        ),
        "",
        "*Citation precision*: the share of excerpts cited in answers (prompts that ask for "
        "citations, e.g. answer@2) that hold the question's labelled evidence, over questions "
        "with labels. Like recall it is a proxy: an excerpt that repeats the figure also counts "
        "as useful to the model but not here.",
        "",
        "By question type:",
        "",
    ]
    types = sorted({t for s in runs for t in s.by_type})

    def by_type(s: RunSummary, t: str) -> str:
        c = s.by_type.get(t, Counter())
        return _pct(c["correct"], c["n"]) if s.generated else "–"

    out.append(
        _table(["question type"] + heads[1:], [[t] + [by_type(s, t) for s in runs] for t in types])
    )
    out.append("")
    return out


def subset_accuracy(s: RunSummary, qids: list[str]) -> str:
    """Correct over every question in the subset, like headline accuracy."""
    return _pct(sum(s.verdict_of.get(q) == "correct" for q in qids), len(qids))


def _by_route(runs: list[RunSummary], heads: list[str], routes: dict[str, RouteLabel]) -> list[str]:
    groups: list[tuple[str, list[str]]] = [
        (f"route: {r}", [q for q, lab in routes.items() if lab.route == r])
        for r in ("fact", "mixed", "prose")
    ] + [
        ("multi-period", [q for q, lab in routes.items() if lab.multi_period]),
        ("single period", [q for q, lab in routes.items() if not lab.multi_period]),
    ]
    body = [
        [f"{name} ({len(qids)})"] + [_gen(lambda s, q=qids: subset_accuracy(s, q))(s) for s in runs]
        for name, qids in groups
    ]
    return [
        f"By hand route label (`{ROUTES_VERSION}`, `corpus/route_labels.csv`):",
        "",
        _table(["subset"] + heads[1:], body),
        "",
    ]


def _generation(heads: list[str], rows: Rows) -> list[str]:
    def period(v: str) -> Any:
        return _gen(
            lambda s: (
                _pct(sum(x == v for x in s.period_of.values()), len(s.period_of))
                if s.period_of
                else "–"
            )
        )

    def claims(s: RunSummary) -> tuple[int, int, int]:
        sup, uns, con = (sum(n[i] for n in s.faith.values()) for i in range(3))
        return sup, uns, con

    def _not_refused(s: RunSummary) -> int:
        return s.answered - sum(v == "refused" for v in s.verdict_of.values())

    def faith(fn: Any) -> Any:
        return _gen(lambda s: fn(s) if s.faith else "–")

    return [
        "## Generation",
        "",
        "*Fiscal period* (`eval/periods.py`, deterministic): whether the answer states the "
        "period the question is about, the latest of its `routes@1` periods, in any of its "
        "forms (FinanceBench label, the company's own year name, a date in the period). "
        "*Wrong*: it states periods but not that one. Over answers not judged refused.",
        "",
        "*Faithfulness* (`faithful@1`, the run's judge model): each checkable claim in the "
        "answer, labelled against the context the generator was given (excerpts and Facts), "
        "not against the gold answer. Over answers not judged refused.",
        "",
        _table(
            heads,
            rows(
                ("period: right", period("right")),
                ("period: wrong", period("wrong")),
                ("period: none stated", period("none")),
                (
                    "faithfulness: answers checked",
                    faith(lambda s: _pct(len(s.faith), _not_refused(s))),
                ),
                (
                    "claims supported",
                    faith(lambda s: _pct(claims(s)[0], sum(claims(s)))),
                ),
                ("claims unsupported", faith(lambda s: str(claims(s)[1]))),
                ("claims contradicted", faith(lambda s: str(claims(s)[2]))),
                (
                    "answers with a contradicted claim",
                    faith(lambda s: _pct(sum(n[2] > 0 for n in s.faith.values()), len(s.faith))),
                ),
                (
                    "answers fully supported",
                    faith(
                        lambda s: _pct(
                            sum(n[1] == n[2] == 0 for n in s.faith.values()), len(s.faith)
                        )
                    ),
                ),
            ),
        ),
        "",
    ]


def _retrieval(runs: list[RunSummary], heads: list[str], rows: Rows) -> list[str]:
    return [
        "## Retrieval",
        "",
        "Against FinanceBench evidence passages mapped to the pipeline's chunks. recall@k: share "
        "of a question's evidence passages found in the top k. hit@k: any passage found. These "
        "are a proxy: an answer can also come from a passage that repeats the same figure, and "
        "a partial overlap can count as a hit (see docs/results/p0-label-audit.md).",
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
                (
                    "rerank cost (list)",
                    lambda s: (
                        _usd(s.component_cost["rerank"]) if "rerank" in s.component_cost else "–"
                    ),
                ),
            ),
        ),
        "",
    ]


def _scoping(heads: list[str], rows: Rows) -> list[str]:
    def count(level: str) -> Any:
        return lambda s: _pct(sum(1 for _, lv, _ in s.scopes if lv == level), len(s.scopes))

    def median_filings(s: RunSummary) -> str:
        n = [k for _, lv, k in s.scopes if lv != "corpus"]
        return f"{percentile([float(x) for x in n], 50):.0f}" if n else "–"

    return [
        "## Scoping",
        "",
        "How each question's search was narrowed before retrieval. *Filings*: the company's "
        "filings for the periods the question names. *Latest year*: the question names no "
        "period, so the company's filings from its latest labelled year (`no_year = 'latest'`). "
        "*Company*: no filing matched the periods, "
        "so all of the company's filings (a fallback). *Whole corpus*: no company found. "
        "*Gold filing in scope*: the question's own filing was searchable (always true for the "
        "whole corpus). Oracle scope searches only the gold filing: an upper bound, not a result.",
        "",
        _table(
            heads,
            rows(
                ("narrowed to filings", count("filings")),
                ("no period named: latest year", count("latest")),
                ("fell back to company", count("company")),
                ("whole corpus", count("corpus")),
                ("oracle", count("oracle")),
                (
                    "gold filing in scope",
                    lambda s: (
                        _pct(sum(1 for g, _, _ in s.scopes if g), len(s.scopes))
                        if s.scopes
                        else "–"
                    ),
                ),
                ("filings in scope, median", median_filings),
                (
                    "scope model cost (plain API)",
                    lambda s: _usd(s.component_cost.get("scope", 0.0)) if s.scopes else "–",
                ),
            ),
        ),
        "",
    ]


def _costs(runs: list[RunSummary], heads: list[str], rows: Rows) -> list[str]:
    cost_rows: list[tuple[str, Any]] = []
    for v in COST_VIEWS:
        cost_rows += [
            (f"{v}: answering, total", _gen(lambda s, v=v: _usd(s.system_cost(v)))),
            (
                f"{v}: per question",
                _gen(lambda s, v=v: _usd(s.system_cost(v) / s.answered if s.answered else None)),
            ),
            (
                f"{v}: per correct answer",
                _gen(lambda s, v=v: _usd(s.system_cost(v) / s.correct if s.correct else None)),
            ),
            (f"{v}: judge, total", _gen(lambda s, v=v: _usd(s.judge_cost[v]))),
        ]
    gpu_note = ""
    if any(s.gpu_rate is not None for s in runs):
        cost_rows += [
            ("GPU-hour: wall time", _gen(lambda s: _hours(s.wall_s) if s.gpu_rate else "–")),
            ("GPU-hour: answering, total", _gen(lambda s: _usd(s.gpu_system_cost()))),
            (
                "GPU-hour: per question",
                _gen(lambda s: _usd(_div(s.gpu_system_cost(), s.answered))),
            ),
            (
                "GPU-hour: per correct answer",
                _gen(lambda s: _usd(_div(s.gpu_system_cost(), s.correct))),
            ),
        ]
        gpu_note = (
            " *GPU-hour* is for a self-hosted model, which has no token price (the token views "
            "cost it at $0): the run's `gpu_usd_per_hour` times its wall time from start to "
            "finish (a run resumed after a pause includes the gap, and it assumes the GPU was "
            "rented for all of it), plus any other models at plain API cost."
        )
    return [
        "## Cost",
        "",
        f"USD at list prices (`config/prices.toml`, as of {load_prices()['as_of']}), whoever "
        "paid. *As recorded* prices the tokens the backend actually used: `claude -p` writes "
        "each new prompt to the 1-hour cache at 2× input and reads repeats from it, so this "
        "figure depends on what earlier runs left in the cache. *Plain API* is the same tokens "
        "as an uncached API call and *Batch API* is that at the batch discount; these two are "
        "comparable across runs and backends. Answering cost is "
        "generation, query embedding, any reranker and any scope model (counted on every run, "
        "even when its "
        "result came from the cache, so runs compare as deployed systems); the judge is eval "
        "overhead and is shown on its own." + gpu_note,
        "",
        _table(heads, rows(*cost_rows)),
        "",
    ]
