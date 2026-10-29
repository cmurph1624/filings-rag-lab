import os
import threading

import psycopg
import pytest
from psycopg.types.json import Jsonb

from filings_rag import db
from filings_rag.backends.base import BackendError, Result
from filings_rag.embed.base import Embeddings, InputType
from filings_rag.extract.html import EXTRACTOR
from filings_rag.harness.config import from_dict
from filings_rag.harness.prompts import get_prompt
from filings_rag.harness.run import GitInfo, check_resume, create_run, execute
from filings_rag.ingest.build import build
from filings_rag.ingest.naive_fixed import NaiveFixed
from filings_rag.pricing import Usage
from filings_rag.retrieve.base import Retrieval, RetrievalError
from filings_rag.retrieve.vector import VectorRetriever

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL"),
]
DOC = "ZZRUN_2022_10K"
QSET = "zz-run"
QIDS = [f"zz_run_{i}" for i in range(4)]
GIT = GitInfo("abc123", False)


class WordEmbedder:
    """One-hot on the number of the text's first word ("w8 w9 ..." -> index 8)."""

    name = "voyage-finance-2"  # priced, so query-embedding cost is computable
    dims = 1024

    def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        vecs = []
        for t in texts:
            v = [0.0] * 1024
            v[int(t.split()[0][1:]) % 1024] = 1.0
            vecs.append(v)
        return Embeddings(vecs, tokens=3 * len(texts))


class ZzRunPipe(NaiveFixed):
    name = "zz-run"


class FakeRetriever:
    def retrieve(self, query: str) -> Retrieval:
        return Retrieval([], embed_tokens=4, embedder="voyage-finance-2")


class FakeBackend:
    name = "fake"
    model = "claude-sonnet-5-5"

    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.prompts: list[str] = []
        self.lock = threading.Lock()

    def generate(self, system: str, prompt: str) -> Result:
        with self.lock:
            self.prompts.append(prompt)
        if any(q in prompt for q in self.fail):
            raise BackendError("usage limit")
        return Result("42", Usage(input_tokens=100, output_tokens=10), 0.01, self.model, "end_turn")


def cfg(**execution: int):  # type: ignore[no-untyped-def]
    return from_dict(
        {
            "name": "zz-run",
            "pipeline": "naive-fixed@1",
            "questions": QSET,
            "retriever": {"kind": "vector", "k": 3},
            "generation": {"backend": "fake", "model": "claude-sonnet-5-5", "prompt": "answer@1"},
            "judge": {"backend": "fake", "model": "claude-opus-5-5", "prompt": "judge@1"},
            "execution": execution,
        }
    )


@pytest.fixture
def conn():  # type: ignore[no-untyped-def]
    url = os.environ["DATABASE_URL"]
    db.migrate(url)
    with psycopg.connect(url) as c:
        c.execute(
            "INSERT INTO benchmark_docs (doc_name, company, doc_type, doc_period)"
            " VALUES (%s, 'ZZ', '10k', 2022) ON CONFLICT DO NOTHING", (DOC,),
        )  # fmt: skip
        c.execute(
            "INSERT INTO documents (accession, doc_name, cik, company, form_type, filing_date,"
            " source, raw_dir, parts, page_offsets, body, extractor) VALUES"
            " ('zzrun-1', %s, 1, 'ZZ', '10-K', '2023-01-01', 'edgar', 'x', %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            (DOC, Jsonb([]), [0], " ".join(f"w{i}" for i in range(25)), EXTRACTOR),
        )
        for qid in QIDS:
            c.execute(
                "INSERT INTO questions (id, benchmark, company, doc_name, question_type, question,"
                " answer, evidence) VALUES (%s, %s, 'ZZ', %s, 'metrics', %s, '42', '[]')"
                " ON CONFLICT DO NOTHING",
                (qid, QSET, DOC, f"question {qid}?"),
            )
        c.commit()
        yield c
        c.rollback()
        c.execute("DELETE FROM runs WHERE name = 'zz-run'")
        c.execute("DELETE FROM questions WHERE benchmark = %s", (QSET,))
        c.execute("DROP TABLE IF EXISTS chunks__zz_run__v1")
        c.execute("DELETE FROM pipelines WHERE name = 'zz-run'")
        c.execute("DELETE FROM documents WHERE accession = 'zzrun-1'")
        c.execute("DELETE FROM benchmark_docs WHERE doc_name = %s", (DOC,))
        c.commit()


