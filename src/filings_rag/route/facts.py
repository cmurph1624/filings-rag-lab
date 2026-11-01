"""Turn a route plan into computed facts and the prompt's Facts block.

The anchor filing comes from the question's scope, never from the gold label: the scope's
10-K or 10-Q for the plan's latest period, else its latest 10-K or 10-Q. Facts are then as
filed in that filing where it reports them (`FactLookup`).

Targets per metric, from the plan's periods (the periods the answer needs data from):
- revenue_cagr: one value, from the first period to the last.
- growth and average-balance metrics: every period but the earliest, which they use as the
  prior year.
- everything else: every period. With aggregate "average", the mean is added.
A metric that can't be computed for a target (a missing input, a quarter for an annual-only
metric) is listed as unavailable, with the reason, rather than dropped silently.
"""

from dataclasses import asdict, dataclass, field
from typing import Any

from filings_rag.edgar.resolve import parse_doc_name
from filings_rag.facts import metrics as M
from filings_rag.facts.lookup import Period, period_label
from filings_rag.route.plan import RoutePlan

GROWTH = {"revenue_growth", "operating_income_growth"}
USES_PRIOR = GROWTH | set(M.ANNUAL_ONLY)


@dataclass(frozen=True)
class FactResult:
    metric: str
    period: str  # label, or "FY2018–FY2020 average"
    value: float | None = None
    display: str | None = None
    formula: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def choose_anchor(scope_docs: list[str], plan: RoutePlan) -> str | None:
    periodic = [d for d in scope_docs if d.endswith(("_10K", "_10Q")) or "_10K_" in d]
    if not periodic:
        return None
    labelled = []
    for d in periodic:
        ref = parse_doc_name(d)
        q = None if ref.fiscal_period == "FY" else int(ref.fiscal_period[1])
        labelled.append(((ref.fiscal_year, q), d))
    if plan.periods:
        latest = max(plan.periods, key=lambda p: (p[0], p[1] or 0))
        exact = sorted(d for p, d in labelled if p == latest)
        if exact:
            return exact[0]
    annual = sorted((p, d) for p, d in labelled if p[1] is None)
    pool = annual or sorted(labelled, key=lambda pd: (pd[0][0], pd[0][1] or 0, pd[1]))
    return pool[-1][1]


def targets(metric: str, periods: tuple[Period, ...]) -> list[tuple[Period, dict[str, Any]]]:
    """(period, extra options) to compute for one metric."""
    ordered = sorted(periods, key=lambda p: (p[0], p[1] or 0))
    if not ordered:
        return []
    if metric == "revenue_cagr":
        return [(ordered[-1], {"since": ordered[0]})] if len(ordered) > 1 else []
    if metric in USES_PRIOR and len(ordered) > 1:
        return [(p, {}) for p in ordered[1:]]
    return [(p, {}) for p in ordered]


def _sources(v: M.MetricValue) -> list[dict[str, Any]]:
    return [
        {
            "item": f.item,
            "period": period_label(f.period),
            "value": f.value,
            "concept": f.concept,
            "accession": f.accession,
            "as_filed": f.as_filed,
        }
        for f in v.facts
    ]


def compute_plan(facts: M.Facts, plan: RoutePlan) -> list[FactResult]:
    out: list[FactResult] = []
    for metric in plan.metrics:
        values: list[M.MetricValue] = []
        wanted = targets(metric, plan.periods)
        for period, extra in wanted:
            opts = plan.options.get(metric, {}) | extra
            try:
                v = M.compute(facts, metric, period, **opts)
            except (M.MissingInput, ValueError, ZeroDivisionError) as e:
                out.append(FactResult(metric, period_label(period), error=str(e)))
                continue
            values.append(v)
            out.append(
                FactResult(
                    metric, period_label(period), v.value, v.display(), v.formula, _sources(v)
                )
            )
        # Only over every target: an average of the years that happened to compute would be
        # shown as the average of all of them.
        if plan.aggregate == "average" and len(values) == len(wanted) > 1 and metric not in GROWTH:
            mean = sum(v.value for v in values) / len(values)
            first, last = period_label(values[0].period), period_label(values[-1].period)
            shown = M.MetricValue(metric, values[-1].period, mean, values[0].unit, "", ())
            out.append(
                FactResult(
                    metric,
                    f"{first}–{last} average",
                    mean,
                    shown.display(),
                    "mean of " + ", ".join(v.display() for v in values),
                )
            )
    return out


def render_block(anchor: str, results: list[FactResult]) -> str:
    """The Facts section of the prompt, or "" when there is nothing to show."""
    if not results:
        return ""
    lines = [f"Facts (computed from XBRL financial data; anchor filing {anchor}):", ""]
    for i, r in enumerate(results, 1):
        if r.error:
            lines.append(f"[F{i}] {r.metric} {r.period}: unavailable ({r.error})")
            continue
        src = "; ".join(
            f"{s['item']} {s['period']} {s['value']:,.0f} ({s['concept']}"
            + ("" if s["as_filed"] else ", from another filing")
            + ")"
            for s in r.sources
        )
        lines.append(f"[F{i}] {r.metric} {r.period} = {r.display}")
        lines.append(f"     {r.formula}" + (f"; {src}" if src else ""))
    return "\n".join(lines) + "\n\n"


def record(plan: RoutePlan, anchor: str | None, results: list[FactResult]) -> dict[str, Any]:
    return {"plan": plan.to_json(), "anchor": anchor, "facts": [asdict(r) for r in results]}
