"""Coverage check (P2 PR 3): for each fact or mixed question, are the items its metrics need
in `xbrl_facts`, for every period it needs, as filed in the question's own (gold) filing?

Periods and fact matching are `facts/lookup.py`'s (FinanceBench labels, period-end dates
through the company's filings, the Ulta label offset).

Each (item, period) is "gold" (in the gold filing), "other" (only in another filing of the
company, e.g. when the gold document is an earnings release with no XBRL), or "missing".
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

from filings_rag.eval.routes import RouteLabel
from filings_rag.facts.concepts import ConceptMap
from filings_rag.facts.lookup import FactLookup, parse_period, read_offsets


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


def check(
    conn: psycopg.Connection[Any],
    labels: dict[str, RouteLabel],
    cmap: ConceptMap,
    companies_csv: Path,
) -> list[QuestionCoverage]:
    offsets = read_offsets(companies_csv)
    gold_of = dict(conn.execute("SELECT id, doc_name FROM questions").fetchall())
    out: list[QuestionCoverage] = []
    for qid, lab in sorted(labels.items()):
        if lab.route == "prose":
            continue
        facts = FactLookup(conn, cmap, gold_of[qid], offsets)
        qc = QuestionCoverage(qid, lab.route, lab.metrics, facts.doc_name, facts.has_xbrl)
        for item in sorted({i for m in lab.metrics for i in cmap.inputs(m)}):
            for label in lab.periods:
                f = facts.get(item, parse_period(label))
                if f is None:
                    qc.checks.append(ItemCheck(item, label, "missing"))
                else:
                    status = "gold" if f.as_filed else "other"
                    qc.checks.append(ItemCheck(item, label, status, f.concept, f.value))
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