def run(conn, backend, *, limit=None, run_id=None, **execution):  # type: ignore[no-untyped-def]
    c = cfg(**execution)
    rid = run_id or create_run(conn, c, GIT)
    stats = execute(
        conn, rid, c, FakeRetriever(), backend, get_prompt("answer@1"), GIT, limit=limit,
        log=lambda s: None,
    )  # fmt: skip
    return rid, stats


def test_vector_retriever_ranks_nearest_chunk_first(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    e = WordEmbedder()
    build(conn, ZzRunPipe(version=1, window_words=10, overlap_words=2), e, docs=[DOC], log=print)
    for search in ("exact", "hnsw"):
        r = VectorRetriever(conn, "chunks__zz_run__v1", e, k=2, search=search).retrieve("w8")
        assert r.hits[0].text.split()[0] == "w8", search
        assert r.hits[0].score == pytest.approx(1.0) and r.hits[0].doc_name == DOC
        assert len(r.hits) == 2 and r.embed_tokens == 3
    with pytest.raises(ValueError, match="search"):
        VectorRetriever(conn, "chunks__zz_run__v1", e, search="ivf")


def test_run_records_answers_and_resume_skips_them(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    b = FakeBackend()
    rid, s = run(conn, b, limit=2)
    assert (s.answered, s.failed, s.skipped, s.finished) == (2, 0, 0, False)
    row = conn.execute(
        "SELECT answer, input_tokens, embed_tokens, gen_cost_usd > 0, git_sha FROM run_questions"
        " WHERE run_id = %s ORDER BY question_id LIMIT 1", (rid,),
    ).fetchone()  # fmt: skip
    assert row == ("42", 100, 4, True, "abc123")
    _, again = run(conn, b, limit=2, run_id=rid)
    assert (again.answered, again.skipped, len(b.prompts)) == (0, 2, 2)
    _, rest = run(conn, b, run_id=rid)
    assert (rest.answered, rest.skipped, rest.finished) == (2, 2, True)
    assert conn.execute("SELECT finished_at IS NOT NULL FROM runs WHERE id = %s", (rid,)).fetchone()
    assert conn.execute("SELECT config_hash FROM runs WHERE id = %s", (rid,)).fetchone() == (
        cfg().hash,
    )


def test_failures_are_recorded_then_retried(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    rid, s = run(conn, FakeBackend(fail={QIDS[1]}))
    assert (s.answered, s.failed, s.finished) == (3, 1, False)
    err = conn.execute(
        "SELECT error FROM run_questions WHERE run_id = %s AND question_id = %s", (rid, QIDS[1])
    ).fetchone()
    assert err is not None and "usage limit" in err[0]
    b = FakeBackend()
    _, again = run(conn, b, run_id=rid)
    assert (again.answered, again.skipped, again.finished, len(b.prompts)) == (1, 3, True, 1)
    row = conn.execute(
        "SELECT attempts, error, embed_tokens FROM run_questions"
        " WHERE run_id = %s AND question_id = %s", (rid, QIDS[1]),
    ).fetchone()  # fmt: skip
    assert row == (2, None, 8)  # query-embedding spend from both attempts is kept


def test_run_stops_after_consecutive_failures(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    b = FakeBackend(fail=set(QIDS))
    _, s = run(conn, b, concurrency=1, max_consecutive_failures=2)
    assert s.stopped_early and s.failed == 2 and len(b.prompts) == 2


def test_resume_refuses_a_different_config(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    rid = create_run(conn, cfg(), GIT)
    other = from_dict({**cfg().raw, "retriever": {"kind": "vector", "k": 5}})
    with pytest.raises(ValueError, match="different config"):
        check_resume(conn, rid, other)
    check_resume(conn, rid, cfg(concurrency=4))  # execution settings don't matter


def test_answer_is_recorded_once_even_if_logging_fails(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    c = cfg(concurrency=1)
    rid = create_run(conn, c, GIT)

    def log(line: str) -> None:
        raise BrokenPipeError("stdout closed")

    with pytest.raises(BrokenPipeError):
        execute(conn, rid, c, FakeRetriever(), FakeBackend(), get_prompt("answer@1"), GIT,
                limit=1, log=log)  # fmt: skip
    conn.rollback()
    row = conn.execute(
        "SELECT attempts, error, answer, embed_tokens FROM run_questions WHERE run_id = %s", (rid,)
    ).fetchone()
    assert row == (1, None, "42", 4)


def test_retrieval_failure_keeps_query_embedding_spend(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    class Broken:
        def retrieve(self, query: str) -> Retrieval:
            raise RetrievalError("relation does not exist", 7, "voyage-finance-2")

    c = cfg()
    rid = create_run(conn, c, GIT)
    s = execute(conn, rid, c, Broken(), FakeBackend(), get_prompt("answer@1"), GIT, limit=1,
                log=lambda s: None)  # fmt: skip
    assert s.failed == 1 and s.embed_tokens == 7
    row = conn.execute(
        "SELECT embed_tokens, embed_cost_usd > 0, error LIKE 'retrieval:%%' FROM run_questions"
        " WHERE run_id = %s", (rid,),
    ).fetchone()  # fmt: skip
    assert row == (7, True, True)


def test_vector_retriever_wraps_db_errors_with_spend(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    r = VectorRetriever(conn, "chunks__does_not_exist__v1", WordEmbedder())
    with pytest.raises(RetrievalError) as info:
        r.retrieve("w1")
    assert info.value.embed_tokens == 3
    conn.rollback()


class FakeJudge:
    name = "fake"
    model = "claude-opus-5-5"

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.calls = 0

    def generate(self, system: str, prompt: str) -> Result:
        self.calls += 1
        text = next((r for q, r in self.replies.items() if q in prompt), '{"verdict": "correct"}')
        return Result(text, Usage(input_tokens=50, output_tokens=5), 0.02, self.model, "end_turn")


def test_judge_then_report(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    from filings_rag.eval.judge import judge_run
    from filings_rag.eval.report import render, summarize
    from filings_rag.harness.prompts import get_judge_prompt

    c = cfg()
    rid, _ = run(conn, FakeBackend(fail={QIDS[3]}))
    j = FakeJudge({QIDS[1]: '{"verdict": "incorrect", "rationale": "wrong year"}', QIDS[2]: "??"})
    s = judge_run(conn, rid, c.judge_key, j, get_judge_prompt("judge@1"), log=lambda s: None)
    assert (s.judged, s.failed, s.skipped, j.calls) == (2, 1, 0, 3)  # QIDS[3] has no answer
    again = FakeJudge({})
    s2 = judge_run(conn, rid, c.judge_key, again, get_judge_prompt("judge@1"), log=lambda s: None)
    assert (s2.judged, s2.skipped, again.calls) == (1, 2, 1)  # only the unparseable one retried
    assert conn.execute(
        "SELECT attempts, verdict, input_tokens FROM judgments"
        " WHERE run_id = %s AND question_id = %s",
        (rid, QIDS[2]),
    ).fetchone() == (2, "correct", 100)

    summary = summarize(conn, rid)
    assert (summary.total, summary.answered, summary.errors, summary.correct) == (4, 3, 1, 2)
    assert summary.verdicts["incorrect"] == 1
    plain = summary.gen_cost["plain API"]
    assert plain == pytest.approx(3 * (100 * 2 + 10 * 10) / 1e6)
    assert summary.gen_cost["Batch API"] == pytest.approx(plain / 2)
    assert summary.retrieval == [] and summary.unlabelled == 3  # no evidence labels in this set
    md = render([summary, summary])
    assert "open subset" in md and "| accuracy | 50.0% (2/4) | 50.0% (2/4) |" in md
    assert "Batch API: per correct answer" in md


def test_retrieval_only_run(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    from filings_rag.eval.judge import judge_run
    from filings_rag.eval.report import render, summarize
    from filings_rag.harness.prompts import get_judge_prompt

    c = from_dict({k: v for k, v in cfg().raw.items() if k not in ("generation", "judge")})
    rid = create_run(conn, c, GIT)
    s = execute(conn, rid, c, FakeRetriever(), None, None, GIT, log=lambda s: None)
    assert (s.answered, s.failed, s.finished) == (4, 0, True)
    rows = conn.execute(
        "SELECT answer, embed_tokens, error FROM run_questions WHERE run_id = %s", (rid,)
    ).fetchall()
    assert rows == [(None, 4, None)] * 4
    j = FakeJudge({})
    judged = judge_run(conn, rid, "x", j, get_judge_prompt("judge@1"), log=lambda s: None)
    assert judged.judged == 0 and j.calls == 0  # nothing to judge
    md = render([summarize(conn, rid)], retrieval_only=True)
    assert "## Retrieval" in md and "## Answers" not in md and "## Cost" not in md
    full = render([summarize(conn, rid)])
    assert "| accuracy | – |" in full and "retrieval only" in full
