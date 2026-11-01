"""Grade a run's answers against the FinanceBench gold answers with the run's pinned judge.

Same shape as the run loop: database work on the calling thread, model calls in a small pool,
each verdict committed on its own. A judge call that fails or returns no usable verdict is
stored with `error` (and its tokens) and retried on the next pass.
"""

import json
import re
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any

import psycopg

from filings_rag.backends.base import ModelBackend, Result
from filings_rag.backends.governor import UsagePaused
from filings_rag.harness.prompts import JudgePrompt
from filings_rag.pricing import Usage, model_cost

VERDICTS = ("correct", "incorrect", "refused")
_JSON = re.compile(r"\{.*\}", re.DOTALL)


class VerdictError(ValueError):
    pass


def parse_verdict(text: str) -> tuple[str, str]:
    """(verdict, rationale) from the judge's reply; tolerates prose or code fences around it."""
    m = _JSON.search(text)
    if not m:
        raise VerdictError(f"no JSON in judge reply: {text[:200]!r}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise VerdictError(f"bad JSON in judge reply: {text[:200]!r}") from e
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        raise VerdictError(f"unknown verdict {verdict!r}")
    return verdict, str(data.get("rationale", "")).strip()


@dataclass
class JudgeStats:
    judged: int = 0
    failed: int = 0
    skipped: int = 0  # already judged by this judge
    usage: Usage = Usage()
    cost: float = 0.0
    paused: str | None = None  # the usage governor's reason, when it stopped judging


_RECORD = """
INSERT INTO judgments
    (run_id, question_id, judge, verdict, rationale, model, input_tokens, output_tokens,
     cache_read_tokens, cache_write_tokens, cache_write_1h_tokens, cost_usd, reported_cost_usd,
     latency_s, error)
VALUES (%(run_id)s, %(qid)s, %(judge)s, %(verdict)s, %(rationale)s, %(model)s, %(inp)s,
        %(out)s, %(cr)s, %(cw)s, %(cw1h)s, %(cost)s, %(reported)s, %(latency)s, %(error)s)
ON CONFLICT (run_id, question_id, judge) DO UPDATE SET
    verdict = EXCLUDED.verdict,
    rationale = EXCLUDED.rationale,
    attempts = judgments.attempts + 1,
    model = EXCLUDED.model,
    input_tokens = judgments.input_tokens + EXCLUDED.input_tokens,
    output_tokens = judgments.output_tokens + EXCLUDED.output_tokens,
    cache_read_tokens = judgments.cache_read_tokens + EXCLUDED.cache_read_tokens,
    cache_write_tokens = judgments.cache_write_tokens + EXCLUDED.cache_write_tokens,
    cache_write_1h_tokens = judgments.cache_write_1h_tokens + EXCLUDED.cache_write_1h_tokens,
    cost_usd = judgments.cost_usd + EXCLUDED.cost_usd,
    reported_cost_usd = CASE
        WHEN EXCLUDED.reported_cost_usd IS NULL THEN judgments.reported_cost_usd
        ELSE coalesce(judgments.reported_cost_usd, 0) + EXCLUDED.reported_cost_usd END,
    latency_s = EXCLUDED.latency_s,
    error = EXCLUDED.error,
    judged_at = now()
"""


def judge_run(
    conn: psycopg.Connection[Any],
    run_id: int,
    judge_key: str,
    backend: ModelBackend,
    prompt: JudgePrompt,
    *,
    concurrency: int = 2,
    only: set[str] | None = None,
    log: Callable[[str], None] = print,
) -> JudgeStats:
    """`only`: judge just these question ids, so disjoint shards can run in parallel."""
    rows = conn.execute(
        "SELECT r.question_id, q.question, q.answer, q.justification, r.answer,"
        "       j.verdict IS NOT NULL"
        " FROM run_questions r JOIN questions q ON q.id = r.question_id"
        " LEFT JOIN judgments j ON j.run_id = r.run_id AND j.question_id = r.question_id"
        "   AND j.judge = %s"
        " WHERE r.run_id = %s AND r.error IS NULL AND r.answer IS NOT NULL"
        " ORDER BY r.question_id",
        (judge_key, run_id),
    ).fetchall()
    conn.commit()
    if only is not None:
        rows = [r for r in rows if r[0] in only]
    stats = JudgeStats(skipped=sum(1 for *_, done in rows if done))

    def record(qid: str, result: Result | None, error: str | None) -> None:
        verdict = rationale = None
        usage = result.usage if result else Usage()
        cost = 0.0
        if result is not None:
            try:
                cost = model_cost(result.model, usage)
                verdict, rationale = parse_verdict(result.text)
            except (KeyError, VerdictError) as e:
                error = f"{type(e).__name__}: {e}"
        conn.execute(
            _RECORD,
            {
                "run_id": run_id,
                "qid": qid,
                "judge": judge_key,
                "verdict": verdict,
                "rationale": rationale,
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
            log(f"  {verdict:9} {qid:24} {rationale}"[:220])
        else:
            stats.failed += 1
            log(f"  FAIL      {qid:24} {error[:200]}")

    def settle(fut: "Future[Result]", qid: str) -> None:
        result: Result | None = None
        error: str | None = None
        try:
            result = fut.result()
        except UsagePaused as e:  # nothing spent; re-running `frl judge` picks it up
            stats.paused = str(e)
            return
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        record(qid, result, error)

    in_flight: dict[Future[Result], str] = {}
    with ThreadPoolExecutor(max(1, concurrency)) as pool:
        for qid, question, gold, justification, answer, done in rows:
            if done:
                continue
            if stats.paused:
                break
            text = prompt.render(question, gold, justification, answer)
            in_flight[pool.submit(backend.generate, prompt.system, text)] = qid
            while len(in_flight) >= max(1, concurrency):
                finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for f in finished:
                    settle(f, in_flight.pop(f))
        while in_flight:
            finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for f in finished:
                settle(f, in_flight.pop(f))
    if stats.paused:
        log(
            f"paused by the usage governor: {stats.paused}. Re-run `frl judge {run_id}`"
            " once usage drops (or after `usage override 2h`)"
        )
    return stats
