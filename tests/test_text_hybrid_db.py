import math
import os

import psycopg
import pytest
from psycopg.types.json import Jsonb

from filings_rag import db
from filings_rag.embed.base import Embeddings, InputType
from filings_rag.extract.html import EXTRACTOR
from filings_rag.ingest.build import build
from filings_rag.ingest.naive_fixed import NaiveFixed
from filings_rag.retrieve.bm25 import BM25, ensure_postings
from filings_rag.retrieve.factory import make_retriever

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL"),
]
TABLE = "chunks__zz_lex__v1"
# One chunk each. The fake embedder puts every chunk on the same vector except "near", so
# vector search ranks "near" first whatever the question; only lexical search can tell the
# others apart.
DOCS = [
    ("zzl-near", "ZZLEX_2019_10K", "w1 the company sold goods and services during the year"),
    ("zzl-item", "ZZLEX_2020_10K", "w2 Item 7 Management's Discussion and Analysis of results"),
    ("zzl-tick", "ZZLEX_2021_10K", "w2 shares of MMM common stock trade on the exchange"),
    ("zzl-num", "ZZLEX_2022_10K", "w2 capital expenditures were 4,567 million in FY2022"),
    ("zzl-fill", "ZZLEX_2023_10K", "w2 weather was mild and nothing else happened at all"),
]


class OneHotEmbedder:
    name = "fake-embed"
    dims = 1024

    def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        vecs = []
        for t in texts:
            v = [0.0] * 1024
            v[1 if t.startswith("w1") or input_type == "query" else 2] = 1.0
            vecs.append(v)
        return Embeddings(vecs, tokens=len(texts))


class ZzLexPipe(NaiveFixed):
    name = "zz-lex"


@pytest.fixture
def ids():  # type: ignore[no-untyped-def]
    url = os.environ["DATABASE_URL"]
    db.migrate(url)
    with psycopg.connect(url) as c:
        for acc, name, body in DOCS:
            c.execute(
                "INSERT INTO documents (accession, doc_name, cik, company, form_type, filing_date,"
                " source, raw_dir, parts, page_offsets, body, extractor) VALUES"
                " (%s, %s, 1, 'ZZ', '10-K', '2023-01-01', 'edgar', 'x', %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (acc, name, Jsonb([]), [0], body, EXTRACTOR),
            )
        c.commit()
        build(
            c,
            ZzLexPipe(1, 350, 50),
            OneHotEmbedder(),
            docs=[n for _, n, _ in DOCS],
            log=lambda s: None,
        )
        doc_ids = [r[0] for r in c.execute("SELECT id FROM documents WHERE accession LIKE 'zzl-%'")]
        yield c, doc_ids
        c.rollback()
        c.execute(f"DROP TABLE IF EXISTS {TABLE}, {TABLE}_postings, {TABLE}_lengths")
        c.execute("DELETE FROM pipelines WHERE name = 'zz-lex'")
        c.execute("DELETE FROM documents WHERE accession LIKE 'zzl-%'")
        c.commit()


def _top(conn, spec, docs, query):  # type: ignore[no-untyped-def]
    r = make_retriever(conn, spec, TABLE, OneHotEmbedder(), docs)
    return [h.doc_name for h in r.retrieve(query).hits]


@pytest.mark.parametrize("ranking", ["bm25", "ts_rank_cd"])
@pytest.mark.parametrize(
    ("query", "doc"),
    [
        ("What does Item 7 say?", "ZZLEX_2020_10K"),
        ("Where does MMM stock trade?", "ZZLEX_2021_10K"),
        ("Which year had capex of 4,567 million?", "ZZLEX_2022_10K"),
        ("FY2022 capital expenditures", "ZZLEX_2022_10K"),
    ],
)
def test_exact_lexical_matches_surface(ids, query: str, doc: str, ranking: str) -> None:  # type: ignore[no-untyped-def]
    conn, docs = ids
    assert _top(conn, {"kind": "text", "k": 1, "ranking": ranking}, docs, query) == [doc]
    assert doc not in _top(conn, {"kind": "vector", "k": 1}, docs, query)
    spec = {"kind": "hybrid", "k": 2, "candidates": 5, "ranking": ranking}
    hybrid = _top(conn, spec, docs, query)
    assert doc in hybrid  # fusion keeps the lexical hit next to the vector one


def test_text_search_respects_the_document_filter_and_stopword_only_queries(ids) -> None:  # type: ignore[no-untyped-def]
    conn, docs = ids
    row = conn.execute("SELECT id FROM documents WHERE accession = 'zzl-item'").fetchone()
    item = [row[0]]
    for ranking in ("bm25", "ts_rank_cd"):
        spec = {"kind": "text", "ranking": ranking}
        assert _top(conn, spec, item, "Item 7 MMM 4,567") == ["ZZLEX_2020_10K"]
        assert _top(conn, spec, docs, "what is the") == []  # all stopwords: no hits


def test_bm25_postings_are_incremental_and_scored_by_the_formula(ids) -> None:  # type: ignore[no-untyped-def]
    conn, docs = ids
    assert ensure_postings(conn, TABLE) == 0  # the build already indexed every chunk
    bm = BM25(conn, TABLE, docs)
    assert bm.n == 5
    hits = bm.search("MMM", 5)
    assert [h.doc_name for h in hits] == ["ZZLEX_2021_10K"]
    length = conn.execute(
        f"SELECT len FROM {TABLE}_lengths l JOIN {TABLE} c ON c.id = l.chunk_id"
        " WHERE c.document_id = (SELECT id FROM documents WHERE accession = 'zzl-tick')"
    ).fetchone()[0]
    idf = math.log(1 + (5 - 1 + 0.5) / (1 + 0.5))
    want = idf * 1 * 2.2 / (1 + 1.2 * (1 - 0.75 + 0.75 * length / bm.avgdl))
    assert hits[0].score == pytest.approx(want)
