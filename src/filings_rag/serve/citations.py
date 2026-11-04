"""Turn an answer's [n] and [Fn] citations into links to the filings on EDGAR."""

from dataclasses import dataclass
from typing import Any

import psycopg

from filings_rag.harness.prompts import citations
from filings_rag.retrieve.base import Hit

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SNIPPET_CHARS = 400


@dataclass(frozen=True)
class DocInfo:
    doc_name: str
    company: str
    form_type: str
    period_end: str | None
    url: str | None


def edgar_url(cik: int, accession: str, filename: str | None) -> str:
    """The filing's main document on EDGAR, or its index page when the file is unknown."""
    folder = f"{ARCHIVES}/{cik}/{accession.replace('-', '')}"
    return f"{folder}/{filename}" if filename else f"{folder}/{accession}-index.htm"


def load_docs(conn: psycopg.Connection[Any]) -> dict[str, DocInfo]:
    docs: dict[str, DocInfo] = {}
    for name, cik, accession, company, form, end, parts in conn.execute(
        "SELECT doc_name, cik, accession, company, form_type, period_end, parts FROM documents"
    ):
        filename = parts[0].get("filename") if parts else None
        docs[name] = DocInfo(
            name,
            company,
            form,
            end.isoformat() if end else None,
            edgar_url(int(cik), accession, filename) if cik and accession else None,
        )
    return docs


def excerpts(answer: str, hits: list[Hit], docs: dict[str, DocInfo]) -> list[dict[str, Any]]:
    """Every excerpt the model saw, numbered as in the prompt, with `cited` marking those the
    answer cites."""
    cited = set(citations(answer, len(hits)))
    out = []
    for n, h in enumerate(hits, 1):
        d = docs.get(h.doc_name)
        out.append(
            {
                "n": n,
                "cited": n in cited,
                "doc_name": h.doc_name,
                "company": d.company if d else None,
                "form_type": d.form_type if d else None,
                "period_end": d.period_end if d else None,
                "page": h.page,
                "section": h.section,
                "url": d.url if d else None,
                "snippet": h.text[:SNIPPET_CHARS],
            }
        )
    return out


def facts(route: dict[str, Any] | None, docs: dict[str, DocInfo]) -> list[dict[str, Any]]:
    """The Facts section's entries ([F1], [F2], ...) with their anchor filing's link."""
    if not route or not route.get("anchor"):
        return []
    d = docs.get(route["anchor"])
    return [
        {
            "n": f"F{i}",
            "metric": f["metric"],
            "period": f["period"],
            "value": f["display"],
            "formula": f["formula"],
            "unavailable": f["error"],
            "filing": route["anchor"],
            "url": d.url if d else None,
        }
        for i, f in enumerate(route.get("facts", []), 1)
    ]
