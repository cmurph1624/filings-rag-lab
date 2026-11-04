"""The serving app: config, citations, and the HTTP endpoints over a fake pipeline."""

import os
import tomllib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from filings_rag import db
from filings_rag.backends.base import BackendError, Result
from filings_rag.harness.answer import REPO_ROOT, AnswerContext
from filings_rag.harness.prompts import get_prompt
from filings_rag.pricing import Usage
from filings_rag.retrieve.base import Hit, Retrieval, RetrievalError
from filings_rag.retrieve.scope import Scope
from filings_rag.serve import citations
from filings_rag.serve import config as serve_config
from filings_rag.serve import service as service_mod
from filings_rag.serve.app import create_app
from filings_rag.serve.service import PUBLIC_ERROR, Service

# --- config -----------------------------------------------------------------------------------


def test_serve_config_is_run_846s_pipeline_with_the_api_backend() -> None:
    cfg = serve_config.load(REPO_ROOT / "configs" / "serve.toml")
    with (REPO_ROOT / "configs" / "p2-04-router-rules-facts.toml").open("rb") as fh:
        base = tomllib.load(fh)
    assert cfg.run.retriever == base["retriever"]
    assert cfg.run.router is not None and base["router"]["mode"] == "rules"
    assert cfg.run.pipeline == base["pipeline"] and cfg.run.corpus == base["corpus"]
    assert cfg.run.generation == {
        "backend": "anthropic-api",
        "model": base["generation"]["model"],
        "prompt": base["generation"]["prompt"],
    }
    assert cfg.max_question_chars == 500


def write(tmp: Path, text: str) -> Path:
    p = tmp / "serve.toml"
    p.write_text(text)
    return p


BASE = 'base = "configs/p2-04-router-rules-facts.toml"\n'


