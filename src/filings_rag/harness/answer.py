"""One question through the pipeline: scope → route → facts → retrieve, then the prompt.

Shared by `frl run` (`harness/run.execute`, which adds the worker pool, the usage governor and
the `run_questions` writes) and the serving app, so a served answer is produced exactly as a
benchmark answer is. Nothing here touches the benchmark tables: the question is plain text and
`question_id` is only a cache key for the LLM scope and route modes.

Failures: `UsagePaused` passes through untouched (the caller stops cleanly). Any other failure
raises `StageError`, naming the step and carrying what was already computed, so the caller can
record the scope (and its spend) or the query embedding on a failed row.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from filings_rag.backends.base import ModelBackend, Result
from filings_rag.backends.governor import UsagePaused
from filings_rag.facts import concepts
from filings_rag.facts.lookup import FactLookup, read_offsets
from filings_rag.harness.prompts import Prompt
from filings_rag.retrieve.base import Retrieval, Retriever
from filings_rag.retrieve.decompose import merge as merge_retrievals
from filings_rag.retrieve.factory import resolve_spec
from filings_rag.retrieve.scope import Scope, Scoper
from filings_rag.route import facts as route_facts
from filings_rag.route.plan import RoutePlan
from filings_rag.route.router import Router

REPO_ROOT = Path(__file__).resolve().parents[3]


class FactRoute:
    """Computes the facts for a fact or mixed plan, anchored on the scope's filing. Lookups are
    cached per anchor for the life of the object (a run, or the serving process)."""

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        cmap_path: Path | None = None,
        companies_csv: Path | None = None,
        *,
        root: Path = REPO_ROOT,
    ) -> None:
        self._conn = conn
        self._cmap = concepts.load(cmap_path or root / "config" / "concepts.toml")
        self._offsets = read_offsets(companies_csv or root / "corpus" / "companies.csv")
        self._lookups: dict[str, FactLookup] = {}

    def run(self, plan: RoutePlan, scope: Scope | None) -> tuple[dict[str, Any], str]:
        """(route record, Facts section for the prompt). Never raises: any failure is recorded
        as `facts_error` and the question is answered without facts, from the excerpts."""
        anchor = None
        try:
            results: list[route_facts.FactResult] = []
            if (
                plan.route != "prose"
                and scope is not None
                and scope.record.get("level") != "corpus"
            ):
                anchor = route_facts.choose_anchor(scope.record.get("docs", []), plan)
            if anchor is not None:
                if anchor not in self._lookups:
                    self._lookups[anchor] = FactLookup(
                        self._conn, self._cmap, anchor, self._offsets
                    )
                results = route_facts.compute_plan(self._lookups[anchor], plan)
            text = route_facts.render_block(anchor, results) if anchor else ""
            return route_facts.record(plan, anchor, results), text
        except Exception as e:
            self._conn.rollback()
            rec = route_facts.record(plan, anchor, [])
            return rec | {"facts_error": f"{type(e).__name__}: {e}"[:300]}, ""


@dataclass(frozen=True)
class AnswerContext:
    """Everything the per-question steps need, built once per run or per serving process."""

    retriever: Retriever
    scoper: Scoper | None = None
    router: Router | None = None
    fact_route: FactRoute | None = None
    decompose: bool = False
    k: int = 10
    decompose_merge: str = "round-robin"
    # Serving only: stop after the scope step when it found no company (level "corpus"), before
    # any embedding, rerank or model spend. Benchmark runs leave it off.
    refuse_unscoped: bool = False

    @classmethod
    def build(
        cls,
        conn: psycopg.Connection[Any],
        retriever_spec: dict[str, Any],
        retriever: Retriever,
        *,
        scoper: Scoper | None = None,
        router: Router | None = None,
        facts: FactRoute | None = None,
    ) -> "AnswerContext":
        spec = resolve_spec(retriever_spec)
        return cls(
            retriever=retriever,
            scoper=scoper,
            router=router,
            fact_route=facts or (FactRoute(conn) if router is not None else None),
            decompose=bool(spec.get("decompose")),
            k=int(spec["k"]),
            decompose_merge=str(spec.get("decompose_merge", "round-robin")),
        )


@dataclass(frozen=True)
class Prepared:
    """A question after scope, route, facts and retrieval: ready for the prompt."""

    question_id: str
    question: str
    retrieval: Retrieval
    retrieve_s: float  # scope + route + facts + retrieval, wall time
    scope: Scope | None = None
    route: dict[str, Any] | None = None  # plan, anchor and computed facts
    route_aux: dict[str, Any] | None = None  # the route model's spend
    facts_text: str = ""

    def render(self, prompt: Prompt, periods: dict[str, str] | None) -> str:
        return prompt.render(self.question, self.retrieval.hits, periods, self.facts_text)


class Unscoped(Exception):
    """The question names no company in the corpus (`AnswerContext.refuse_unscoped`)."""

    def __init__(self, scope: Scope) -> None:
        self.scope = scope
        super().__init__("no company in the corpus was found in the question")


class StageError(Exception):
    """A step failed. `message` is what a run records as the row's error; `scope` is set when
    the scope step had already succeeded."""

    def __init__(self, stage: str, cause: Exception, scope: Scope | None = None) -> None:
        self.stage = stage
        self.cause = cause
        self.scope = scope
        self.message = f"{stage}: {type(cause).__name__}: {cause}"
        super().__init__(self.message)


def prepare(ctx: AnswerContext, question_id: str, question: str) -> Prepared:
    """Scope, route, facts and retrieval for one question. Raises `UsagePaused`, `StageError`,
    or `Unscoped` (only with `refuse_unscoped`); the fact step never raises (see
    `FactRoute.run`)."""
    start = time.monotonic()
    scope: Scope | None = None
    if ctx.scoper is not None:
        try:
            scope = ctx.scoper.scope(question_id, question)
        except UsagePaused:
            raise
        except Exception as e:
            raise StageError("scope", e) from e
        if ctx.refuse_unscoped and scope.record.get("level") == "corpus":
            raise Unscoped(scope)
    route: dict[str, Any] | None = None
    route_aux: dict[str, Any] | None = None
    facts_text = ""
    if ctx.router is not None:
        try:
            plan, route_aux = ctx.router.route(question_id, question)
        except UsagePaused:
            raise
        except Exception as e:
            raise StageError("route", e, scope) from e
        assert ctx.fact_route is not None
        route, facts_text = ctx.fact_route.run(plan, scope)
    try:
        if ctx.decompose and scope is not None and len(scope.groups) > 1:
            scope.record["decomposed"] = len(scope.groups)
            retrieval = merge_retrievals(
                [ctx.retriever.retrieve(question, g) for g in scope.groups],
                ctx.k,
                ctx.decompose_merge,
            )
        else:
            retrieval = ctx.retriever.retrieve(question, scope.documents if scope else None)
    except Exception as e:
        raise StageError("retrieval", e, scope) from e
    return Prepared(
        question_id,
        question,
        retrieval,
        time.monotonic() - start,
        scope,
        route,
        route_aux,
        facts_text,
    )


def answer_one(
    ctx: AnswerContext,
    question_id: str,
    question: str,
    backend: ModelBackend,
    prompt: Prompt,
    periods: dict[str, str] | None,
) -> tuple[Prepared, Result]:
    """`prepare`, then generate on the calling thread. A generation failure raises
    `StageError("generate", ...)`; `UsagePaused` passes through."""
    prep = prepare(ctx, question_id, question)
    try:
        return prep, backend.generate(prompt.system, prep.render(prompt, periods))
    except UsagePaused:
        raise
    except Exception as e:
        raise StageError("generate", e, prep.scope) from e
