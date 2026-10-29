"""Run one config over a question set and record every answer. Resumable per question.

Retrieval and all database writes happen on the calling thread; only generation runs in the
worker pool, so one connection is enough. Each answer commits on its own.

Tokens and costs on a run_questions row are summed over attempts (a failed attempt that
reported no usage adds nothing); the answer and retrieved chunks are from the latest attempt.
"""

import subprocess
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from filings_rag.backends.base import ModelBackend, Result
from filings_rag.harness.config import RunConfig
from filings_rag.harness.prompts import Prompt
from filings_rag.ingest.registry import get_pipeline
from filings_rag.pricing import Usage, embedding_cost, model_cost
from filings_rag.retrieve.base import Retrieval, RetrievalError, Retriever

Log = Callable[[str], None]


@dataclass(frozen=True)
class GitInfo:
    sha: str
    dirty: bool


def git_info(root: Path) -> GitInfo:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()

    return GitInfo(git("rev-parse", "HEAD"), bool(git("status", "--porcelain")))


def pipeline_index(conn: psycopg.Connection[Any], ref: str) -> tuple[str, str, int]:
    """(chunk_table, embedder, dims) for a built pipeline version."""
    p = get_pipeline(ref)
    row = conn.execute(
        "SELECT chunk_table, embedder, dims FROM pipelines WHERE name = %s AND version = %s",
        (p.name, p.version),
    ).fetchone()
    if row is None:
        raise ValueError(f"{ref} is not built; run `frl ingest build {ref}` first")
    return str(row[0]), str(row[1]), int(row[2])


def create_run(conn: psycopg.Connection[Any], cfg: RunConfig, git: GitInfo) -> int:
    row = conn.execute(
        "INSERT INTO runs (name, config, config_hash, git_sha, git_dirty)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (cfg.name, Jsonb(cfg.raw), cfg.hash, git.sha, git.dirty),
    ).fetchone()
    assert row is not None
    conn.commit()
    return int(row[0])


