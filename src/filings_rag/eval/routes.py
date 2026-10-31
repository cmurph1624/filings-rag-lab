"""Hand route labels for the question set (`corpus/route_labels.csv`, frozen as `routes@1`).

Each question gets the route an ideal router would pick:
- `fact`: the answer comes entirely from non-dimensional financial-statement lines, so XBRL facts
  plus arithmetic can answer it.
- `mixed`: a statement-line metric plus a judgment or explanation ("is working capital useful
  here?", "is this capital intensive?").
- `prose`: everything else: drivers, segments and regions (dimensional), guidance, non-GAAP
  figures, 8-K events, legal and business descriptions.

`metrics` names what a fact or mixed answer needs, from METRICS (P2's concept map and metrics
library implement these names). `periods` are the fiscal periods the answer needs data from,
using FinanceBench's labels ("FY2022", "Q2 FY2023"). When a question names no period, they come
from its gold document. `multi_period` is true when more than one period is needed.

The labels were written before any P2 router existed, so router accuracy against them is not
tuned to the router. Change them only as a new version.
"""

import csv
import re
from dataclasses import dataclass
from pathlib import Path

VERSION = "routes@1"
ROUTES = ("fact", "mixed", "prose")
METRICS = frozenset(
    {
        # statement lines
        "revenue", "cogs", "net_income", "total_assets", "current_assets",
        "current_liabilities", "inventory", "accounts_receivable", "accounts_payable", "ppe_net",
        "cash", "debt", "capex", "cfo", "cfi", "cff", "dividends_paid",
        # derived
        "working_capital", "current_ratio", "quick_ratio", "gross_margin", "operating_margin",
        "net_margin", "cogs_margin", "da_margin", "ebitda", "ebitda_margin", "fcf",
        "fcf_conversion", "capex_pct_revenue", "capital_intensity", "fixed_asset_ratio", "roa",
        "asset_turnover", "fixed_asset_turnover", "inventory_turnover", "dpo", "ccc",
        "ocf_ratio", "effective_tax_rate", "payout_ratio", "retention_ratio", "revenue_growth",
        "revenue_cagr", "operating_income_growth",
    }
)  # fmt: skip
PERIOD = re.compile(r"(Q[1-4] )?FY\d{4}")


@dataclass(frozen=True)
class RouteLabel:
    question_id: str
    route: str
    metrics: tuple[str, ...]
    periods: tuple[str, ...]
    multi_period: bool


def parse_row(row: dict[str, str]) -> RouteLabel:
    """One CSV row, validated. Raises ValueError naming the question on any problem."""
    qid, route = row["question_id"], row["route"]
    metrics = tuple(m for m in row["metrics"].split(";") if m)
    periods = tuple(p for p in row["periods"].split(";") if p)
    multi = row["multi_period"]

    def bad(why: str) -> ValueError:
        return ValueError(f"{qid}: {why}")

    if route not in ROUTES:
        raise bad(f"unknown route {route!r}")
    if unknown := [m for m in metrics if m not in METRICS]:
        raise bad(f"unknown metrics {unknown}")
    if (route == "prose") == bool(metrics):
        raise bad("prose takes no metrics; fact and mixed need at least one")
    if not periods or any(not PERIOD.fullmatch(p) for p in periods):
        raise bad(f"periods must be FY2022 or Q2 FY2023 style: {row['periods']!r}")
    if multi not in ("true", "false") or (multi == "true") != (len(periods) > 1):
        raise bad("multi_period must be true exactly when there is more than one period")
    return RouteLabel(qid, route, metrics, periods, multi == "true")


def load(path: Path) -> dict[str, RouteLabel]:
    with path.open(newline="") as f:
        labels = [parse_row(r) for r in csv.DictReader(f)]
    out = {lab.question_id: lab for lab in labels}
    if len(out) != len(labels):
        raise ValueError(f"{path}: duplicate question ids")
    return out
