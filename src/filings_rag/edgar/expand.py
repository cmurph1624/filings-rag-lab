"""Distractor filings: the benchmark companies' other 10-Ks (and, for companies with quarterly
questions, their other 10-Qs). A corpus of only the gold filings is too easy: most retrieved
chunks come from the right document by default. Distractors make the wrong year or quarter a
real candidate.

Fiscal labels come from period-end dates, not from each filing's XBRL `fy`, which is wrong
often enough to matter (Best Buy's fiscal 2021 10-K says 2020, Costco's 2019 10-K says Q4). XBRL
only decides each company's convention: whether a year ending in January belongs to the
calendar year it ends in (Best Buy) or the one before (Foot Locker, Ulta).

Names follow FinanceBench's `{PREFIX}_{year}[Qn]_{form}` style so excerpts look the same to the
model; a name already taken (Ulta's FinanceBench labels are a year off) gets the period end
appended.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from filings_rag.edgar.resolve import ManifestRow

PREFIX = re.compile(r"^(?P<prefix>.+?)_\d{4}")
QUARTERS = ("Q1", "Q2", "Q3")  # Q4 is the 10-K


@dataclass(frozen=True)
class Window:
    ten_k: range  # fiscal years of 10-Ks, every company
    ten_q: range  # fiscal years of 10-Qs, quarterly companies only


def company_prefixes(gold: list[ManifestRow]) -> dict[str, str]:
    """company -> the doc_name prefix FinanceBench uses for it (e.g. 3M -> 3M, Pfizer -> Pfizer)."""
    out: dict[str, str] = {}
    for r in gold:
        m = PREFIX.match(r.doc_name)
        if m:
            out.setdefault(r.company, m["prefix"])
    return out


def quarterly_companies(gold: list[ManifestRow]) -> set[str]:
    """Companies with at least one 10-Q question document."""
    return {r.company for r in gold if r.form == "10-Q"}


def wanted(window: Window, quarterly: bool) -> list[tuple[str, int, str]]:
    keys = [("10-K", fy, "FY") for fy in window.ten_k]
    if quarterly:
        keys += [("10-Q", fy, q) for fy in window.ten_q for q in QUARTERS]
    return keys


def _year(period_end: str) -> int:
    # A week back, so 52/53-week years ending in the first days of January (J&J's 2023-01-01)
    # count as the year they mostly cover.
    return (date.fromisoformat(period_end) - timedelta(days=7)).year


def fiscal_labels(
    filings: list[dict[str, Any]], periodic: dict[str, tuple[str, int, str]]
) -> dict[str, tuple[str, int, str]]:
    """accession -> (form, fy, fp) for every original 10-K and 10-Q, from period-end dates.

    10-K: fy = the adjusted period-end year minus the company's offset, which is the majority
    of (adjusted year - XBRL fy) over its 10-Ks (0 if it has no XBRL). 10-Q: the fiscal year
    after the latest 10-K before it, and the quarter is its position after that 10-K. A 10-Q
    with no earlier 10-K is left out.
    """
    dated = [f for f in filings if f["form"] in ("10-K", "10-Q") and f.get("reportDate")]
    annual = sorted((f for f in dated if f["form"] == "10-K"), key=lambda f: f["reportDate"])
    votes = Counter(
        _year(f["reportDate"]) - periodic[f["accessionNumber"]][1]
        for f in annual
        if f["accessionNumber"] in periodic
    )
    offset = votes.most_common(1)[0][0] if votes else 0
    out = {f["accessionNumber"]: ("10-K", _year(f["reportDate"]) - offset, "FY") for f in annual}

    quarters = sorted({f["reportDate"] for f in dated if f["form"] == "10-Q"})
    for f in dated:
        if f["form"] != "10-Q":
            continue
        end = f["reportDate"]
        before = [a for a in annual if a["reportDate"] < end]
        if not before:
            continue
        prev = before[-1]["reportDate"]
        n = sum(1 for q in quarters if prev < q <= end)
        fy = out[before[-1]["accessionNumber"]][1] + 1
        out[f["accessionNumber"]] = ("10-Q", fy, f"Q{n}")
    return out


def distractors(
    company: str,
    cik: int,
    prefix: str,
    keys: list[tuple[str, int, str]],
    filings: list[dict[str, Any]],
    periodic: dict[str, tuple[str, int, str]],
    gold_accessions: set[str],
    taken: set[str],
) -> tuple[list[ManifestRow], list[str]]:
    """Manifest rows for each wanted (form, fy, fp) the gold set doesn't already cover.

    Returns (rows, notes). Notes report gaps (no filing for a slot) and slots with several
    original filings, where the earliest is kept. `taken` holds names in use; it is updated.
    """
    by_accn = {f["accessionNumber"]: f for f in filings}
    slots: dict[tuple[str, int, str], list[str]] = defaultdict(list)
    for accn, key in fiscal_labels(filings, periodic).items():
        slots[key].append(accn)

    rows: list[ManifestRow] = []
    notes: list[str] = []
    for key in keys:
        form, fy, fp = key
        label = f"{form} FY{fy}{'' if fp == 'FY' else ' ' + fp}"
        accns = sorted(slots.get(key, []), key=lambda a: (by_accn[a]["filingDate"], a))
        if not accns:
            notes.append(f"{company}: no {label}")
            continue
        if gold_accessions.intersection(accns):
            continue  # a question document already covers this slot
        if len(accns) > 1:
            notes.append(f"{company}: {len(accns)} filings for {label}, kept {accns[0]}")
        f = by_accn[accns[0]]
        name = f"{prefix}_{fy}{'' if fp == 'FY' else fp}_{form.replace('-', '')}"
        note = f"distractor {label}"
        if name in taken:
            name = f"{name}_{f['reportDate']}"
            note += "; name taken by a FinanceBench label, period end appended"
        taken.add(name)
        rows.append(
            ManifestRow(
                doc_name=name,
                company=company,
                cik=cik,
                form=form,
                fiscal_year=fy,
                fiscal_period=fp,
                dated="",
                accession=accns[0],
                filing_date=f.get("filingDate", ""),
                report_date=f.get("reportDate", ""),
                primary_document=f.get("primaryDocument", ""),
                source="edgar",
                status="resolved",
                note=note,
            )
        )
    return rows, notes
