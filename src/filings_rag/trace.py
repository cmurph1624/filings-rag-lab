"""Tracing behind a thin interface: a no-op by default, Langfuse when the serving app has keys.

One trace per served question, its id the query's uuid in hex (so `served_queries.trace_id`
and feedback scores need no lookup). Steps nest as observations: scope, route, facts,
retrieve (with embed and rerank inside), generate. Benchmark runs and tests use `NOOP`, so the
answer path costs nothing extra there.

Tracing never breaks an answer: `LangfuseTracer` logs and carries on when the SDK fails, and
the SDK exports in a background thread.
"""

import contextlib
import json
import logging
from collections.abc import Iterator
from typing import Any, Protocol

from filings_rag.embed.base import Embedder, Embeddings, InputType
from filings_rag.pricing import embedding_cost, rerank_cost
from filings_rag.rerank.base import Reranked, Reranker

log = logging.getLogger(__name__)


class Span(Protocol):
    def update(self, **fields: Any) -> None:
        """Fields: output, metadata, usage (token counts), cost ({"total": usd}), level,
        status_message, model."""
        ...


class Tracer(Protocol):
    enabled: bool

    def trace(
        self,
        trace_id: str,
        name: str,
        *,
        input: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> contextlib.AbstractContextManager[Span]:
        """The root of a trace; `observe` calls inside it nest under it."""
        ...

    def observe(
        self, name: str, *, kind: str = "span", **fields: Any
    ) -> contextlib.AbstractContextManager[Span]:
        """A step inside the current trace. `kind`: span, generation, embedding, retriever.
        An exception leaving the block marks the step as an error and propagates."""
        ...

    def score(
        self,
        trace_id: str,
        name: str,
        value: float,
        *,
        comment: str | None = None,
        score_id: str | None = None,
    ) -> None: ...

    def shutdown(self) -> None:
        """Flush what's queued. Called once, when the process stops."""
        ...


class _NoopSpan:
    def update(self, **fields: Any) -> None:
        pass


NOOP_SPAN = _NoopSpan()


class NoopTracer:
    enabled = False

    @contextlib.contextmanager
    def trace(self, trace_id: str, name: str, **fields: Any) -> Iterator[Span]:
        yield NOOP_SPAN

    @contextlib.contextmanager
    def observe(self, name: str, *, kind: str = "span", **fields: Any) -> Iterator[Span]:
        yield NOOP_SPAN

    def score(self, trace_id: str, name: str, value: float, **fields: Any) -> None:
        pass

    def shutdown(self) -> None:
        pass


NOOP: Tracer = NoopTracer()

# Our field names → the SDK's.
_FIELDS = {"usage": "usage_details", "cost": "cost_details"}


def _sdk(fields: dict[str, Any]) -> dict[str, Any]:
    return {_FIELDS.get(k, k): v for k, v in fields.items() if v is not None}


class _LangfuseSpan:
    def __init__(self, obs: Any) -> None:
        self._obs = obs

    def update(self, **fields: Any) -> None:
        try:
            self._obs.update(**_sdk(fields))
        except Exception:
            log.exception("trace update failed")


class LangfuseTracer:
    enabled = True

    def __init__(self, client: Any) -> None:
        self._client = client  # a langfuse.Langfuse

    @classmethod
    def connect(
        cls,
        public_key: str,
        secret_key: str,
        host: str,
        *,
        environment: str,
        release: str,
        **client_kw: Any,  # tests pass an in-memory span_exporter
    ) -> "LangfuseTracer":
        from langfuse import Langfuse, is_langfuse_span

        return cls(
            Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                base_url=host,
                environment=environment,
                release=release,
                # Only our steps: the Anthropic SDK's own span would duplicate `generate` (and
                # its cost) in every trace.
                should_export_span=is_langfuse_span,
                **client_kw,
            )
        )

    @contextlib.contextmanager
    def trace(
        self,
        trace_id: str,
        name: str,
        *,
        input: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[Span]:
        from langfuse import propagate_attributes

        with contextlib.ExitStack() as stack:
            try:  # tags and the trace name are trace attributes, inherited by every step
                stack.enter_context(propagate_attributes(tags=tags, trace_name=name))
            except Exception:
                log.exception("trace attributes failed")
            with self.observe(
                name, input=input, metadata=metadata, trace_context={"trace_id": trace_id}
            ) as span:
                yield span

    @contextlib.contextmanager
    def observe(self, name: str, *, kind: str = "span", **fields: Any) -> Iterator[Span]:
        try:
            cm = self._client.start_as_current_observation(name=name, as_type=kind, **_sdk(fields))
            obs = cm.__enter__()
        except Exception:
            log.exception("trace start failed: %s", name)
            yield NOOP_SPAN
            return
        span = _LangfuseSpan(obs)
        try:
            yield span
        except BaseException as e:
            span.update(level="ERROR", status_message=f"{type(e).__name__}: {e}"[:500])
            _exit(cm, e)
            raise
        _exit(cm, None)

    def score(
        self,
        trace_id: str,
        name: str,
        value: float,
        *,
        comment: str | None = None,
        score_id: str | None = None,
    ) -> None:
        try:
            self._client.create_score(
                name=name,
                value=value,
                trace_id=trace_id,
                score_id=score_id,
                data_type="NUMERIC",
                comment=comment,
            )
        except Exception:
            log.exception("trace score failed")

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception:
            log.exception("trace shutdown failed")


def _exit(cm: Any, e: BaseException | None) -> None:
    try:
        cm.__exit__(type(e) if e else None, e, e.__traceback__ if e else None)
    except Exception:
        log.exception("trace end failed")


class TracedEmbedder:
    """An embedder whose calls appear as `embed` steps, with tokens and cost."""

    def __init__(self, inner: Embedder, tracer: Tracer) -> None:
        self._inner = inner
        self._tracer = tracer
        self.name = inner.name
        self.dims = inner.dims

    def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        with self._tracer.observe(
            "embed", kind="embedding", model=self.name, input={"texts": len(texts)}
        ) as span:
            out = self._inner.embed(texts, input_type)
            span.update(
                usage={"input": out.tokens},
                cost={"total": embedding_cost(self.name, out.tokens)},
            )
            return out


class TracedReranker:
    """A reranker whose calls appear as `rerank` steps, with tokens, cost and the new order."""

    def __init__(self, inner: Reranker, tracer: Tracer) -> None:
        self._inner = inner
        self._tracer = tracer
        self.name = inner.name

    def rerank(self, query: str, texts: list[str], top_k: int) -> Reranked:
        # A model call, typed "generation" so Langfuse adds its cost to the trace's total.
        with self._tracer.observe(
            "rerank",
            kind="generation",
            model=self.name,
            input={"candidates": len(texts), "top_k": top_k},
        ) as span:
            out = self._inner.rerank(query, texts, top_k)
            span.update(
                output={"order": [i for i, _ in out.order]},
                usage={"input": out.tokens},
                cost={"total": rerank_cost(self.name, out.tokens)},
            )
            return out


def compact(value: Any, limit: int = 4000) -> Any:
    """`value` as is when its JSON is short, else truncated JSON text (keeps traces small)."""
    text = json.dumps(value, default=str)
    return value if len(text) <= limit else text[:limit] + "…"
