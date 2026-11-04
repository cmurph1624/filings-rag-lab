"""Answers one free-text question and records it in `served_queries`.

The pipeline objects share one index connection and aren't thread-safe, so scope, route, facts
and retrieval run under a lock (they take well under a second); generation, which takes
seconds, runs outside it. Each request writes its row on its own connection to the app
database, so a failed write never leaves the index connection mid-transaction.
"""

import csv
import dataclasses
import logging
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic
import psycopg
from psycopg.types.json import Jsonb

from filings_rag.backends.anthropic_api import AnthropicApiBackend
from filings_rag.backends.base import ModelBackend, Result
from filings_rag.backends.factory import make_backend
from filings_rag.config import Settings
from filings_rag.harness import setup
from filings_rag.harness.answer import (
    REPO_ROOT,
    AnswerContext,
    FactRoute,
    Prepared,
    StageError,
    Unscoped,
)
from filings_rag.harness.answer import prepare as prepare_question
from filings_rag.harness.prompts import Prompt, get_prompt
from filings_rag.harness.run import document_periods, pipeline_index
from filings_rag.pricing import Usage, embedding_cost, model_cost
from filings_rag.retrieve.base import RetrievalError
from filings_rag.retrieve.scope import Scope
from filings_rag.serve import citations, limits
from filings_rag.serve.config import ServeConfig
from filings_rag.serve.limits import Limits

log = logging.getLogger(__name__)
PUBLIC_ERROR = "The answer couldn't be produced just now. Please try again later."


class QuestionTooLong(ValueError):
    pass


def read_companies(path: Path) -> list[str]:
    with path.open(newline="") as fh:
        return sorted(r["company"] for r in csv.DictReader(fh))


@dataclass
class Asked:
    query_id: uuid.UUID
    status: str  # answered | refused | error
    answer: str | None = None
    refusal: str | None = None  # why no answer was generated, e.g. out_of_coverage
    message: str | None = None  # shown to the user with a refusal
    excerpts: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    model: str | None = None
    cost_usd: float = 0.0
    latency_s: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "query_id": str(self.query_id),
            "status": self.status,
            "answer": self.answer,
            "refusal": self.refusal,
            "message": self.message,
            "excerpts": self.excerpts,
            "facts": self.facts,
            # The detail stays in served_queries; the public sees a generic message.
            "error": PUBLIC_ERROR if self.error else None,
            "model": self.model,
            "cost_usd": round(self.cost_usd, 6),
            "latency_s": round(self.latency_s, 2),
        }


# Settles the row `Limits.reserve` inserted (or inserts it, when no reservation was made).
_INSERT = """
INSERT INTO served_queries
    (id, question, status, refusal, error, answer, model, stop_reason, input_tokens,
     output_tokens, cache_read_tokens, cache_write_tokens, embed_tokens, gen_cost_usd,
     embed_cost_usd, rerank_cost_usd, cost_usd, retrieve_s, generate_s, retrieved, scope, route,
     config_hash, git_sha, ip_hash, tags)
VALUES
    (%(id)s, %(question)s, %(status)s, %(refusal)s, %(error)s, %(answer)s, %(model)s, %(stop)s,
     %(inp)s, %(out)s, %(cr)s, %(cw)s, %(emb)s, %(gen)s, %(embc)s, %(rr)s, %(cost)s,
     %(retrieve_s)s, %(generate_s)s, %(retrieved)s, %(scope)s, %(route)s, %(config_hash)s,
     %(git_sha)s, %(ip_hash)s, %(tags)s)
ON CONFLICT (id) DO UPDATE SET
    status = EXCLUDED.status, refusal = EXCLUDED.refusal, error = EXCLUDED.error,
    answer = EXCLUDED.answer, model = EXCLUDED.model, stop_reason = EXCLUDED.stop_reason,
    input_tokens = EXCLUDED.input_tokens, output_tokens = EXCLUDED.output_tokens,
    cache_read_tokens = EXCLUDED.cache_read_tokens,
    cache_write_tokens = EXCLUDED.cache_write_tokens, embed_tokens = EXCLUDED.embed_tokens,
    gen_cost_usd = EXCLUDED.gen_cost_usd, embed_cost_usd = EXCLUDED.embed_cost_usd,
    rerank_cost_usd = EXCLUDED.rerank_cost_usd, cost_usd = EXCLUDED.cost_usd,
    retrieve_s = EXCLUDED.retrieve_s, generate_s = EXCLUDED.generate_s,
    retrieved = EXCLUDED.retrieved, scope = EXCLUDED.scope, route = EXCLUDED.route
"""


