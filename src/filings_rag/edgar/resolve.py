"""Resolve FinanceBench doc_names (e.g. `AMCOR_2023Q2_10Q`) to EDGAR accession numbers.

Priority: `corpus/overrides.csv`, then an accession embedded in the FinanceBench doc_link
(that is the exact filing the PDF came from), then the rules below.

10-K / 10-Q: matched on XBRL fiscal year + period from `companyfacts` (each filing's own
fy/fp, so non-calendar fiscal years resolve correctly). 8-K: matched on the `dated` date
against the submissions list. Earnings releases fall back to the FinanceBench PDF unless
overridden. FinanceBench's own fiscal-year labels are not always the company's (Ulta), which
is why the link accession outranks the fiscal-period match.
"""

import csv
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Protocol

DOC_NAME = re.compile(
    r"^(?P<company>.+?)_(?P<year>\d{4})(?:Q(?P<quarter>[1-4]))?_(?P<kind>10K|10Q|8K|EARNINGS)"
    r"(?:_dated[-_](?P<dated>\d{4}-\d{2}-\d{2}))?$",
    re.IGNORECASE,
)
LINK_ACCESSION = re.compile(r"(\d{10})-?(\d{2})-?(\d{6})")
FORMS = {"10K": "10-K", "10Q": "10-Q", "8K": "8-K"}


@dataclass(frozen=True)
class DocRef:
    kind: str  # 10K | 10Q | 8K | EARNINGS
    fiscal_year: int
    fiscal_period: str  # FY | Q1..Q4
    dated: str | None


def parse_doc_name(name: str) -> DocRef:
    m = DOC_NAME.match(name)
    if not m:
        raise ValueError(f"unrecognized doc_name: {name}")
    kind = m["kind"].upper()
    period = f"Q{m['quarter']}" if m["quarter"] else "FY"
    return DocRef(kind, int(m["year"]), period, m["dated"])


def link_accession(link: str | None) -> str | None:
    m = LINK_ACCESSION.search(link or "")
    return f"{m[1]}-{m[2]}-{m[3]}" if m else None


def periodic_filings(companyfacts: dict[str, Any]) -> dict[str, tuple[str, int, str]]:
    """accession -> (form, fy, fp) for original 10-K/10-Q filings, by majority vote over facts."""
    votes: dict[str, Counter[tuple[str, int, str]]] = defaultdict(Counter)
    for ns in companyfacts.get("facts", {}).values():
        for concept in ns.values():
            for unit in concept.get("units", {}).values():
                for f in unit:
                    if f.get("form") in ("10-K", "10-Q") and f.get("fy") and f.get("fp"):
                        votes[f["accn"]][(f["form"], int(f["fy"]), f["fp"])] += 1
    return {accn: c.most_common(1)[0][0] for accn, c in votes.items()}


@dataclass
class ManifestRow:
    doc_name: str
    company: str
    cik: int | None
    form: str
    fiscal_year: int
    fiscal_period: str
    dated: str
    accession: str
    filing_date: str
    report_date: str
    primary_document: str
    source: str  # edgar | fb_pdf
    status: str  # resolved | ambiguous | fallback | unresolved
    note: str


class FilingSource(Protocol):
    def submissions(self, cik: int) -> list[dict[str, Any]]: ...
    def companyfacts(self, cik: int) -> Any: ...


def resolve(
    doc: dict[str, Any],
    cik: int | None,
    src: FilingSource,
    overrides: dict[str, str],
) -> ManifestRow:
    ref = parse_doc_name(doc["doc_name"])
    row = ManifestRow(
        doc_name=doc["doc_name"],
        company=doc["company"],
        cik=cik,
        form=FORMS.get(ref.kind, "EARNINGS"),
        fiscal_year=ref.fiscal_year,
        fiscal_period=ref.fiscal_period,
        dated=ref.dated or "",
        accession="",
        filing_date="",
        report_date="",
        primary_document="",
        source="fb_pdf",
        status="unresolved",
        note="",
    )
    if cik is None:
        row.note = "no CIK for company"
        return row

    filings = {f["accessionNumber"]: f for f in src.submissions(cik)}
    linked = link_accession(doc.get("doc_link"))
    candidates: list[str]
    if doc["doc_name"] in overrides:
        candidates = [overrides[doc["doc_name"]]]
        row.note = "override"
    elif linked:
        candidates = [linked]
        row.note = "doc_link accession"
    elif ref.kind in ("10K", "10Q"):
        periodic = periodic_filings(src.companyfacts(cik))
        want = (row.form, ref.fiscal_year, ref.fiscal_period)
        candidates = sorted(a for a, key in periodic.items() if key == want)
    elif ref.kind == "8K":
        candidates = sorted(
            a
            for a, f in filings.items()
            if f["form"] == "8-K" and ref.dated in (f.get("reportDate"), f.get("filingDate"))
        )
    else:
        row.status = "fallback"
        row.note = "earnings release; add an override to use the 8-K exhibit"
        return row

    if len(candidates) != 1:
        row.status = "ambiguous" if candidates else "unresolved"
        row.note = "; ".join(filter(None, [row.note, f"candidates: {candidates}"]))
        return row

    accn = candidates[0]
    f = filings.get(accn)
    if f is None:
        row.status = "unresolved"
        row.note = f"{accn} not in submissions"
        return row
    row.accession = accn
    row.filing_date = f.get("filingDate", "")
    row.report_date = f.get("reportDate", "")
    row.primary_document = f.get("primaryDocument", "")
    row.source = "edgar"
    row.status = "resolved"
    return row


def read_companies(path: Path) -> dict[str, int]:
    with path.open() as fh:
        return {r["company"]: int(r["cik"]) for r in csv.DictReader(fh)}


def read_overrides(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open() as fh:
        return {r["doc_name"]: r["accession"] for r in csv.DictReader(fh) if r["accession"]}


def write_manifest(rows: list[ManifestRow], path: Path) -> None:
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[f.name for f in fields(ManifestRow)])
        w.writeheader()
        for r in sorted(rows, key=lambda r: r.doc_name):
            w.writerow(asdict(r))
