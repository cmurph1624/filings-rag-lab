import os

import psycopg
import pytest
from psycopg.types.json import Jsonb

from filings_rag import db
from filings_rag.embed.base import Embeddings, InputType
from filings_rag.extract.html import EXTRACTOR
from filings_rag.ingest.base import Chunk, Doc
from filings_rag.ingest.build import build, register
from filings_rag.ingest.naive_fixed import NaiveFixed

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL"),
]
DOC = "ZZTEST_2022_10K"
DOC2 = "ZZTEST_2023_10K"


class FakeEmbedder:
    name = "fake-embed"
    dims = 1024

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        self.calls += 1
        return Embeddings([[1.0] + [0.0] * 1023 for _ in texts], tokens=5 * len(texts))


class FailingEmbedder(FakeEmbedder):
    def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        if self.calls == 1:
            raise RuntimeError("embedding API down")
        return super().embed(texts, input_type)


class EmptyDocPipe(NaiveFixed):
    name = "zz-test"

    def chunk(self, doc: Doc) -> list[Chunk]:
        return []


class ZzPipe(NaiveFixed):
    name = "zz-test"


@pytest.fixture
def conn():  # type: ignore[no-untyped-def]
    url = os.environ["DATABASE_URL"]
    db.migrate(url)
    with psycopg.connect(url) as c:
        for acc, name in (("zz-1", DOC), ("zz-2", DOC2)):
            c.execute(
                "INSERT INTO benchmark_docs (doc_name, company, doc_type, doc_period)"
                " VALUES (%s, 'ZZ', '10k', 2022) ON CONFLICT DO NOTHING", (name,),
            )  # fmt: skip
            c.execute(
                "INSERT INTO documents (accession, doc_name, cik, company, form_type, filing_date,"
                " source, raw_dir, parts, page_offsets, body, extractor) VALUES"
                " (%s, %s, 1, 'ZZ', '10-K', '2023-01-01', 'edgar', 'x', %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (acc, name, Jsonb([]), [0], " ".join(f"w{i}" for i in range(25)), EXTRACTOR),
            )
        c.commit()
        yield c
        c.rollback()
        c.execute("DROP TABLE IF EXISTS chunks__zz_test__v1, chunks__zz_test__v2")
        c.execute("DELETE FROM pipelines WHERE name = 'zz-test'")
        c.execute("DELETE FROM documents WHERE accession IN ('zz-1', 'zz-2')")
        c.execute("DELETE FROM benchmark_docs WHERE doc_name IN (%s, %s)", (DOC, DOC2))
        c.commit()


def test_build_is_resumable_and_records_tokens(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    e = FakeEmbedder()
    p = ZzPipe(version=1, window_words=10, overlap_words=2)
    stats = build(conn, p, e, docs=[DOC], log=lambda s: None)
    assert (stats.documents, stats.chunks, stats.embed_tokens) == (1, 3, 15)
    again = build(conn, p, e, docs=[DOC], log=lambda s: None)
    assert again.documents == 0 and e.calls == 1  # already built: nothing re-embedded
    row = conn.execute("SELECT count(*), min(page) FROM chunks__zz_test__v1").fetchone()
    assert row == (3, 1)
    assert conn.execute(
        "SELECT embed_tokens FROM pipelines WHERE name = 'zz-test' AND version = 1"
    ).fetchone() == (15,)


def test_changed_params_need_a_new_version(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    register(conn, ZzPipe(version=1, window_words=10, overlap_words=2), FakeEmbedder())
    with pytest.raises(ValueError, match="bump the version"):
        register(conn, ZzPipe(version=1, window_words=12, overlap_words=2), FakeEmbedder())
    conn.rollback()
    assert register(conn, ZzPipe(version=2, window_words=12, overlap_words=2), FakeEmbedder())


def test_each_document_commits_on_its_own(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """One build over two docs: when embedding fails on the second, the first stays committed."""
    p = ZzPipe(version=1, window_words=10, overlap_words=2)
    with pytest.raises(RuntimeError):
        build(conn, p, FailingEmbedder(), docs=[DOC, DOC2], log=lambda s: None)
    conn.rollback()
    with psycopg.connect(os.environ["DATABASE_URL"]) as other:  # sees only committed rows
        n = other.execute("SELECT count(*) FROM chunks__zz_test__v1").fetchone()
    assert n == (3,)
    # and a rerun picks up only the missing document
    stats = build(conn, p, FakeEmbedder(), docs=[DOC, DOC2], log=lambda s: None)
    assert stats.documents == 1


def test_empty_document_is_skipped_not_retried(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    e = FakeEmbedder()
    stats = build(conn, EmptyDocPipe(version=1), e, docs=[DOC], log=lambda s: None)
    assert (stats.documents, stats.empty, e.calls) == (0, 1, 0)


def test_tokens_recorded_even_when_insert_fails(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    class BadVectors(FakeEmbedder):
        def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
            self.calls += 1
            return Embeddings([[1.0] * 3 for _ in texts], tokens=40)  # wrong dims: insert fails

    p = ZzPipe(version=1, window_words=10, overlap_words=2)
    with pytest.raises(psycopg.Error):
        build(conn, p, BadVectors(), docs=[DOC], log=lambda s: None)
    conn.rollback()
    assert conn.execute(
        "SELECT embed_tokens FROM pipelines WHERE name = 'zz-test' AND version = 1"
    ).fetchone() == (40,)
