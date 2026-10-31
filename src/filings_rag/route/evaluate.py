"""Score a router against the hand route labels (`routes@1`), without any generation."""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import psycopg

from filings_rag.eval.routes import RouteLabel
from filings_rag.facts.lookup import parse_period
from filings_rag.route.plan import RoutePlan


@dataclass(frozen=True)
class RouteScore:
    question_id: str
    label: RouteLabel
    plan: RoutePlan

    @property
    def route_ok(self) -> bool:
        return self.plan.route == self.label.route

    @property
    def metrics_ok(self) -> bool:
        """Same metric set; only scored where both sides use facts."""
        return set(self.plan.metrics) == set(self.label.metrics)

    @property
    def periods_ok(self) -> bool:
        return set(self.plan.periods) == {parse_period(p) for p in self.label.periods}


def summarize(scores: list[RouteScore]) -> str:
    n = len(scores)
    confusion = Counter((s.label.route, s.plan.route) for s in scores)
    factual = [s for s in scores if s.label.route != "prose" and s.plan.route != "prose"]
    lines = [
        f"route accuracy: {sum(s.route_ok for s in scores)}/{n}",
        f"metrics exact (both non-prose): {sum(s.metrics_ok for s in factual)}/{len(factual)}",
        f"periods exact: {sum(s.periods_ok for s in scores)}/{n}",
        "",
        "label -> router: " + ", ".join(f"{a}->{b} {k}" for (a, b), k in sorted(confusion.items())),
        "",
        "disagreements:",
    ]
    for s in scores:
        bad = [
            w
            for w, ok in (
                ("route", s.route_ok),
                ("metrics", s.metrics_ok or s.label.route == "prose" or s.plan.route == "prose"),
                ("periods", s.periods_ok),
            )
            if not ok
        ]
        if bad:
            lines.append(
                f"  {s.question_id[16:]} {'/'.join(bad)}: label {s.label.route}"
                f" {','.join(s.label.metrics) or '-'} {';'.join(s.label.periods)}"
                f" | plan {s.plan.route} {','.join(s.plan.metrics) or '-'}"
                f" {';'.join(s.plan.to_json()['periods'])}"
            )
    return "\n".join(lines)


# --- judge-free fact-route accuracy ----------------------------------------------------------

NUMBER = re.compile(r"-?\$?\s*-?\d[\d,]*(?:\.\d+)?\s*%?")


def gold_number(answer: str) -> tuple[float, int, bool] | None:
    """(value, decimals, is_percent) when the gold answer is a bare number ("$5409.00", "2.8%",
    "-3.7"), else None: a sentence ("Yes, it rose from 20% to 23%") can't be checked this way,
    so those questions are left to the judge."""
    m = NUMBER.fullmatch(answer.strip())
    if m is None:
        return None
    raw = m[0].replace("$", "").replace(",", "").replace(" ", "")
    pct = raw.endswith("%")
    raw = raw.rstrip("%")
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    return float(raw), decimals, pct


def fact_matches_gold(value: float, unit: str, gold: tuple[float, int, bool]) -> bool:
    """Rounded to the gold's precision, the fact equals the gold number in some scale the
    gold could be written in: % for ratios, $, $M or $B for money, or the plain value."""
    g, decimals, pct = gold
    tol = 0.5 * 10**-decimals + 1e-9
    scales = [100.0] if pct else [1.0, 100.0]
    if unit == "usd":
        scales = [1.0, 1e-6, 1e-9]
    return any(abs(value * s - g) <= tol or abs(value * s - g) <= 0.005 * abs(g) for s in scales)


@dataclass(frozen=True)
class FactCheck:
    question_id: str
    gold: str
    shown: str | None  # the headline fact's display, or why there was none
    ok: bool


def headline(facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The fact an answer would lead with: an average if there is one, else the last computed."""
    ok = [f for f in facts if f.get("error") is None]
    averages = [f for f in ok if f["period"].endswith("average")]
    pick = averages or ok
    return pick[-1] if pick else None


def fact_route_accuracy(
    conn: psycopg.Connection[Any], run_id: int, labels: dict[str, RouteLabel]
) -> list[FactCheck]:
    """For questions labelled `fact` whose gold answer is a bare number: does the headline
    computed fact equal it?"""
    rows = conn.execute(
        "SELECT r.question_id, q.answer, r.route FROM run_questions r"
        " JOIN questions q ON q.id = r.question_id WHERE r.run_id = %s ORDER BY 1",
        (run_id,),
    ).fetchall()
    out = []
    for qid, answer, route in rows:
        if labels.get(qid) is None or labels[qid].route != "fact":
            continue
        gold = gold_number(answer)
        if gold is None:
            continue
        top = headline((route or {}).get("facts", []))
        if top is None:
            out.append(FactCheck(qid, answer, None if top is None else top["display"], False))
            continue
        unit = (
            "usd"
            if top["display"].startswith("$")
            else "pct"
            if top["display"].endswith("%")
            else "x"
        )
        value = top["value"]
        out.append(FactCheck(qid, answer, top["display"], fact_matches_gold(value, unit, gold)))
    return out
