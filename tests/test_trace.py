"""Tracing: the step tree `prepare` records, the embed/rerank proxies, and the Langfuse
adapter against an in-memory exporter (no network)."""

import json
import uuid
from typing import Any

import pytest
from conftest import RecordingTracer
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from filings_rag import trace
from filings_rag.config import Settings
from filings_rag.embed.base import Embeddings, InputType
from filings_rag.harness.answer import AnswerContext, StageError, Unscoped, prepare
from filings_rag.pricing import embedding_cost, rerank_cost
from filings_rag.rerank.base import Reranked
from filings_rag.retrieve.base import Hit, Retrieval
from filings_rag.retrieve.rerank import RerankingRetriever
from filings_rag.retrieve.scope import Scope
from filings_rag.route.plan import RoutePlan
from filings_rag.serve.service import make_tracer

# --- fakes ------------------------------------------------------------------------------------


class Embedder:
    name = "voyage-finance-2"
    dims = 2

    def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        return Embeddings([[0.1, 0.2] for _ in texts], tokens=7)


class Reranker:
    name = "rerank-3"

    def rerank(self, query: str, texts: list[str], top_k: int) -> Reranked:
        return Reranked([(1, 0.9), (0, 0.5)][:top_k], tokens=400)


class EmbeddingRetriever:
    """Embeds the query (through whatever embedder it's given), then returns two hits."""

    def __init__(self, embedder: Any, fail: bool = False) -> None:
        self.embedder = embedder
        self.fail = fail

    def retrieve(self, query: str, documents: list[int] | None = None) -> Retrieval:
        out = self.embedder.embed([query], "query")
        if self.fail:
            raise RuntimeError("index down")
        hits = [Hit(i, f"DOC{i}", 1, 1.0 - i / 10, f"text {i}") for i in (1, 2)]
        return Retrieval(hits, out.tokens, self.embedder.name)


class Scoper:
    def __init__(self, level: str = "filings") -> None:
        self.level = level

    def scope(self, question_id: str, question: str) -> Scope:
        return Scope([1, 2], {"mode": "rules", "level": self.level, "docs": ["ZZ_2022_10K"]})


class Router:
    def route(self, question_id: str, question: str) -> tuple[RoutePlan, dict[str, Any] | None]:
        return RoutePlan("prose"), None


class Facts:
    def run(self, plan: RoutePlan, scope: Scope | None) -> tuple[dict[str, Any], str]:
        return {"plan": plan.to_json(), "anchor": None, "facts": []}, ""


def traced_ctx(tracer: RecordingTracer, **kw: Any) -> AnswerContext:
    embedder = trace.TracedEmbedder(Embedder(), tracer)
    base = EmbeddingRetriever(embedder, fail=kw.pop("fail", False))
    retriever = RerankingRetriever(base, trace.TracedReranker(Reranker(), tracer), k=2)
    kw = {"scoper": Scoper(), "router": Router(), "fact_route": Facts()} | kw
    return AnswerContext(retriever=retriever, tracer=tracer, **kw)


# --- the step tree ----------------------------------------------------------------------------


def test_prepare_records_each_step_in_order(tracer: RecordingTracer) -> None:
    with tracer.trace("t1", "ask"):
        prep = prepare(traced_ctx(tracer), "q1", "ZZ FY2022 revenue?")
    root = tracer.roots[0]
    assert root.names() == ["scope", "route", "facts", "retrieve"]
    assert root.find("scope").fields["output"]["docs"] == ["ZZ_2022_10K"]
    assert root.find("route").fields["output"] == RoutePlan("prose").to_json()
    retrieve = root.find("retrieve")
    assert retrieve.kind == "retriever" and retrieve.names() == ["embed", "rerank"]
    assert [h["chunk_id"] for h in retrieve.fields["output"]] == [
        h.chunk_id for h in prep.retrieval.hits
    ]


def test_embed_and_rerank_steps_carry_tokens_and_list_price_cost(tracer: RecordingTracer) -> None:
    prepare(traced_ctx(tracer), "q1", "q")
    embed, rerank = next(n for n in tracer.roots if n.name == "retrieve").children
    assert (embed.kind, embed.fields["model"]) == ("embedding", "voyage-finance-2")
    assert embed.fields["usage"] == {"input": 7}
    assert embed.fields["cost"] == {"total": pytest.approx(embedding_cost("voyage-finance-2", 7))}
    assert rerank.kind == "generation" and rerank.fields["usage"] == {"input": 400}
    assert rerank.fields["cost"] == {"total": pytest.approx(rerank_cost("rerank-3", 400))}
    assert rerank.fields["output"] == {"order": [1, 0]}


def test_a_failed_step_is_marked_and_still_raises(tracer: RecordingTracer) -> None:
    with pytest.raises(StageError):
        prepare(traced_ctx(tracer, fail=True), "q1", "q")
    retrieve = next(n for n in tracer.roots if n.name == "retrieve")
    assert isinstance(retrieve.error, RuntimeError)
    assert retrieve.names() == ["embed"]  # the embedding was made (and paid) before the failure


