"""Fiscal-period correctness: does an answer state the period the question is about?

Deterministic, no model calls. The period an answer should state is the latest of the
question's hand-labelled periods (`routes@1`, FinanceBench labels): the period the answer is
"as of". A growth question for FY2022 also needs FY2021, but the answer is about FY2022.

That period can be written several ways, and all of them count:
- the FinanceBench label year ("FY2023", "fiscal 2023", "2023");
- the company's own name for the year, when FinanceBench labels it differently (Ulta's FY2023
  is the year Ulta calls fiscal 2022: `label_offset` in corpus/companies.csv);
- a date inside the period or within a few days of its end ("January 28, 2023",
  "2023-01-28", an 8-K's "May 3, 2023"), from the company's 10-K or 10-Q with that label; a
  bare year also matches the period end's year.
A quarter ("Q2 FY2023") is stated by a mention of that quarter, a date inside it, or its fiscal
year: the check is for wrong years. A mention of another quarter of that year doesn't count.

Verdicts:
- right: the answer states that period.
- wrong: the answer states periods, but not that one.
- none: the answer states no period at all.

Refused answers are not scored (the report leaves out answers the judge marked refused).

Known limits: an answer that names the right year next to a figure from another year still
counts as right, and for Ulta-style companies a bare year is ambiguous between two labels.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

from filings_rag.edgar.resolve import parse_doc_name
from filings_rag.eval.routes import RouteLabel
from filings_rag.facts.lookup import (
    END_SLACK,
    Period,
    parse_period,
    period_end_dates,
    period_label,
    read_offsets,
    target_end,
)

VERDICTS = ("right", "wrong", "none")
YEAR_DAYS = 364
QUARTER_DAYS = 91
_I = re.IGNORECASE

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        1,
    )
    for m in names
}  # fmt: skip
_Y = r"((?:19|20)\d{2})"
_FISCAL = r"(?:FY|fiscal(?:\s+year)?)\s*'?"
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_ORDINALS |= {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4}

DATE = re.compile(r"\b([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),?\s+" + _Y + r"(?!\d)")
ISO_DATE = re.compile(_Y + r"-(\d{2})-(\d{2})(?!\d)")
RANGE = re.compile(
    _FISCAL + r"(\d{4}|\d{2})\s*(?:-|–|—|to|through)\s*" + _FISCAL + r"?(\d{4}|\d{2})(?!\d)", _I
)
QUARTER = re.compile(r"\bQ([1-4])\s*(?:of\s+)?(?:" + _FISCAL + r")?(\d{4}|'?\d{2})(?!\d)", _I)
YEAR_QUARTER = re.compile(r"(?:\bFY\s*)?" + _Y + r"\s*Q([1-4])\b", _I)
ORDINAL = re.compile(
    r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+(?:fiscal\s+)?quarter\s+(?:of\s+)?"
    r"(?:" + _FISCAL + r")?" + _Y + r"(?!\d)",
    _I,
)
FISCAL = re.compile(r"\b" + _FISCAL + r"(\d{4}|\d{2})(?!\d)", _I)
BARE = re.compile(r"(?<![\d$.,])" + _Y + r"(?!\d|,\d|\.\d|%)")  # not 2,024 / $2024 / 2024%


@dataclass(frozen=True)
class Mention:
    kind: str  # fiscal | quarter | date | bare
    year: int = 0
    quarter: int | None = None
    when: date | None = None


def _year(s: str) -> int:
    s = s.lstrip("'")
    return int(s) if len(s) == 4 else 2000 + int(s)


def mentions(text: str) -> list[Mention]:
    """Every period an answer states. Each matched span is blanked, so "Q2 FY2023" is one
    quarter mention and not also a fiscal-year mention."""
    out: list[Mention] = []
    rest = text

    def take(pat: re.Pattern[str], make: Any) -> None:
        nonlocal rest
        for m in pat.finditer(rest):
            got = make(m)
            if got:
                out.extend(got)
                rest = rest[: m.start()] + " " * (m.end() - m.start()) + rest[m.end() :]

    def a_date(m: re.Match[str]) -> list[Mention]:
        month = _MONTHS.get(m[1].lower())
        if month is None:
            return []
        try:
            return [Mention("date", int(m[3]), when=date(int(m[3]), month, int(m[2])))]
        except ValueError:
            return []

    def iso(m: re.Match[str]) -> list[Mention]:
        try:
            return [Mention("date", int(m[1]), when=date(int(m[1]), int(m[2]), int(m[3])))]
        except ValueError:
            return []

    def a_range(m: re.Match[str]) -> list[Mention]:
        a, b = sorted((_year(m[1]), _year(m[2])))
        return [Mention("fiscal", y) for y in range(a, b + 1)] if b - a <= 10 else []

    take(DATE, a_date)
    take(ISO_DATE, iso)
    take(RANGE, a_range)
    take(QUARTER, lambda m: [Mention("quarter", _year(m[2]), int(m[1]))])
    take(YEAR_QUARTER, lambda m: [Mention("quarter", int(m[1]), int(m[2]))])
    take(ORDINAL, lambda m: [Mention("quarter", int(m[2]), _ORDINALS[m[1].lower()])])
    take(FISCAL, lambda m: [Mention("fiscal", _year(m[1]))])
    take(BARE, lambda m: [Mention("bare", int(m[1]))])
    return out


@dataclass(frozen=True)
class Expected:
    """The period an answer should state, and the ways it may be written."""

    period: Period
    years: frozenset[int]  # label year and the company's own name for it
    end: date | None  # period end, when the company's filings give one

    @property
    def label(self) -> str:
        return period_label(self.period)


def expected_for(
    period: Period, offset: int, ends: dict[Period, date], anchor: tuple[Period, date] | None
) -> Expected:
    return Expected(
        period, frozenset({period[0], period[0] - offset}), target_end(period, ends, anchor)
    )


def _inside(d: date | None, e: Expected) -> bool:
    """A date on or a few days around the period end, or inside the period (an 8-K event
    dated May 2023 states FY2023)."""
    if d is None:
        return False
    if e.end is None:
        return d.year in e.years
    length = QUARTER_DAYS if e.period[1] else YEAR_DAYS
    # The first few days are left out: a date there is usually the prior period's end.
    return -(length - END_SLACK) < (d - e.end).days <= END_SLACK


def states(m: Mention, e: Expected) -> bool:
    """Whether one mention names the expected period. A quarter is also stated by its fiscal
    year: the check is for wrong years, and a wrong quarter (Q1 for Q2) still fails."""
    quarter = e.period[1]
    ends_in = e.end.year if e.end is not None else None
    if m.kind == "date":
        return _inside(m.when, e)
    if m.kind == "quarter":
        return quarter == m.quarter and (m.year in e.years or m.year == ends_in)
    if m.kind == "fiscal":
        return m.year in e.years
    return m.year in e.years or m.year == ends_in  # bare year


def classify(answer: str, e: Expected) -> str:
    found = mentions(answer)
    if not found:
        return "none"
    return "right" if any(states(m, e) for m in found) else "wrong"


def expectations(
    conn: psycopg.Connection[Any],
    question_ids: Iterable[str],
    routes: dict[str, RouteLabel],
    companies_csv: Path,
) -> dict[str, Expected]:
    """question id -> Expected, for questions with a route label. Period ends come from the
    company of the question's gold filing, labelled as `FactLookup` labels them."""
    qids = sorted(q for q in set(question_ids) if q in routes)
    if not qids:
        return {}
    offsets = read_offsets(companies_csv)
    gold: dict[str, tuple[str, int, str | None, date | None]] = {}
    for qid, doc, cik, form, end in conn.execute(
        "SELECT q.id, q.doc_name, d.cik, d.form_type, d.period_end FROM questions q"
        " JOIN documents d ON d.doc_name = q.doc_name WHERE q.id = ANY(%s)",
        (qids,),
    ):
        gold[qid] = (doc, int(cik), form, end)
    ciks = sorted({g[1] for g in gold.values()})
    benchmark = {r[0] for r in conn.execute("SELECT doc_name FROM benchmark_docs")}
    docs: dict[int, list[tuple[str, date, bool]]] = {}
    for name, cik, end in conn.execute(
        "SELECT doc_name, cik, period_end FROM documents WHERE cik = ANY(%s)"
        " AND form_type IN ('10-K', '10-Q') AND period_end IS NOT NULL ORDER BY doc_name",
        (ciks,),
    ):
        docs.setdefault(int(cik), []).append((name, end, name in benchmark))
    conn.commit()
    ends = {cik: period_end_dates(d, offsets.get(cik, 0)) for cik, d in docs.items()}
    out: dict[str, Expected] = {}
    for qid in qids:
        target = max(
            (parse_period(p) for p in routes[qid].periods), key=lambda p: (p[0], p[1] or 0)
        )
        if qid not in gold:  # gold filing not in the documents table: years only
            out[qid] = Expected(target, frozenset({target[0]}), None)
            continue
        doc, cik, form, end = gold[qid]
        anchor = None
        if form in ("10-K", "10-Q") and end is not None:
            ref = parse_doc_name(doc)
            q = None if ref.fiscal_period == "FY" else int(ref.fiscal_period[1])
            anchor = ((ref.fiscal_year, q), end)
        out[qid] = expected_for(target, offsets.get(cik, 0), ends.get(cik, {}), anchor)
    return out
