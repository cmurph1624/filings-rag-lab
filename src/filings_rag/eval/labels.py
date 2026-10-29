"""Map each FinanceBench evidence passage to the chunks that contain it, per pipeline.

FinanceBench's evidence text comes from PDF extraction and often has words run together
("Transaction-basedrevenue"), so matching compares lowercase alphanumerics with all spaces and
punctuation removed, as overlapping 40-character shingles.

A chunk is labelled when it holds a meaningful share of the passage (COVERAGE_MIN), or when the
chunk itself lies mostly inside the passage (INSIDE_MIN; long passages span several chunks).
"""

import re
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql

from filings_rag.extract.html import EXTRACTOR
from filings_rag.ingest.base import IngestPipeline, chunk_table

SHINGLE = 40
STEP = 20
COVERAGE_MIN = 0.25
INSIDE_MIN = 0.5
BROAD_LABELS = 2  # a short passage matching more chunks than this is flagged as too generic
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def norm(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower())


def shingles(s: str) -> list[str]:
    if len(s) <= SHINGLE:
        return [s] if s else []
    return [s[i : i + SHINGLE] for i in range(0, len(s) - SHINGLE + 1, STEP)]


def match(evidence: str, chunk: str) -> tuple[float, float]:
    """(coverage of the evidence by this chunk, share of the chunk inside the evidence)."""
    ev = shingles(norm(evidence))
    if not ev:
        return 0.0, 0.0
    hits = sum(s in chunk for s in ev)
    coverage = hits / len(ev)
    inside = min(1.0, hits * STEP / max(len(chunk), 1))
    return coverage, inside


def is_broad(evidence: str, n_labels: int) -> bool:
    """Short passages are one or two shingles; matching many chunks means they're too generic."""
    return len(norm(evidence)) <= 2 * SHINGLE and n_labels > BROAD_LABELS


@dataclass
class LabelReport:
    items: int = 0
    labelled: int = 0
    labels: int = 0
    page_checked: int = 0
    page_agree: int = 0
    missing: list[str] = field(default_factory=list)
    broad: list[str] = field(default_factory=list)  # short passages that matched many chunks


def label(conn: psycopg.Connection[Any], p: IngestPipeline) -> LabelReport:
    table = sql.Identifier(chunk_table(p.name, p.version))
    report = LabelReport()
    cache: dict[str, list[tuple[int, int, str]]] = {}

    def chunks_for(doc_name: str) -> list[tuple[int, int, str]]:
        if doc_name not in cache:
            rows = conn.execute(
                sql.SQL(
                    "SELECT c.id, c.page, c.text FROM {t} c"
                    " JOIN documents d ON d.id = c.document_id"
                    " WHERE d.doc_name = %s AND d.extractor = %s ORDER BY c.ord"
                ).format(t=table),
                (doc_name, EXTRACTOR),
            ).fetchall()
            cache[doc_name] = [(cid, page, norm(text)) for cid, page, text in rows]
        return cache[doc_name]

    rows: list[tuple[str, int, int, float]] = []
    questions = conn.execute("SELECT id, evidence FROM questions ORDER BY id").fetchall()
    for qid, evidence in questions:
        for idx, ev in enumerate(evidence):
            report.items += 1
            hits = []
            for cid, page, text in chunks_for(ev["doc_name"]):
                coverage, inside = match(ev["evidence_text"], text)
                if coverage >= COVERAGE_MIN or inside >= INSIDE_MIN:
                    hits.append((cid, page, coverage))
            if not hits:
                report.missing.append(f"{qid}#{idx} {ev['doc_name']}")
                continue
            report.labelled += 1
            report.labels += len(hits)
            if is_broad(ev["evidence_text"], len(hits)):
                report.broad.append(f"{qid}#{idx} {ev['doc_name']} ({len(hits)} chunks)")
            fb_page = ev.get("evidence_page_num")
            if isinstance(fb_page, int):
                # FinanceBench pages are 0-based, ours 1-based; a chunk may start a page early.
                report.page_checked += 1
                report.page_agree += any(pg in (fb_page, fb_page + 1) for _, pg, _ in hits)
            rows += [(qid, idx, cid, cov) for cid, _, cov in hits]

    conn.commit()  # end the reads; the delete + insert below is its own transaction
    with conn.transaction():
        conn.execute(
            "DELETE FROM evidence_labels WHERE pipeline = %s AND version = %s",
            (p.name, p.version),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO evidence_labels"
                " (pipeline, version, question_id, evidence_idx, chunk_id, coverage)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [(p.name, p.version, q, i, c, cov) for q, i, c, cov in rows],
            )
    return report
