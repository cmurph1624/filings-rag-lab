"""`harness.answer`: the per-question steps shared by `frl run` and the serving app."""

from typing import Any

import pytest

from filings_rag.backends.base import BackendError, Result
from filings_rag.backends.governor import UsagePaused
from filings_rag.harness.answer import AnswerContext, Prepared, StageError, answer_one, prepare
from filings_rag.harness.prompts import get_prompt
from filings_rag.pricing import Usage
from filings_rag.retrieve.base import Hit, Retrieval, RetrievalError
from filings_rag.retrieve.scope import Scope
from filings_rag.route.plan import RoutePlan


def hit(i: int) -> Hit:
    return Hit(i, f"DOC{i}", 1, 1.0 - i / 100, f"text {i}", "Item 7")


class Retriever:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[list[int] | None] = []
        self.fail = fail

    def retrieve(self, query: str, documents: list[int] | None = None) -> Retrieval:
        self.calls.append(documents)
        if self.fail:
            raise self.fail
        base = documents[0] if documents else 0
        return Retrieval([hit(base), hit(base + 1)], 5, "voyage-finance-2")


class Scoper:
    def __init__(self, scope: Scope | None = None, fail: Exception | None = None) -> None:
        self.result = scope or Scope([1, 2], {"mode": "rules", "level": "filings", "docs": []})
        self.fail = fail

    def scope(self, question_id: str, question: str) -> Scope:
        if self.fail:
            raise self.fail
        return self.result


class Router:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail

    def route(self, question_id: str, question: str) -> tuple[RoutePlan, dict[str, Any] | None]:
        if self.fail:
            raise self.fail
        return RoutePlan("prose"), None


class Facts:
    def run(self, plan: RoutePlan, scope: Scope | None) -> tuple[dict[str, Any], str]:
        return {"plan": plan.to_json()}, "FACTS BLOCK"


class Backend:
    name = "fake"
    model = "claude-sonnet-5-5"

    def __init__(self, fail: Exception | None = None) -> None:
        self.prompts: list[str] = []
        self.fail = fail

    def generate(self, system: str, prompt: str) -> Result:
        self.prompts.append(prompt)
        if self.fail:
            raise self.fail
        usage = Usage(input_tokens=10, output_tokens=2)
        return Result("42 [1]", usage, 0.1, self.model, "end_turn")


def ctx(**kw: Any) -> AnswerContext:
    kw.setdefault("retriever", Retriever())
    return AnswerContext(**kw)


def test_without_scope_or_router_retrieves_over_the_corpus() -> None:
    r = Retriever()
    prep = prepare(ctx(retriever=r), "q1", "What was revenue?")
    assert r.calls == [None]
    assert prep.scope is None and prep.route is None and prep.facts_text == ""
    assert [h.chunk_id for h in prep.retrieval.hits] == [0, 1]
    assert prep.retrieve_s >= 0


def test_scope_route_and_facts_feed_retrieval_and_the_prompt() -> None:
    r = Retriever()
    c = ctx(retriever=r, scoper=Scoper(), router=Router(), fact_route=Facts())  # type: ignore[arg-type]
    prep = prepare(c, "q1", "What was revenue?")
    assert r.calls == [[1, 2]]
    assert prep.route == {"plan": RoutePlan("prose").to_json()}
    assert prep.facts_text == "FACTS BLOCK"
    prompt = get_prompt("answer@4")
    assert prep.render(prompt, None) == prompt.render(
        "What was revenue?", prep.retrieval.hits, None, "FACTS BLOCK"
    )


def test_decompose_retrieves_each_period_group_and_merges() -> None:
    r = Retriever()
    scope = Scope([1, 2, 3], {"level": "filings"}, groups=[[1], [3]])
    c = ctx(retriever=r, scoper=Scoper(scope), decompose=True, k=4)
    prep = prepare(c, "q1", "Compare 2021 and 2022")
    assert r.calls == [[1], [3]]
    assert {h.chunk_id for h in prep.retrieval.hits} == {1, 2, 3, 4}
    assert scope.record["decomposed"] == 2


@pytest.mark.parametrize(
    ("kw", "stage", "keeps_scope"),
    [
        ({"scoper": Scoper(fail=ValueError("bad"))}, "scope", False),
        ({"scoper": Scoper(), "router": Router(fail=ValueError("bad"))}, "route", True),
        ({"scoper": Scoper(), "retriever": Retriever(fail=ValueError("bad"))}, "retrieval", True),
    ],
)
def test_a_failed_step_names_itself(kw: dict[str, Any], stage: str, keeps_scope: bool) -> None:
    c = ctx(fact_route=Facts(), **kw)  # type: ignore[arg-type]
    with pytest.raises(StageError) as e:
        prepare(c, "q1", "What was revenue?")
    assert e.value.stage == stage
    assert e.value.message == f"{stage}: ValueError: bad"
    assert (e.value.scope is not None) is keeps_scope


def test_retrieval_error_keeps_its_embedding_spend_on_the_cause() -> None:
    fail = RetrievalError("db down", embed_tokens=7, embedder="voyage-finance-2")
    with pytest.raises(StageError) as e:
        prepare(ctx(retriever=Retriever(fail=fail)), "q1", "q")
    assert e.value.cause is fail


@pytest.mark.parametrize("where", ["scoper", "router"])
def test_a_usage_pause_passes_through(where: str) -> None:
    pause = UsagePaused("75%")
    kw: dict[str, Any] = {"scoper": Scoper(), "router": Router(), "fact_route": Facts()}
    kw[where] = Scoper(fail=pause) if where == "scoper" else Router(fail=pause)
    with pytest.raises(UsagePaused):
        prepare(ctx(**kw), "q1", "q")


def test_answer_one_generates_from_the_rendered_prompt() -> None:
    b = Backend()
    prompt = get_prompt("answer@4")
    prep, result = answer_one(ctx(), "q1", "What was revenue?", b, prompt, None)  # type: ignore[arg-type]
    assert isinstance(prep, Prepared)
    assert result.text == "42 [1]"
    assert b.prompts == [prep.render(prompt, None)]


def test_answer_one_names_a_generation_failure() -> None:
    b = Backend(fail=BackendError("overloaded"))
    with pytest.raises(StageError) as e:
        answer_one(ctx(), "q1", "q", b, get_prompt("answer@4"), None)  # type: ignore[arg-type]
    assert e.value.stage == "generate"


def test_build_reads_the_retriever_spec() -> None:
    spec = {
        "kind": "vector",
        "k": 7,
        "decompose": True,
        "decompose_merge": "score",
        "scope": {"mode": "rules"},
    }
    c = AnswerContext.build(None, spec, Retriever())  # type: ignore[arg-type]
    assert (c.decompose, c.k, c.decompose_merge) == (True, 7, "score")
    assert c.fact_route is None  # no router, no facts


def test_refuse_unscoped_stops_before_route_and_retrieval() -> None:
    from filings_rag.harness.answer import Unscoped

    scope = Scope([1, 2], {"mode": "rules", "level": "corpus", "docs": []})
    r = Retriever()
    with pytest.raises(Unscoped) as e:
        prepare(ctx(retriever=r, scoper=Scoper(scope), refuse_unscoped=True), "q1", "Hi?")
    assert e.value.scope is scope and r.calls == []
    prepare(ctx(retriever=r, scoper=Scoper(scope)), "q1", "Hi?")  # benchmarks: off by default
    assert r.calls == [[1, 2]]