@pytest.mark.parametrize(
    ("text", "msg"),
    [
        (BASE + '[generation]\nbackend="claude-cli"\nmodel="m"\nprompt="answer@4"\n', "local"),
        ('[generation]\nbackend="anthropic-api"\nmodel="m"\nprompt="answer@4"\n', "base"),
        (
            BASE + '[generation]\nbackend="anthropic-api"\nmodel="m"\nprompt="answer@4"\n'
            "[serve]\nnope = 1\n",
            "unknown",
        ),  # fmt: skip
        (BASE + "[judge]\nx = 1\n", "unknown"),
    ],
)
def test_serve_config_rejects(tmp_path: Path, text: str, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        serve_config.load(write(tmp_path, text), REPO_ROOT)


# --- citations --------------------------------------------------------------------------------

DOCS = {
    "ZZ_2022_10K": citations.DocInfo(
        "ZZ_2022_10K",
        "ZZ Corp",
        "10-K",
        "2022-12-31",
        citations.edgar_url(66740, "0000066740-23-000014", "mmm-20221231.htm"),
    )
}


def hit(i: int, doc: str = "ZZ_2022_10K") -> Hit:
    return Hit(100 + i, doc, 10 + i, 0.9, "x" * 1000, "Item 7")


def test_edgar_url() -> None:
    assert citations.edgar_url(66740, "0000066740-23-000014", "mmm-20221231.htm") == (
        "https://www.sec.gov/Archives/edgar/data/66740/000006674023000014/mmm-20221231.htm"
    )
    assert citations.edgar_url(1, "0000000001-23-000001", None).endswith(
        "/1/000000000123000001/0000000001-23-000001-index.htm"
    )


def test_excerpts_mark_what_the_answer_cites() -> None:
    out = citations.excerpts(
        "Revenue rose [2], see also [1, 2] and [9].", [hit(1), hit(2), hit(3)], DOCS
    )
    assert [e["cited"] for e in out] == [True, True, False]
    assert out[0]["url"] == DOCS["ZZ_2022_10K"].url and out[0]["page"] == 11
    assert len(out[0]["snippet"]) == citations.SNIPPET_CHARS
    unknown = citations.excerpts("[1]", [hit(1, "OTHER")], DOCS)
    assert unknown[0]["url"] is None


def test_facts_link_their_anchor_filing() -> None:
    route = {
        "anchor": "ZZ_2022_10K",
        "facts": [
            {
                "metric": "capex",
                "period": "FY2022",
                "display": "$1.7B",
                "formula": "f",
                "error": None,
            },
            {
                "metric": "dpo",
                "period": "FY2022",
                "display": None,
                "formula": None,
                "error": "no AP",
            },
        ],
    }
    out = citations.facts(route, DOCS)
    assert [f["n"] for f in out] == ["F1", "F2"]
    assert out[0]["url"] == DOCS["ZZ_2022_10K"].url and out[1]["unavailable"] == "no AP"
    assert citations.facts({"anchor": None, "facts": []}, DOCS) == []
    assert citations.facts(None, DOCS) == []


# --- the app ----------------------------------------------------------------------------------

CONFIG_HASH = "zz-serve-test"


class Retriever:
    def retrieve(self, query: str, documents: list[int] | None = None) -> Retrieval:
        aux = {"rerank": {"model": "rerank-3", "tokens": 1000, "cost_usd": 0.00005}}
        return Retrieval([hit(1), hit(2)], 12, "voyage-finance-2", aux)


class Backend:
    name = "fake"
    model = "claude-sonnet-5-5"

    def __init__(self) -> None:
        self.fail = False
        self.prompts: list[str] = []

    def generate(self, system: str, prompt: str) -> Result:
        self.prompts.append(prompt)
        if self.fail:
            raise BackendError("anthropic API error: overloaded (secret detail)")
        usage = Usage(input_tokens=1000, output_tokens=100)
        return Result("ZZ's FY2022 revenue was $5B [1].", usage, 1.5, self.model, "end_turn")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    url = os.environ["DATABASE_URL"]
    db.migrate(url)
    with psycopg.connect(url) as c:
        yield c
        c.rollback()
    with psycopg.connect(url) as c:
        c.execute("DELETE FROM served_queries WHERE config_hash = %s", (CONFIG_HASH,))


@pytest.fixture
def backend() -> Backend:
    return Backend()


def service(backend: Backend, **kw: Any) -> Service:
    args: dict[str, Any] = {
        "ctx": AnswerContext(retriever=Retriever()),  # type: ignore[arg-type]
        "backend": backend,
        "prompt": get_prompt("answer@4"),
        "periods": None,
        "docs": DOCS,
        "index_conn": psycopg.connect(os.environ["DATABASE_URL"]),
        "app_url": os.environ["DATABASE_URL"],
        "config_hash": CONFIG_HASH,
        "pipeline": "nope@1",
        "git_sha": "abc123",
        "max_question_chars": 50,
    }
    return Service(**(args | kw))


@pytest.fixture
def client(conn: psycopg.Connection[Any], backend: Backend) -> Iterator[TestClient]:
    with TestClient(create_app(lambda: service(backend))) as c:
        yield c


def row(conn: psycopg.Connection[Any], qid: str) -> dict[str, Any]:
    cur = conn.execute("SELECT * FROM served_queries WHERE id = %s", (qid,))
    names = [d.name for d in cur.description or []]
    r = cur.fetchone()
    conn.commit()
    assert r is not None
    return dict(zip(names, r, strict=True))


@pytest.mark.db
@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL")
def test_ask_answers_cites_and_records(
    client: TestClient, conn: psycopg.Connection[Any], backend: Backend
) -> None:
    r = client.post("/ask", json={"question": "  ZZ FY2022 revenue?  "})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "answered" and body["answer"].endswith("[1].")
    assert [e["cited"] for e in body["excerpts"]] == [True, False]
    assert body["facts"] == [] and body["error"] is None
    rec = row(conn, body["query_id"])
    assert rec["question"] == "ZZ FY2022 revenue?"
    assert rec["status"] == "answered" and rec["git_sha"] == "abc123"
    assert (rec["input_tokens"], rec["output_tokens"], rec["embed_tokens"]) == (1000, 100, 12)
    # Sonnet 5.5 at $2/$10 per M: 0.002 + 0.001; rerank 0.00005; embedding 12 tok at $0.12/M
    assert rec["gen_cost_usd"] == pytest.approx(0.003)
    assert rec["rerank_cost_usd"] == pytest.approx(0.00005)
    assert rec["cost_usd"] == pytest.approx(0.003 + 0.00005 + 12 * 0.12e-6)
    assert body["cost_usd"] == pytest.approx(rec["cost_usd"], abs=1e-6)
    assert [h["chunk_id"] for h in rec["retrieved"]] == [101, 102]
    assert "ZZ FY2022 revenue?" in backend.prompts[0]


@pytest.mark.db
@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL")
def test_a_generation_failure_is_recorded_but_not_shown(
    client: TestClient, conn: psycopg.Connection[Any], backend: Backend
) -> None:
    backend.fail = True
    r = client.post("/ask", json={"question": "ZZ FY2022 revenue?"})
    assert r.status_code == 502
    body = r.json()
    assert body["status"] == "error" and body["error"] == PUBLIC_ERROR
    assert "secret" not in r.text
    rec = row(conn, body["query_id"])
    assert rec["status"] == "error" and "overloaded" in rec["error"]
    assert rec["gen_cost_usd"] == 0 and rec["rerank_cost_usd"] > 0  # retrieval was still paid


@pytest.mark.db
@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL")
@pytest.mark.parametrize("question", ["x" * 51, "   "])
def test_too_long_or_empty_is_refused_without_a_model_call(
    client: TestClient, backend: Backend, question: str
) -> None:
    r = client.post("/ask", json={"question": question})
    assert r.status_code == 422
    assert backend.prompts == []


@pytest.mark.db
@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL")
def test_feedback_is_recorded_and_replaced(
    client: TestClient, conn: psycopg.Connection[Any]
) -> None:
    qid = client.post("/ask", json={"question": "ZZ FY2022 revenue?"}).json()["query_id"]
    assert client.post("/feedback", json={"query_id": qid, "rating": "down"}).status_code == 204
    r = client.post("/feedback", json={"query_id": qid, "rating": "up", "comment": "ok now"})
    assert r.status_code == 204
    got = conn.execute(
        "SELECT rating, comment FROM feedback WHERE query_id = %s", (qid,)
    ).fetchall()
    conn.commit()
    assert got == [("up", "ok now")]
    unknown = client.post("/feedback", json={"query_id": str(uuid.uuid4()), "rating": "up"})
    assert unknown.status_code == 404
    bad = client.post("/feedback", json={"query_id": qid, "rating": "meh"})
    assert bad.status_code == 422


@pytest.mark.db
@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL")
def test_health_version_and_page(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get("/healthz").status_code == 503  # "nope@1" is not a built pipeline
    monkeypatch.setattr(service_mod, "pipeline_index", lambda conn, ref: ("t", "e", 1))
    assert client.get("/healthz").json() == {"ok": True, "pipeline": "nope@1"}
    assert client.get("/version").json() == {
        "sha": "abc123",
        "config_hash": CONFIG_HASH,
        "pipeline": "nope@1",
        "model": "claude-sonnet-5-5",
    }
    page = client.get("/")
    assert page.status_code == 200 and "Filings RAG Lab" in page.text


class FailingRetriever:
    def retrieve(self, query: str, documents: list[int] | None = None) -> Retrieval:
        raise RetrievalError("db down", embed_tokens=9, embedder="voyage-finance-2")


class OneScoper:
    def scope(self, question_id: str, question: str) -> Scope:
        return Scope([1], {"mode": "rules", "level": "filings", "docs": ["ZZ_2022_10K"]})


@pytest.mark.db
@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL")
def test_a_retrieval_failure_keeps_its_scope_and_embedding_spend(
    conn: psycopg.Connection[Any], backend: Backend
) -> None:
    ctx = AnswerContext(retriever=FailingRetriever(), scoper=OneScoper())  # type: ignore[arg-type]
    with TestClient(create_app(lambda: service(backend, ctx=ctx))) as c:
        r = c.post("/ask", json={"question": "ZZ FY2022 revenue?"})
    assert r.status_code == 502 and backend.prompts == []
    rec = row(conn, r.json()["query_id"])
    assert rec["error"].startswith("retrieval: RetrievalError")
    assert rec["scope"]["docs"] == ["ZZ_2022_10K"]
    assert rec["embed_tokens"] == 9 and rec["cost_usd"] == pytest.approx(9 * 0.12e-6)


@pytest.mark.db
@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL")
def test_a_failed_record_write_still_returns_the_paid_answer(
    backend: Backend, caplog: pytest.LogCaptureFixture
) -> None:
    bad = "postgresql://nobody:x@127.0.0.1:1/none"
    with TestClient(create_app(lambda: service(backend, app_url=bad))) as c:
        r = c.post("/ask", json={"question": "ZZ FY2022 revenue?"})
    assert r.status_code == 200 and r.json()["answer"].endswith("[1].")
    assert "served_queries write failed" in caplog.text and r.json()["query_id"] in caplog.text
