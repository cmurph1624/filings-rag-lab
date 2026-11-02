"""Faithfulness: is each claim in an answer supported by the context its generator was given?

The judge (`faithful@1`, the run's judge model) sees the same user prompt the generator saw,
rebuilt from the run record: the Facts section, the retrieved excerpts in rank order with the
same headers, and the question. It lists the answer's checkable claims and labels each one
supported, unsupported or contradicted. Unlike `judge@1` it never sees the gold answer: an
answer can be correct and unfaithful (a right figure from outside knowledge) or faithful and
wrong (a figure faithfully read from the wrong filing).

Same loop and accounting as `frl judge`: resumable, one row per question, errors retried.
"""

import json
import re
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from filings_rag.backends.base import ModelBackend, Result
from filings_rag.eval.judge import JudgeStats, call_all
from filings_rag.harness.config import RunConfig
from filings_rag.harness.prompts import FaithfulPrompt, Prompt
from filings_rag.harness.run import document_periods
from filings_rag.ingest.registry import get_pipeline
from filings_rag.pricing import Usage, model_cost
from filings_rag.retrieve.base import Hit
from filings_rag.route import facts as route_facts

PROMPT = "faithful@1"
SUPPORT = ("supported", "unsupported", "contradicted")
_JSON = re.compile(r"\{.*\}", re.DOTALL)


class ClaimsError(ValueError):
    pass