def check_resume(conn: psycopg.Connection[Any], run_id: int, cfg: RunConfig) -> None:
    row = conn.execute("SELECT config_hash FROM runs WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"no run {run_id}")
    if row[0] != cfg.hash:
        raise ValueError(
            f"run {run_id} was made with a different config ({row[0]} != {cfg.hash}); "
            "start a new run instead of mixing results"
        )


@dataclass
class RunStats:
    run_id: int
    answered: int = 0
    failed: int = 0
    skipped: int = 0  # already answered in an earlier invocation
    usage: Usage = field(default_factory=Usage)
    embed_tokens: int = 0
    gen_cost: float = 0.0
    embed_cost: float = 0.0
    reported_cost: float = 0.0  # sum of what the backend reported, for cross-checking
    stopped_early: bool = False
    finished: bool = False


@dataclass(frozen=True)
class _Job:
    question_id: str
    retrieval: Retrieval
    retrieve_s: float


_RECORD = """
INSERT INTO run_questions
    (run_id, question_id, git_sha, retrieved, answer, model, stop_reason,
     input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cache_write_1h_tokens,
     embed_tokens, gen_cost_usd, embed_cost_usd, reported_cost_usd, retrieve_s, generate_s, error)
VALUES (%(run_id)s, %(qid)s, %(git_sha)s, %(retrieved)s, %(answer)s, %(model)s, %(stop)s,
        %(inp)s, %(out)s, %(cr)s, %(cw)s, %(cw1h)s,
        %(emb)s, %(gen_cost)s, %(emb_cost)s, %(reported)s, %(retrieve_s)s, %(generate_s)s,
        %(error)s)
ON CONFLICT (run_id, question_id) DO UPDATE SET
    git_sha = EXCLUDED.git_sha,
    attempts = run_questions.attempts + 1,
    retrieved = EXCLUDED.retrieved,
    answer = EXCLUDED.answer,
    model = EXCLUDED.model,
    stop_reason = EXCLUDED.stop_reason,
    input_tokens = run_questions.input_tokens + EXCLUDED.input_tokens,
    output_tokens = run_questions.output_tokens + EXCLUDED.output_tokens,
    cache_read_tokens = run_questions.cache_read_tokens + EXCLUDED.cache_read_tokens,
    cache_write_tokens = run_questions.cache_write_tokens + EXCLUDED.cache_write_tokens,
    cache_write_1h_tokens = run_questions.cache_write_1h_tokens + EXCLUDED.cache_write_1h_tokens,
    embed_tokens = run_questions.embed_tokens + EXCLUDED.embed_tokens,
    gen_cost_usd = run_questions.gen_cost_usd + EXCLUDED.gen_cost_usd,
    embed_cost_usd = run_questions.embed_cost_usd + EXCLUDED.embed_cost_usd,
    reported_cost_usd = CASE
        WHEN EXCLUDED.reported_cost_usd IS NULL THEN run_questions.reported_cost_usd
        ELSE coalesce(run_questions.reported_cost_usd, 0) + EXCLUDED.reported_cost_usd END,
    retrieve_s = EXCLUDED.retrieve_s,
    generate_s = EXCLUDED.generate_s,
    error = EXCLUDED.error,
    answered_at = now()
"""


def pending_questions(
    conn: psycopg.Connection[Any], run_id: int, question_set: str, limit: int | None
) -> tuple[list[tuple[str, str]], int]:
    """(questions still to answer, count already answered) within the first `limit` by id."""
    rows = conn.execute(
        "SELECT q.id, q.question, r.question_id IS NOT NULL AND r.error IS NULL"
        " FROM questions q LEFT JOIN run_questions r"
        "   ON r.run_id = %s AND r.question_id = q.id"
        " WHERE q.benchmark = %s ORDER BY q.id LIMIT %s",
        (run_id, question_set, limit),
    ).fetchall()
    if not rows:
        raise ValueError(f"no questions in set {question_set!r}; run `frl bench pull` first")
    todo = [(qid, text) for qid, text, done in rows if not done]
    return todo, len(rows) - len(todo)


def execute(
    conn: psycopg.Connection[Any],
    run_id: int,
    cfg: RunConfig,
    retriever: Retriever,
    backend: ModelBackend | None,
    prompt: Prompt | None,
    git: GitInfo,
    *,
    limit: int | None = None,
    log: Log = print,
) -> RunStats:
    todo, skipped = pending_questions(conn, run_id, cfg.questions, limit)
    conn.commit()
    stats = RunStats(run_id, skipped=skipped)
    concurrency = max(1, cfg.execution["concurrency"])
    max_failures = cfg.execution["max_consecutive_failures"]
    streak = 0

    def record(job: _Job | None, qid: str, result: Result | None, error: str | None) -> None:
        nonlocal streak
        usage = result.usage if result else Usage()
        gen_cost = 0.0
        if result is not None:
            try:
                gen_cost = model_cost(result.model, usage)
            except KeyError as e:  # keep the answer and its tokens; a resume retries it
                error = str(e)
        emb_tokens = job.retrieval.embed_tokens if job else 0
        emb_cost = 0.0
        if job is not None and emb_tokens:
            try:
                emb_cost = embedding_cost(job.retrieval.embedder, emb_tokens)
            except KeyError as e:
                error = error or str(e)
        retrieved = [
            {"chunk_id": h.chunk_id, "doc_name": h.doc_name, "page": h.page, "score": h.score}
            for h in (job.retrieval.hits if job else [])
        ]
        conn.execute(
            _RECORD,
            {
                "run_id": run_id,
                "qid": qid,
                "git_sha": git.sha,
                "retrieved": Jsonb(retrieved),
                "answer": result.text if result else None,
                "model": result.model if result else None,
                "stop": result.stop_reason if result else None,
                "inp": usage.input_tokens,
                "out": usage.output_tokens,
                "cr": usage.cache_read_tokens,
                "cw": usage.cache_write_tokens,
                "cw1h": usage.cache_write_1h_tokens,
                "emb": emb_tokens,
                "gen_cost": gen_cost,
                "emb_cost": emb_cost,
                "reported": result.reported_cost_usd if result else None,
                "retrieve_s": job.retrieve_s if job else None,
                "generate_s": result.latency_s if result else None,
                "error": error,
            },
        )
        conn.commit()
        stats.usage += usage
        stats.embed_tokens += emb_tokens
        stats.gen_cost += gen_cost
        stats.embed_cost += emb_cost
        stats.reported_cost += (result.reported_cost_usd or 0.0) if result else 0.0
        if error is None:
            stats.answered += 1
            streak = 0
            if result is None:  # retrieval-only run
                log(f"  ok   {qid:24} retrieved {len(job.retrieval.hits) if job else 0}")
            else:
                took = f"{result.latency_s:6.1f}s"
                log(f"  ok   {qid:24} {took}  ${gen_cost:.4f}  {result.stop_reason}")
        else:
            stats.failed += 1
            streak += 1
            log(f"  FAIL {qid:24} {error[:200]}")

    def settle(fut: "Future[Result]", job: _Job) -> None:
        result: Result | None = None
        error: str | None = None
        try:
            result = fut.result()
        except Exception as e:  # one bad answer shouldn't stop the run; it's retried on resume
            error = f"{type(e).__name__}: {e}"
        record(job, job.question_id, result, error)  # exactly once, whatever happens after

    in_flight: dict[Future[Result], _Job] = {}
    with ThreadPoolExecutor(concurrency) as pool:
        for qid, question in todo:
            if streak >= max_failures:
                stats.stopped_early = True
                log(f"stopping: {streak} failures in a row (resume with --resume {run_id})")
                break
            start = time.monotonic()
            try:
                retrieval = retriever.retrieve(question)
            except Exception as e:
                conn.rollback()
                spent = None
                if isinstance(e, RetrievalError):  # the query was embedded before the failure
                    spent = _Job(qid, Retrieval([], e.embed_tokens, e.embedder), 0.0)
                record(spent, qid, None, f"retrieval: {type(e).__name__}: {e}")
                continue
            job = _Job(qid, retrieval, time.monotonic() - start)
            if backend is None or prompt is None:
                record(job, qid, None, None)
                continue
            fut = pool.submit(
                backend.generate, prompt.system, prompt.render(question, retrieval.hits)
            )
            in_flight[fut] = job
            while len(in_flight) >= concurrency:
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for f in done:
                    settle(f, in_flight.pop(f))
        while in_flight:
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for f in done:
                settle(f, in_flight.pop(f))

    stats.finished = _mark_finished(conn, run_id, cfg.questions)
    return stats


def _mark_finished(conn: psycopg.Connection[Any], run_id: int, question_set: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM questions q WHERE q.benchmark = %s AND NOT EXISTS ("
        " SELECT 1 FROM run_questions r"
        " WHERE r.run_id = %s AND r.question_id = q.id AND r.error IS NULL)",
        (question_set, run_id),
    ).fetchone()
    done = row is not None and row[0] == 0
    if done:
        conn.execute(
            "UPDATE runs SET finished_at = now() WHERE id = %s AND finished_at IS NULL", (run_id,)
        )
    conn.commit()
    return done
