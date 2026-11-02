"""Corpus sets: named, versioned lists of filings that a run searches.

A corpus is defined once from committed manifests (`frl corpus define`) and never changes;
different filings mean a new version. Retrievers filter a pipeline's chunks to the corpus.
"""

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql

DEFAULT = "fb-gold@1"  # what every P0 run searched: the 84 question filings


@dataclass(frozen=True)
class Corpus:
    description: str
    manifests: tuple[str, ...]  # files under corpus/


# name -> version -> definition. Add versions; never edit a defined one.
CORPORA: dict[str, dict[int, Corpus]] = {
    "fb-gold": {1: Corpus("FinanceBench question filings only", ("manifest.csv",))},
    "fb-distractors": {
        1: Corpus(
            "question filings plus the same companies' other 10-Ks (FY2015-2023) and, for "
            "companies with quarterly questions, other 10-Qs (FY2021-2024)",
            ("manifest.csv", "distractors.csv"),
        )
    },
    "fb-gate": {
        1: Corpus(
            "the retrieval gate's corpus (P3): question filings plus each one's next filing of "
            "the same form (FY+1), which repeats the question year's figures as comparatives",
            ("manifest.csv", "gate_distractors.csv"),
        )
    },
}


def parse_ref(ref: str) -> tuple[str, int, Corpus]:
    """`name@version` -> (name, version, definition)."""
    name, _, ver = ref.partition("@")
    try:
        return name, int(ver), CORPORA[name][int(ver)]
    except (KeyError, ValueError) as e:
        known = [f"{n}@{v}" for n, vs in CORPORA.items() for v in vs]
        raise ValueError(f"unknown corpus {ref!r}; known: {known}") from e


def define(conn: psycopg.Connection[Any], ref: str, accessions: set[str]) -> int:
    """Record the corpus's filings, or check an existing definition matches exactly.

    Every accession must already be fetched (a row in `documents`). Returns the filing count.
    """
    name, version, corpus = parse_ref(ref)
    if not accessions:
        raise ValueError(f"{ref}: no filings")
    rows = conn.execute(
        "SELECT accession FROM corpus_documents WHERE corpus = %s AND version = %s",
        (name, version),
    ).fetchall()
    if rows:
        existing = {r[0] for r in rows}
        if existing != accessions:
            raise ValueError(
                f"{ref} is already defined with {len(existing)} filings "
                f"({len(accessions - existing)} new, {len(existing - accessions)} dropped); "
                "bump the version"
            )
        return len(existing)
    fetched = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT accession FROM documents WHERE accession = ANY(%s)",
            (sorted(accessions),),
        )
    }
    missing = accessions - fetched
    if missing:
        raise ValueError(f"{ref}: {len(missing)} filings not fetched, e.g. {sorted(missing)[:3]}")
    with conn.transaction():
        conn.execute(
            "INSERT INTO corpora (name, version, description) VALUES (%s, %s, %s)",
            (name, version, corpus.description),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO corpus_documents (corpus, version, accession) VALUES (%s, %s, %s)",
                [(name, version, a) for a in sorted(accessions)],
            )
    return len(accessions)


def document_ids(conn: psycopg.Connection[Any], ref: str, chunk_table: str) -> list[int]:
    """The `documents` ids a run on this corpus searches, for the pipeline behind `chunk_table`.

    Fails if the corpus isn't defined, or if any of its filings has no chunks in that table
    (not fetched with the pipeline's extractor, or not built yet): a run would otherwise
    search a smaller corpus than its config says.
    """
    name, version, _ = parse_ref(ref)
    q = sql.SQL(
        """SELECT cd.accession, d.id,
                  EXISTS (SELECT 1 FROM {t} c WHERE c.document_id = d.id)
           FROM corpus_documents cd
           LEFT JOIN documents d ON d.accession = cd.accession AND d.extractor =
               (SELECT extractor FROM pipelines WHERE chunk_table = %(t)s)
           WHERE cd.corpus = %(name)s AND cd.version = %(version)s
           ORDER BY d.id"""
    ).format(t=sql.Identifier(chunk_table))
    rows = conn.execute(q, {"t": chunk_table, "name": name, "version": version}).fetchall()
    if not rows:
        raise ValueError(f"corpus {ref} is not defined; run `frl corpus define {ref}`")
    unbuilt = [accn for accn, doc_id, built in rows if doc_id is None or not built]
    if unbuilt:
        raise ValueError(
            f"{len(unbuilt)} of {len(rows)} {ref} filings have no chunks in {chunk_table}, "
            f"e.g. {unbuilt[:3]}; fetch and build them first"
        )
    return [int(doc_id) for _, doc_id, _ in rows]


def doc_names(conn: psycopg.Connection[Any], ref: str) -> list[str]:
    """The corpus's filings by doc_name (what `ingest.build` takes)."""
    name, version, _ = parse_ref(ref)
    return [
        r[0]
        for r in conn.execute(
            "SELECT d.doc_name FROM corpus_documents c JOIN documents d"
            " ON d.accession = c.accession WHERE c.corpus = %s AND c.version = %s"
            " ORDER BY d.doc_name",
            (name, version),
        )
    ]