def test_an_unscoped_question_stops_after_the_scope_step(tracer: RecordingTracer) -> None:
    ctx = traced_ctx(tracer, scoper=Scoper("corpus"), refuse_unscoped=True)
    with pytest.raises(Unscoped):
        prepare(ctx, "q1", "Capital of France?")
    assert [n.name for n in tracer.roots] == ["scope"]


def test_benchmarks_trace_nothing_by_default() -> None:
    assert AnswerContext(retriever=EmbeddingRetriever(Embedder())).tracer is trace.NOOP  # type: ignore[arg-type]
    assert make_tracer(Settings(_env_file=None), "abc") is trace.NOOP  # type: ignore[call-arg]


def test_proxies_keep_the_names_the_factory_checks() -> None:
    e = trace.TracedEmbedder(Embedder(), trace.NOOP)
    r = trace.TracedReranker(Reranker(), trace.NOOP)
    assert (e.name, e.dims, r.name) == ("voyage-finance-2", 2, "rerank-3")


def test_compact_truncates_long_values() -> None:
    assert trace.compact({"a": 1}) == {"a": 1}
    long = trace.compact({"docs": ["x" * 100] * 100}, limit=50)
    assert isinstance(long, str) and len(long) == 51 and long.endswith("…")


# --- Langfuse adapter -------------------------------------------------------------------------


@pytest.fixture
def langfuse() -> Any:
    exporter = InMemorySpanExporter()
    tracer = trace.LangfuseTracer.connect(
        f"pk-lf-test-{uuid.uuid4().hex}",  # the SDK shares state per key
        "sk-lf-test",
        "http://127.0.0.1:9",  # nothing listens; spans go to the exporter
        environment="test",
        release="abc123",
        span_exporter=exporter,
    )
    yield tracer, exporter
    tracer.shutdown()


def spans(exporter: InMemorySpanExporter) -> dict[str, Any]:
    return {s.name: s for s in exporter.get_finished_spans()}


TRACE_ID = "0123456789abcdef0123456789abcdef"


def test_langfuse_nests_steps_under_the_query_trace(langfuse: Any) -> None:
    tracer, exporter = langfuse
    with tracer.trace(TRACE_ID, "ask", input="q?", tags=["test"], metadata={"k": "v"}) as root:
        with tracer.observe("generate", kind="generation", model="claude-sonnet-5-5") as gen:
            gen.update(output="42", usage={"input": 10, "output": 2}, cost={"total": 0.003})
        root.update(output={"status": "answered"})
    tracer.shutdown()
    got = spans(exporter)
    ask, generate = got["ask"], got["generate"]
    assert format(ask.context.trace_id, "032x") == TRACE_ID
    assert generate.context.trace_id == ask.context.trace_id
    assert generate.parent is not None and generate.parent.span_id == ask.context.span_id
    a = generate.attributes
    assert a["langfuse.observation.type"] == "generation"
    assert json.loads(a["langfuse.observation.usage_details"]) == {"input": 10, "output": 2}
    assert json.loads(a["langfuse.observation.cost_details"]) == {"total": 0.003}
    assert tuple(ask.attributes["langfuse.trace.tags"]) == ("test",)
    assert ask.attributes["langfuse.environment"] == "test"


def test_langfuse_marks_a_raising_step_as_an_error(langfuse: Any) -> None:
    tracer, exporter = langfuse
    with pytest.raises(ValueError), tracer.trace(TRACE_ID, "ask"), tracer.observe("retrieve"):
        raise ValueError("index down")
    tracer.shutdown()
    attrs = spans(exporter)["retrieve"].attributes
    assert attrs["langfuse.observation.level"] == "ERROR"
    assert "index down" in attrs["langfuse.observation.status_message"]


class BrokenClient:
    def __init__(self) -> None:
        self.scored: list[dict[str, Any]] = []

    def start_as_current_observation(self, **kw: Any) -> Any:
        raise RuntimeError("sdk broke")

    def create_score(self, **kw: Any) -> None:
        self.scored.append(kw)
        raise RuntimeError("sdk broke")

    def shutdown(self) -> None:
        raise RuntimeError("sdk broke")


def test_a_failing_sdk_never_breaks_the_answer(caplog: pytest.LogCaptureFixture) -> None:
    client = BrokenClient()
    tracer = trace.LangfuseTracer(client)
    ran = []
    with tracer.trace(TRACE_ID, "ask") as root, tracer.observe("generate") as span:
        span.update(output="x")
        root.update(output="y")
        ran.append(True)
    tracer.score(TRACE_ID, "user-feedback", 0.0, comment="wrong", score_id="s1")
    tracer.shutdown()
    assert ran == [True]
    assert client.scored[0] == {
        "name": "user-feedback",
        "value": 0.0,
        "trace_id": TRACE_ID,
        "score_id": "s1",
        "data_type": "NUMERIC",
        "comment": "wrong",
    }
    assert "trace start failed" in caplog.text and "trace score failed" in caplog.text
