import os

import psycopg
import pytest
from psycopg.types.json import Jsonb

from filings_rag import corpora, db
from filings_rag.embed.base import Embeddings, InputType
from filings_rag.extract.html import EXTRACTOR
from filings_rag.ingest.build import build
from filings_rag.ingest.naive_fixed import NaiveFixed
from filings_rag.retrieve.base import RetrievalError
from filings_rag.retrieve.vector import VectorRetriever

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL"),
]
GOLD = "ZZCORP_2022_10K"
# (accession, doc_name, body). The distractor has no benchmark_docs row, like real distractors.
DOCS = [
    ("zzc-gold", GOLD, "w5 gold"),
    ("zzc-twin", "ZZCORP_2021_10K", "w5 twin"),  # same vector as the gold filing
    ("zzc-far", "ZZCORP_2020_10K", "w9 far"),
]
TABLE = "chunks__zz_corp__v1"


class WordEmbedder:
    """One-hot on the number of the text's first word ("w5 ..." -> index 5)."""

    name = "fake-embed"
    dims = 1024

    def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        vecs = []
        for t in texts:
            v = [0.0] * 1024
            v[int(t.split()[0][1:])] = 1.0
            vecs.append(v)
        return Embeddings(vecs, tokens=len(texts))


class ZzCorpPipe(NaiveFixed):
    name = "zz-corp"


@pytest.fixture
def conn(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    for name in ("zz-small", "zz-all"):
        monkeypatch.setitem(corpora.CORPORA, name, {1: corpora.Corpus("test", ())})
    url = os.environ["DATABASE_URL"]
    db.migrate(url)
    with psycopg.connect(url) as c:
        c.execute(
            "INSERT INTO benchmark_docs (doc_name, company, doc_type, doc_period)"
            " VALUES (%s, 'ZZ', '10k', 2022) ON CONFLICT DO NOTHING", (GOLD,),
        )  # fmt: skip
        for acc, name, body in DOCS:
            c.execute(
                "INSERT INTO documents (accession, doc_name, cik, company, form_type, filing_date,"
                " source, raw_dir, parts, page_offsets, body, extractor) VALUES"
                " (%s, %s, 1, 'ZZ', '10-K', '2023-01-01', 'edgar', 'x', %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (acc, name, Jsonb([]), [0], body, EXTRACTOR),
            )
        c.commit()
        yield c
        c.rollback()
        c.execute("DELETE FROM corpora WHERE name LIKE 'zz-%'")
        c.execute(f"DROP TABLE IF EXISTS {TABLE}")
        c.execute("DELETE FROM pipelines WHERE name = 'zz-corp'")
        c.execute("DELETE FROM documents WHERE accession LIKE 'zzc-%'")
        c.execute("DELETE FROM benchmark_docs WHERE doc_name = %s", (GOLD,))
        c.commit()


def _build(conn: psycopg.Connection, docs: list[str] | None = None) -> None:  # type: ignore[type-arg]
    p = ZzCorpPipe(version=1, window_words=350, overlap_words=50)
    names = docs or [name for _, name, _ in DOCS]  # never the real filings in a dev database
    build(conn, p, WordEmbedder(), docs=names, log=lambda s: None)


def test_define_is_immutable_and_needs_fetched_filings(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValueError, match="not fetched"):
        corpora.define(conn, "zz-small@1", {"zzc-gold", "zzc-missing"})
    assert corpora.define(conn, "zz-small@1", {"zzc-gold", "zzc-far"}) == 2
    assert corpora.define(conn, "zz-small@1", {"zzc-gold", "zzc-far"}) == 2  # same set: no-op
    with pytest.raises(ValueError, match="bump the version"):
        corpora.define(conn, "zz-small@1", {"zzc-gold"})


def test_document_ids_refuse_undefined_or_unbuilt_corpora(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    _build(conn, docs=[GOLD])  # the other two filings aren't built yet
    with pytest.raises(ValueError, match="not defined"):
        corpora.document_ids(conn, "zz-all@1", TABLE)
    corpora.define(conn, "zz-all@1", {a for a, _, _ in DOCS})
    with pytest.raises(ValueError, match="2 of 3 zz-all@1 filings have no chunks"):
        corpora.document_ids(conn, "zz-all@1", TABLE)
    _build(conn)  # appends the rest; the gold filing's chunks stay as they were
    assert len(corpora.document_ids(conn, "zz-all@1", TABLE)) == 3


@pytest.mark.parametrize("search", ["exact", "hnsw"])
def test_vector_retriever_searches_only_the_corpus(conn: psycopg.Connection, search: str) -> None:  # type: ignore[type-arg]
    _build(conn)
    corpora.define(conn, "zz-small@1", {"zzc-gold", "zzc-far"})
    ids = corpora.document_ids(conn, "zz-small@1", TABLE)
    unfiltered = VectorRetriever(conn, TABLE, WordEmbedder(), k=3, search=search)
    every = [h.doc_name for h in unfiltered.retrieve("w5 query").hits]
    assert set(every[:2]) == {GOLD, "ZZCORP_2021_10K"}  # the twin ties with the gold filing
    scoped = VectorRetriever(conn, TABLE, WordEmbedder(), k=2, search=search, documents=ids)
    assert [h.doc_name for h in scoped.retrieve("w5 query").hits] == [GOLD, "ZZCORP_2020_10K"]


def test_hnsw_short_of_k_within_the_filter_is_an_error(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    _build(conn)
    corpora.define(conn, "zz-small@1", {"zzc-gold"})  # one chunk: k=3 can't be met
    ids = corpora.document_ids(conn, "zz-small@1", TABLE)
    r = VectorRetriever(conn, TABLE, WordEmbedder(), k=3, search="hnsw", documents=ids)
    with pytest.raises(RetrievalError, match="1 of 3"):
        r.retrieve("w5 query")
    exact = VectorRetriever(conn, TABLE, WordEmbedder(), k=3, documents=ids)
    assert len(exact.retrieve("w5 query").hits) == 1  # exact search returns what exists
