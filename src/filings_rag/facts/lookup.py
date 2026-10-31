"""Find a statement item's XBRL fact for a FinanceBench period, as filed in a given filing.

Periods are FinanceBench labels, (fiscal year, quarter or None): "FY2022" is (2022, None) and
"Q2 FY2023" is (2023, 2). A label maps to a period-end date through the company's 10-K or 10-Q
with that label (`documents.period_end`; distractor filings take the company's `label_offset`,
as in scope `fiscal_labels = "financebench"`). When no filing has the label, the date is the
anchor filing's period end shifted by whole years. A fact matches a period when its end date is
within END_SLACK days and, for flows, its length is a year (annual) or a quarter (Q1-Q4);
balances are instants.

`FactLookup.get` prefers the anchor filing's own value (as filed), then the latest-filed value
from any of the company's filings, and says which it used.
"""

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

from filings_rag.edgar.resolve import parse_doc_name
from filings_rag.facts.concepts import ConceptMap, Item

END_SLACK = 10  # days: 52/53-week fiscal years end on different dates
ANNUAL_DAYS = (340, 380)
QUARTER_DAYS = (80, 100)
PERIOD = re.compile(r"(?:Q([1-4]) )?FY(\d{4})")

Period = tuple[int, int | None]  # (fiscal year, quarter or None)
Rows = dict[str, list[tuple[str, date | None, date, float]]]  # concept -> (accn, start, end, value)


def parse_period(label: str) -> Period:
    m = PERIOD.fullmatch(label)
    if m is None:
        raise ValueError(f"bad period label {label!r}")
    return int(m[2]), int(m[1]) if m[1] else None


def period_label(p: Period) -> str:
    return f"Q{p[1]} FY{p[0]}" if p[1] else f"FY{p[0]}"


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + years, day=28)


def period_end_dates(docs: list[tuple[str, date, bool]], offset: int) -> dict[Period, date]:
    """(year, quarter or None) -> period end, from (doc_name, period_end, is_benchmark_doc)
    for one company's 10-K and 10-Q filings. When two filings get the same label, the
    benchmark document wins, then the earlier period end, so the result doesn't depend on row
    order."""
    out: dict[Period, date] = {}
    for name, end, benchmark in sorted(docs, key=lambda d: (not d[2], d[1], d[0])):
        ref = parse_doc_name(name)
        year = ref.fiscal_year + (0 if benchmark else offset)
        quarter = None if ref.fiscal_period == "FY" else int(ref.fiscal_period[1])
        out.setdefault((year, quarter), end)
    return out


def target_end(
    period: Period, ends: dict[Period, date], anchor: tuple[Period, date] | None
) -> date | None:
    if period in ends:
        return ends[period]
    if anchor is None:
        return None
    (ay, aq), aend = anchor
    y, q = period
    return _shift_years(aend, y - ay) if aq == q else None


def fact_matches(kind: str, quarter: int | None, start: date | None, end: date, want: date) -> bool:
    if abs((end - want).days) > END_SLACK:
        return False
    if kind == "instant":
        return start is None
    if start is None:
        return False
    lo, hi = QUARTER_DAYS if quarter else ANNUAL_DAYS
    return lo <= (end - start).days <= hi


def find(
    facts: Rows, item: Item, quarter: int | None, want: date, accessions: set[str] | None
) -> tuple[str, str, float] | None:
    """(concept, accession, value) for the first concept (in map order) with a matching fact,
    restricted to `accessions` when given. Within a concept, rows are in the caller's order."""
    for concept in item.concepts:
        for accn, start, end, value in facts.get(concept, []):
            if accessions is not None and accn not in accessions:
                continue
            if fact_matches(item.kind, quarter, start, end, want):
                return concept, accn, value
    return None


@dataclass(frozen=True)
class Fact:
    item: str
    period: Period
    value: float
    concept: str
    accession: str
    period_end: date
    as_filed: bool  # from the anchor filing itself, not a later or other report


def read_offsets(companies_csv: Path) -> dict[int, int]:
    with companies_csv.open() as fh:
        return {int(r["cik"]): int(r.get("label_offset") or 0) for r in csv.DictReader(fh)}


class FactLookup:
    """Facts for one company, anchored on one filing (normally the question's gold filing)."""

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        cmap: ConceptMap,
        doc_name: str,
        offsets: dict[int, int],
    ) -> None:
        row = conn.execute(
            "SELECT cik, accession, form_type, period_end FROM documents WHERE doc_name = %s",
            (doc_name,),
        ).fetchone()
        if row is None:
            raise ValueError(f"no document {doc_name!r}")
        self.cik, self.accession, form, end = row
        self.doc_name = doc_name
        self._cmap = cmap
        concepts = sorted({c for it in cmap.items.values() for c in it.concepts})
        self._facts: Rows = {}
        for concept, accn, start, fend, value in conn.execute(
            "SELECT concept, accession, period_start, period_end, value FROM xbrl_facts"
            " WHERE cik = %s AND taxonomy = 'us-gaap' AND concept = ANY(%s)"
            " ORDER BY filed DESC, accession",  # among other filings, prefer the latest report
            (self.cik, concepts),
        ):
            self._facts.setdefault(concept, []).append((accn, start, fend, float(value)))
        self.has_xbrl = (
            conn.execute(
                "SELECT 1 FROM xbrl_facts WHERE accession = %s LIMIT 1", (self.accession,)
            ).fetchone()
            is not None
        )
        benchmark = {r[0] for r in conn.execute("SELECT doc_name FROM benchmark_docs")}
        docs = [
            (n, e, n in benchmark)
            for n, e in conn.execute(
                "SELECT doc_name, period_end FROM documents WHERE cik = %s"
                " AND form_type IN ('10-K', '10-Q') AND period_end IS NOT NULL ORDER BY doc_name",
                (self.cik,),
            )
        ]
        conn.commit()
        self._ends = period_end_dates(docs, offsets.get(self.cik, 0))
        self._anchor: tuple[Period, date] | None = None
        if form in ("10-K", "10-Q"):
            ref = parse_doc_name(doc_name)
            q = None if ref.fiscal_period == "FY" else int(ref.fiscal_period[1])
            self._anchor = ((ref.fiscal_year, q), end)

    def period_end(self, period: Period) -> date | None:
        return target_end(period, self._ends, self._anchor)

    def get(self, item_name: str, period: Period) -> Fact | None:
        item = self._cmap.items[item_name]
        want = self.period_end(period)
        if want is None:
            return None
        for accessions in ({self.accession}, None):
            hit = find(self._facts, item, period[1], want, accessions)
            if hit is not None:
                concept, accn, value = hit
                return Fact(item_name, period, value, concept, accn, want, accn == self.accession)
        return None
