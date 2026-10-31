"""Coverage check (P2 PR 3): for each fact or mixed question, are the items its metrics need
in `xbrl_facts`, for every period it needs, as filed in the question's own (gold) filing?

Periods are FinanceBench labels ("FY2022", "Q2 FY2023"). A label maps to a period-end date
through the company's filing with that label (`documents.period_end`; distractors take the
company's `label_offset`, as in scope `fiscal_labels = "financebench"`). When no filing has the
label, the date is the gold filing's period end shifted by whole years. A fact matches a period
when its end date is within END_SLACK days and, for flows, its length is a year (annual) or a
quarter (Q1-Q4); balances are instants.

Each (item, period) is "gold" (in the gold filing), "other" (only in another filing of the
company, e.g. when the gold document is an earnings release with no XBRL), or "missing".
"""

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

from filings_rag.edgar.resolve import parse_doc_name
from filings_rag.eval.routes import RouteLabel
from filings_rag.facts.concepts import ConceptMap, Item

END_SLACK = 10  # days: 52/53-week fiscal years end on different dates
ANNUAL_DAYS = (340, 380)
QUARTER_DAYS = (80, 100)
PERIOD = re.compile(r"(?:Q([1-4]) )?FY(\d{4})")


@dataclass
class ItemCheck:
    item: str
    period: str
    status: str  # gold | other | missing
    concept: str | None = None
    value: float | None = None


@dataclass
class QuestionCoverage:
    question_id: str
    route: str
    metrics: tuple[str, ...]
    gold_doc: str
    gold_has_xbrl: bool
    checks: list[ItemCheck] = field(default_factory=list)

    @property
    def status(self) -> str:
        """gold: everything in the gold filing; other: everything somewhere; else missing."""
        if all(c.status == "gold" for c in self.checks):
            return "gold"
        if all(c.status != "missing" for c in self.checks):
            return "other"
        return "missing"


def parse_period(label: str) -> tuple[int, int | None]:
    m = PERIOD.fullmatch(label)
    if m is None:
        raise ValueError(f"bad period label {label!r}")
    return int(m[2]), int(m[1]) if m[1] else None


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + years, day=28)


def period_end_dates(
    docs: list[tuple[str, date, bool]], offset: int
) -> dict[tuple[int, int | None], date]:
    """(year, quarter or None) -> period end, from (doc_name, period_end, is_benchmark_doc)
    for one company's 10-K and 10-Q filings. When two filings get the same label, the
    benchmark document wins, then the earlier period end, so the result doesn't depend on row
    order."""
    out: dict[tuple[int, int | None], date] = {}
    for name, end, benchmark in sorted(docs, key=lambda d: (not d[2], d[1], d[0])):
        ref = parse_doc_name(name)
        year = ref.fiscal_year + (0 if benchmark else offset)
        quarter = None if ref.fiscal_period == "FY" else int(ref.fiscal_period[1])
        out.setdefault((year, quarter), end)
    return out


def target_end(
    period: tuple[int, int | None],
    ends: dict[tuple[int, int | None], date],
    gold: tuple[tuple[int, int | None], date] | None,
) -> date | None:
    if period in ends:
        return ends[period]
    if gold is None:
        return None
    (gy, gq), gend = gold
    y, q = period
    if gq == q:
        return _shift_years(gend, y - gy)
    return None


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
    facts: dict[str, list[tuple[str, date | None, date, float]]],
    item: Item,
    quarter: int | None,
    want: date,
    accessions: set[str] | None,
) -> tuple[str, float] | None:
    """The first concept (in map order) with a matching fact, restricted to `accessions`."""
    for concept in item.concepts:
        for accn, start, end, value in facts.get(concept, []):
            if accessions is not None and accn not in accessions:
                continue
            if fact_matches(item.kind, quarter, start, end, want):
                return concept, value
    return None