def parse_claims(text: str) -> list[dict[str, str]]:
    """[{claim, support, source}] from the judge's reply; tolerates prose or fences around it."""
    m = _JSON.search(text)
    if not m:
        raise ClaimsError(f"no JSON in reply: {text[:200]!r}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ClaimsError(f"bad JSON in reply: {text[:200]!r}") from e
    claims = data.get("claims") if isinstance(data, dict) else None
    if not isinstance(claims, list):
        raise ClaimsError("reply has no claims list")
    out = []
    for c in claims:
        if not isinstance(c, dict):
            raise ClaimsError(f"claim is not an object: {c!r}"[:200])
        support = str(c.get("support", "")).strip().lower()
        if support not in SUPPORT:
            raise ClaimsError(f"unknown support {support!r}")
        out.append(
            {
                "claim": str(c.get("claim", "")).strip(),
                "support": support,
                "source": str(c.get("source") or "none").strip(),
            }
        )
    return out


def judge_key(cfg: RunConfig) -> str:
    """The run's judge (backend, model, effort) with the faithfulness prompt."""
    if cfg.judge_key is None:
        raise ValueError("run has no [judge]")
    return cfg.judge_key.rsplit(":", 1)[0] + ":" + PROMPT


def contexts(
    conn: psycopg.Connection[Any], run_id: int, cfg: RunConfig, prompt: Prompt
) -> dict[str, tuple[str, str]]:
    """question id -> (the generator's user prompt, rebuilt; its answer), for answered
    questions."""
    rows = conn.execute(
        "SELECT r.question_id, q.question, r.answer, r.retrieved, r.route FROM run_questions r"
        " JOIN questions q ON q.id = r.question_id"
        " WHERE r.run_id = %s AND r.error IS NULL AND r.answer IS NOT NULL ORDER BY 1",
        (run_id,),
    ).fetchall()
    ids = sorted({int(h["chunk_id"]) for *_, retrieved, _ in rows for h in retrieved})
    table = "–"
    text: dict[int, str] = {}
    if ids:
        p = get_pipeline(cfg.pipeline)
        row = conn.execute(
            "SELECT chunk_table FROM pipelines WHERE name = %s AND version = %s",
            (p.name, p.version),
        ).fetchone()
        if row is None:
            raise ValueError(f"pipeline {cfg.pipeline} is not built")
        table = row[0]
        text = dict(
            conn.execute(
                sql.SQL("SELECT id, text FROM {} WHERE id = ANY(%s)").format(sql.Identifier(table)),
                (ids,),
            ).fetchall()
        )
    periods = document_periods(conn) if prompt.uses_periods else None
    conn.commit()
    out: dict[str, tuple[str, str]] = {}
    for qid, question, answer, retrieved, route in rows:
        missing = [h["chunk_id"] for h in retrieved if h["chunk_id"] not in text]
        if missing:
            raise ValueError(f"{qid}: chunks {missing[:3]} not in {table}")
        hits = [
            Hit(
                h["chunk_id"],
                h["doc_name"],
                h["page"],
                h["score"],
                text[h["chunk_id"]],
                h["section"],
            )
            for h in retrieved
        ]
        facts = ""
        if route and route.get("anchor"):
            results = [route_facts.FactResult(**f) for f in route.get("facts", [])]
            facts = route_facts.render_block(route["anchor"], results)
        out[qid] = (prompt.render(question, hits, periods, facts), answer)
    return out


_RECORD = """
INSERT INTO faithfulness
    (run_id, question_id, judge, claims, supported, unsupported, contradicted, model,
     input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cache_write_1h_tokens,
     cost_usd, reported_cost_usd, latency_s, error)
VALUES (%(run_id)s, %(qid)s, %(judge)s, %(claims)s, %(sup)s, %(uns)s, %(con)s, %(model)s,
        %(inp)s, %(out)s, %(cr)s, %(cw)s, %(cw1h)s, %(cost)s, %(reported)s, %(latency)s,
        %(error)s)
ON CONFLICT (run_id, question_id, judge) DO UPDATE SET
    claims = EXCLUDED.claims,
    supported = EXCLUDED.supported,
    unsupported = EXCLUDED.unsupported,
    contradicted = EXCLUDED.contradicted,
    attempts = faithfulness.attempts + 1,
    model = EXCLUDED.model,
    input_tokens = faithfulness.input_tokens + EXCLUDED.input_tokens,
    output_tokens = faithfulness.output_tokens + EXCLUDED.output_tokens,
    cache_read_tokens = faithfulness.cache_read_tokens + EXCLUDED.cache_read_tokens,
    cache_write_tokens = faithfulness.cache_write_tokens + EXCLUDED.cache_write_tokens,
    cache_write_1h_tokens = faithfulness.cache_write_1h_tokens + EXCLUDED.cache_write_1h_tokens,
    cost_usd = faithfulness.cost_usd + EXCLUDED.cost_usd,
    reported_cost_usd = CASE
        WHEN EXCLUDED.reported_cost_usd IS NULL THEN faithfulness.reported_cost_usd
        ELSE coalesce(faithfulness.reported_cost_usd, 0) + EXCLUDED.reported_cost_usd END,
    latency_s = EXCLUDED.latency_s,
    error = EXCLUDED.error,
    judged_at = now()
"""


def faithful_run(
    conn: psycopg.Connection[Any],
    run_id: int,
    cfg: RunConfig,
    backend: ModelBackend,
    prompt: FaithfulPrompt,
    gen_prompt: Prompt,
    *,
    concurrency: int = 1,
    only: set[str] | None = None,
    log: Callable[[str], None] = print,
) -> JudgeStats:
    key = judge_key(cfg)
    ctx = contexts(conn, run_id, cfg, gen_prompt)
    done = {
        r[0]
        for r in conn.execute(
            "SELECT question_id FROM faithfulness"
            " WHERE run_id = %s AND judge = %s AND claims IS NOT NULL",
            (run_id, key),
        )
    }
    conn.commit()
    qids = [q for q in ctx if only is None or q in only]
    stats = JudgeStats(skipped=sum(q in done for q in qids))

    def record(qid: str, result: Result | None, error: str | None) -> None:
        claims: list[dict[str, str]] | None = None
        usage = result.usage if result else Usage()
        cost = 0.0
        if result is not None:
            try:
                cost = model_cost(result.model, usage)
                claims = parse_claims(result.text)
            except (KeyError, ClaimsError) as e:
                error = f"{type(e).__name__}: {e}"
        n = {s: sum(c["support"] == s for c in claims or []) for s in SUPPORT}
        conn.execute(
            _RECORD,
            {
                "run_id": run_id,
                "qid": qid,
                "judge": key,
                "claims": Jsonb(claims) if claims is not None else None,
                "sup": n["supported"] if claims is not None else None,
                "uns": n["unsupported"] if claims is not None else None,
                "con": n["contradicted"] if claims is not None else None,
                "model": result.model if result else None,
                "inp": usage.input_tokens,
                "out": usage.output_tokens,
                "cr": usage.cache_read_tokens,
                "cw": usage.cache_write_tokens,
                "cw1h": usage.cache_write_1h_tokens,
                "cost": cost,
                "reported": result.reported_cost_usd if result else None,
                "latency": result.latency_s if result else None,
                "error": error,
            },
        )
        conn.commit()
        stats.usage += usage
        stats.cost += cost
        if error is None:
            stats.judged += 1
            log(
                f"  {qid:24} {n['supported']} supported, {n['unsupported']} unsupported,"
                f" {n['contradicted']} contradicted"
            )
        else:
            stats.failed += 1
            log(f"  FAIL {qid:24} {error[:200]}")

    jobs = ((q, prompt.system, prompt.render(*ctx[q])) for q in qids if q not in done)
    call_all(jobs, backend, record, stats, concurrency)
    if stats.paused:
        log(
            f"paused by the usage governor: {stats.paused}. Re-run `frl faithful {run_id}`"
            " once usage drops (or after `usage override 2h`)"
        )
    return stats