def coverage_message(companies: list[str]) -> str:
    listed = ", ".join(companies) if companies else "the companies in FinanceBench's open subset"
    return (
        "I couldn't find a company I cover in that question. I answer from the 10-K, 10-Q and "
        f"8-K filings of {listed}, fiscal years 2015–2023. Name the company, for example "
        '"What was 3M\'s FY2022 capital expenditure?"'
    )


@dataclass(frozen=True)
class _Spend:
    """USD at list price: generation, query embedding, rerank."""

    embed_tokens: int = 0
    gen: float = 0.0
    embed: float = 0.0
    rerank: float = 0.0

    @property
    def total(self) -> float:
        return self.gen + self.embed + self.rerank

    @classmethod
    def of(
        cls, prep: Prepared | None, failed: StageError | None, result: Result | None
    ) -> "_Spend":
        tokens, embedder, rerank = 0, "", 0.0
        if prep is not None:
            tokens, embedder = prep.retrieval.embed_tokens, prep.retrieval.embedder
            rerank = float(prep.retrieval.aux.get("rerank", {}).get("cost_usd", 0.0))
        elif failed is not None and isinstance(failed.cause, RetrievalError):
            tokens, embedder = failed.cause.embed_tokens, failed.cause.embedder  # paid anyway
        return cls(
            tokens,
            model_cost(result.model, result.usage) if result else 0.0,
            embedding_cost(embedder, tokens) if tokens else 0.0,
            rerank,
        )