def check(
    conn: psycopg.Connection[Any],
    labels: dict[str, RouteLabel],
    cmap: ConceptMap,
    companies_csv: Path,
) -> list[QuestionCoverage]:
    with companies_csv.open() as fh:
        offsets = {int(r["cik"]): int(r.get("label_offset") or 0) for r in csv.DictReader(fh)}
    benchmark = {r[0] for r in conn.execute("SELECT doc_name FROM benchmark_docs")}
    docs = conn.execute(
        "SELECT doc_name, cik, accession, form_type, period_end FROM documents ORDER BY doc_name"
    ).fetchall()
    by_name = {d[0]: d for d in docs}
    gold_of = dict(conn.execute("SELECT id, doc_name FROM questions").fetchall())
    concepts = sorted({c for it in cmap.items.values() for c in it.concepts})
    out: list[QuestionCoverage] = []
    for qid, lab in sorted(labels.items()):
        if lab.route == "prose":
            continue
        gold_doc = gold_of[qid]
        _, cik, gold_accn, gold_form, gold_end = by_name[gold_doc]
        facts: dict[str, list[tuple[str, date | None, date, float]]] = {}
        for concept, accn, start, end, value in conn.execute(
            "SELECT concept, accession, period_start, period_end, value FROM xbrl_facts"
            " WHERE cik = %s AND taxonomy = 'us-gaap' AND concept = ANY(%s)"
            " ORDER BY filed DESC",  # among other filings, prefer the latest report
            (cik, concepts),
        ):
            facts.setdefault(concept, []).append((accn, start, end, float(value)))
        gold_has_xbrl = (
            conn.execute(
                "SELECT 1 FROM xbrl_facts WHERE accession = %s LIMIT 1", (gold_accn,)
            ).fetchone()
            is not None
        )
        company_docs = [
            (n, e, n in benchmark)
            for n, c, _, f, e in docs
            if c == cik and f in ("10-K", "10-Q") and e is not None
        ]
        ends = period_end_dates(company_docs, offsets.get(cik, 0))
        gold_period = None
        if gold_form in ("10-K", "10-Q"):
            ref = parse_doc_name(gold_doc)
            gq = None if ref.fiscal_period == "FY" else int(ref.fiscal_period[1])
            gold_period = ((ref.fiscal_year, gq), gold_end)
        qc = QuestionCoverage(qid, lab.route, lab.metrics, gold_doc, gold_has_xbrl)
        items = sorted({i for m in lab.metrics for i in cmap.inputs(m)})
        for item_name in items:
            item = cmap.items[item_name]
            for label in lab.periods:
                period = parse_period(label)
                want = target_end(period, ends, gold_period)
                if want is None:
                    qc.checks.append(ItemCheck(item_name, label, "missing"))
                    continue
                hit = find(facts, item, period[1], want, {gold_accn})
                status = "gold"
                if hit is None:
                    hit, status = find(facts, item, period[1], want, None), "other"
                if hit is None:
                    qc.checks.append(ItemCheck(item_name, label, "missing"))
                else:
                    qc.checks.append(ItemCheck(item_name, label, status, hit[0], hit[1]))
        out.append(qc)
    return out


def render(results: list[QuestionCoverage], version: str) -> str:
    n = len(results)
    by = {s: sum(r.status == s for r in results) for s in ("gold", "other", "missing")}
    lines = [
        f"# XBRL coverage ({version}, `companyfacts`)",
        "",
        "**FinanceBench open subset. These are not full-set FinanceBench results.**",
        "",
        f"Fact and mixed questions (`routes@1`): {n}.",
        "",
        "| coverage | questions |",
        "|---|---|",
        f"| every input in the gold filing (as filed) | {by['gold']}"
        f" ({100 * by['gold'] / n:.0f}%) |",
        f"| every input somewhere, not all in the gold filing | {by['other']} |",
        f"| some input missing | {by['missing']} |",
        "",
        "| question | route | gold filing | metrics | status | missing / not in gold |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        gaps = [f"{c.item} {c.period}" + ("" if c.status == "missing" else " (other)")
                for c in r.checks if c.status != "gold"]  # fmt: skip
        doc = r.gold_doc + ("" if r.gold_has_xbrl else " (no XBRL)")
        lines.append(
            f"| {r.question_id[16:]} | {r.route} | {doc} | {', '.join(r.metrics)} | {r.status}"
            f" | {'; '.join(gaps) or '–'} |"
        )
    return "\n".join(lines) + "\n"


# --- value check -----------------------------------------------------------------------------


def printed_forms(value: float) -> set[str]:
    """How a statement might print a value: in millions or thousands, with or without commas,
    rounded or to one decimal."""
    out: set[str] = set()
    for div in (1e6, 1e3):
        x = abs(value) / div
        if x >= 1:
            out |= {f"{round(x):,}", str(round(x)), f"{x:,.1f}"}
    return out


@dataclass(frozen=True)
class Verification:
    found: int
    total: int
    unquoted: list[tuple[str, ItemCheck]]  # (question id, check) whose value isn't in the text


def verify(conn: psycopg.Connection[Any], results: list[QuestionCoverage]) -> Verification:
    """Is each matched value printed in the question's FinanceBench evidence or justification?
    A value that isn't can still be right (an evidence page that quotes components, or a
    qualitative question whose evidence quotes no figures), so the unquoted ones are listed
    for a hand check, not counted as wrong."""
    text = {
        qid: f"{just or ''} {json.dumps(ev)}".replace("\\n", " ")
        for qid, just, ev in conn.execute("SELECT id, justification, evidence FROM questions")
    }
    found, total, unquoted = 0, 0, []
    for r in results:
        for c in r.checks:
            if c.value is None:
                continue
            total += 1
            if any(f in text[r.question_id] for f in printed_forms(c.value)):
                found += 1
            else:
                unquoted.append((r.question_id, c))
    return Verification(found, total, unquoted)


def render_verify(v: Verification) -> str:
    lines = [
        "",
        "## Values in the evidence text (`--verify`)",
        "",
        f"{v.found} of {v.total} matched values appear in the question's FinanceBench evidence or"
        " justification, as statements print them ($M or $K). Unquoted values need a hand check:",
        "",
        "| question | item | period | concept | value |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {q[16:]} | {c.item} | {c.period} | {c.concept} | {c.value:,.0f} |"
        for q, c in v.unquoted
    ]
    return "\n".join(lines) + "\n"