class Service:
    def __init__(
        self,
        *,
        ctx: AnswerContext,
        backend: ModelBackend,
        prompt: Prompt,
        periods: dict[str, str] | None,
        docs: dict[str, citations.DocInfo],
        index_conn: psycopg.Connection[Any],
        app_url: str,
        config_hash: str,
        pipeline: str,
        git_sha: str,
        max_question_chars: int = 500,
        limits: Limits | None = None,
        companies: list[str] | None = None,
        ip_salt: str = "",
        tag: str = "local",
    ) -> None:
        self.ctx = ctx
        self.backend = backend
        self.prompt = prompt
        self.periods = periods
        self.docs = docs
        self.index_conn = index_conn
        self.app_url = app_url
        self.config_hash = config_hash
        self.pipeline = pipeline
        self.git_sha = git_sha
        self.max_question_chars = max_question_chars
        self.limits = limits or Limits()
        self.companies = companies or []
        if not ip_salt:
            log.warning("no IP_HASH_SALT: rate-limit hashes change on restart")
        self.ip_salt = ip_salt or secrets.token_hex(16)
        self.tag = tag
        self._lock = threading.Lock()

    @classmethod
    def from_config(
        cls, cfg: ServeConfig, settings: Settings, git_sha: str, root: Path = REPO_ROOT
    ) -> "Service":
        run = cfg.run
        assert run.generation is not None  # serve.config refuses retrieval-only bases
        conn = psycopg.connect(settings.database_url)
        side = setup.retrieval_side(conn, run, settings, root / "corpus" / "companies.csv", None)
        ctx = AnswerContext.build(
            conn,
            run.retriever,
            side.retriever,
            scoper=side.scoper,
            router=side.router,
            facts=FactRoute(conn, root=root) if side.router is not None else None,
        )
        ctx = dataclasses.replace(ctx, refuse_unscoped=cfg.refuse_unscoped)
        prompt = get_prompt(run.generation["prompt"])
        periods = document_periods(conn) if prompt.uses_periods else None
        docs = citations.load_docs(conn)
        conn.commit()
        spec = run.backend_spec or {}
        backend: ModelBackend
        if spec.get("backend") == "anthropic-api":  # the key comes from Settings (.env or SSM)
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
            extra = {k: v for k, v in spec.items() if k not in {"backend", "model"}}
            backend = AnthropicApiBackend(spec["model"], client=client, **extra)
        else:
            backend = make_backend(spec)
        return cls(
            ctx=ctx,
            backend=backend,
            prompt=prompt,
            periods=periods,
            docs=docs,
            index_conn=conn,
            app_url=settings.app_db,
            config_hash=run.hash,
            pipeline=run.pipeline,
            git_sha=git_sha,
            max_question_chars=cfg.max_question_chars,
            limits=Limits.from_config(cfg.serve),
            companies=read_companies(root / "corpus" / "companies.csv"),
            ip_salt=settings.ip_hash_salt,
            tag=settings.serve_tag,
        )

    def close(self) -> None:
        self.index_conn.close()

    def ask(self, question: str, *, client_ip: str | None = None) -> Asked:
        """Raises `QuestionTooLong` (nothing recorded) or `limits.Refused`."""
        question = question.strip()
        if not question:
            raise QuestionTooLong("empty question")
        if len(question) > self.max_question_chars:
            raise QuestionTooLong(f"questions are limited to {self.max_question_chars} characters")
        qid = uuid.uuid4()
        ip_hash = limits.ip_hash(client_ip, self.ip_salt) if client_ip else None
        tags = (self.tag,)
        with psycopg.connect(self.app_url) as conn:
            self.limits.check_rate(conn, ip_hash)  # Refused (429): nothing is recorded
        self.limits.acquire()  # Refused (503) when every slot stays busy
        try:
            return self._answer(qid, question, ip_hash, tags)
        finally:
            self.limits.release()

    def _answer(
        self, qid: uuid.UUID, question: str, ip_hash: str | None, tags: tuple[str, ...]
    ) -> Asked:
        start = time.monotonic()
        with psycopg.connect(self.app_url) as conn:  # Refused (503) over the daily cap
            self.limits.reserve(
                conn,
                qid,
                {
                    "question": question,
                    "config_hash": self.config_hash,
                    "git_sha": self.git_sha,
                    "ip_hash": ip_hash,
                    "tags": list(tags),
                },
            )
        prep: Prepared | None = None
        result: Result | None = None
        error: str | None = None
        failed: StageError | None = None
        unscoped: Unscoped | None = None
        with self._lock:
            try:
                prep = prepare_question(self.ctx, str(qid), question)
            except StageError as e:
                self.index_conn.rollback()
                error, failed = e.message, e
            except Unscoped as e:  # no company found: refuse before any spend
                self.index_conn.commit()
                unscoped = e
            else:
                self.index_conn.commit()  # end the read transaction; don't hold it while idle
        if prep is not None:
            try:
                rendered = prep.render(self.prompt, self.periods)
                result = self.backend.generate(self.prompt.system, rendered)
            except Exception as e:
                error = f"generate: {type(e).__name__}: {e}"
        spend = _Spend.of(prep, failed, result)
        asked = self._finish(qid, prep, result, error, unscoped, spend, start)
        scope = (
            prep.scope if prep else failed.scope if failed else unscoped.scope if unscoped else None
        )
        try:
            self._record(asked, question, prep, scope, result, spend, ip_hash, tags)
        except Exception:  # the answer was paid for: return it, and keep the spend in the log
            log.exception(
                "served_queries write failed: id=%s status=%s cost_usd=%.6f tokens=%s",
                asked.query_id,
                asked.status,
                asked.cost_usd,
                result.usage if result else None,
            )
        return asked

    def _finish(
        self,
        qid: uuid.UUID,
        prep: Prepared | None,
        result: Result | None,
        error: str | None,
        unscoped: Unscoped | None,
        spend: "_Spend",
        start: float,
    ) -> Asked:
        status = "error" if error else "refused" if unscoped else "answered"
        asked = Asked(
            qid,
            status,
            refusal="out_of_coverage" if unscoped else None,
            message=coverage_message(self.companies) if unscoped else None,
            error=error,
            model=result.model if result else None,
            cost_usd=spend.total,
            latency_s=time.monotonic() - start,
        )
        if result is not None and prep is not None:
            asked.answer = result.text
            asked.excerpts = citations.excerpts(result.text, prep.retrieval.hits, self.docs)
            asked.facts = citations.facts(prep.route, self.docs)
        return asked

    def _record(
        self,
        asked: Asked,
        question: str,
        prep: Prepared | None,
        scope: Scope | None,  # also set when a stage failed or the question was refused
        result: Result | None,
        spend: "_Spend",
        ip_hash: str | None,
        tags: tuple[str, ...],
    ) -> None:
        usage = result.usage if result else Usage()
        hits = prep.retrieval.hits if prep else []
        retrieved = [
            {"chunk_id": h.chunk_id, "doc_name": h.doc_name, "page": h.page, "score": h.score,
             "section": h.section}
            for h in hits
        ]  # fmt: skip
        with psycopg.connect(self.app_url) as conn:
            conn.execute(
                _INSERT,
                {
                    "id": asked.query_id,
                    "question": question,
                    "status": asked.status,
                    "refusal": asked.refusal,
                    "error": asked.error,
                    "answer": asked.answer,
                    "model": asked.model,
                    "stop": result.stop_reason if result else None,
                    "inp": usage.input_tokens,
                    "out": usage.output_tokens,
                    "cr": usage.cache_read_tokens,
                    "cw": usage.cache_write_tokens + usage.cache_write_1h_tokens,
                    "emb": spend.embed_tokens,
                    "gen": spend.gen,
                    "embc": spend.embed,
                    "rr": spend.rerank,
                    "cost": asked.cost_usd,
                    "retrieve_s": prep.retrieve_s if prep else None,
                    "generate_s": result.latency_s if result else None,
                    "retrieved": Jsonb(retrieved),
                    "scope": Jsonb(scope.record) if scope else None,
                    "route": Jsonb(prep.route) if prep and prep.route is not None else None,
                    "config_hash": self.config_hash,
                    "git_sha": self.git_sha,
                    "ip_hash": ip_hash,
                    "tags": list(tags),
                },
            )

    def feedback(self, query_id: uuid.UUID, rating: str, comment: str | None) -> bool:
        """Record (or replace) a rating. False when the query id is unknown."""
        with psycopg.connect(self.app_url) as conn:
            row = conn.execute(
                "INSERT INTO feedback (query_id, rating, comment)"
                " SELECT id, %s, %s FROM served_queries WHERE id = %s"
                " ON CONFLICT (query_id) DO UPDATE SET rating = EXCLUDED.rating,"
                "   comment = EXCLUDED.comment, created_at = now()"
                " RETURNING query_id",
                (rating, comment, query_id),
            ).fetchone()
        return row is not None

    def health(self) -> dict[str, Any]:
        """Both databases answer and the served pipeline is built. No model call."""
        with self._lock:
            try:
                pipeline_index(self.index_conn, self.pipeline)  # raises if not built
            finally:
                self.index_conn.rollback()
        with psycopg.connect(self.app_url) as conn:
            conn.execute("SELECT 1 FROM served_queries LIMIT 1")
        return {"ok": True, "pipeline": self.pipeline}
