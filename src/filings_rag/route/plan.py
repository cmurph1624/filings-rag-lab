"""The route plan and the rules router."""

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from filings_rag.eval.routes import METRICS, ROUTES
from filings_rag.facts.lookup import Period, parse_period, period_label
from filings_rag.facts.metrics import REGISTRY
from filings_rag.retrieve.scope import parse_periods

AGGREGATES = ("none", "average")


@dataclass(frozen=True)
class RoutePlan:
    route: str  # fact | mixed | prose
    metrics: tuple[str, ...] = ()
    periods: tuple[Period, ...] = ()
    options: dict[str, dict[str, Any]] = field(default_factory=dict)  # metric -> {option: value}
    aggregate: str = "none"

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["periods"] = [period_label(p) for p in self.periods]
        d["metrics"] = list(self.metrics)
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "RoutePlan":
        """Lenient: unknown routes become prose, unknown metrics and bad periods are dropped, so
        a model's reply can't inject names the facts layer doesn't know."""
        route = str(d["route"]) if d.get("route") in ROUTES else "prose"
        metrics = tuple(m for m in d.get("metrics") or [] if isinstance(m, str) and m in METRICS)
        periods: set[Period] = set()
        for p in d.get("periods") or []:
            try:
                periods.add(parse_period(str(p)))
            except ValueError:
                continue
        raw = d.get("options")
        raw_opts: dict[str, Any] = raw if isinstance(raw, dict) else {}
        options = {m: dict(o) for m, o in raw_opts.items() if m in metrics and isinstance(o, dict)}
        aggregate = str(d["aggregate"]) if d.get("aggregate") in AGGREGATES else "none"
        if not metrics:
            route = "prose"
        elif route == "prose":
            metrics, options = (), {}
        return cls(route, metrics, tuple(sorted(periods, key=_period_key)), options, aggregate)


def _period_key(p: Period) -> tuple[int, int]:
    return p[0], p[1] or 0


# --- the rules router -------------------------------------------------------------------------

# (pattern, metrics), most specific first: the first match per span wins, and a matched span is
# blanked so "working capital ratio" doesn't also match "working capital".
_I = re.IGNORECASE
METRIC_PATTERNS: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(p, _I), ms)
    for p, ms in [
        (r"days payable outstanding|\bDPO\b", ("dpo",)),
        (r"cash conversion cycle|\bCCC\b", ("ccc",)),
        (r"inventory turnover|sold its inventory", ("inventory_turnover",)),
        (r"fixed asset turnover", ("fixed_asset_turnover",)),
        (r"asset turnover", ("asset_turnover",)),
        (r"return on assets|\bROA\b", ("roa",)),
        (r"quick ratio", ("quick_ratio",)),
        (r"working capital ratio|current ratio", ("current_ratio",)),
        (r"working capital", ("working_capital",)),
        (r"operating cash flow ratio", ("ocf_ratio",)),
        (r"free cash ?flow conversion|FCF conversion", ("fcf_conversion",)),
        (r"free cash flow|\bFCF\b", ("fcf",)),
        (r"EBITDA less capex", ("ebitda", "capex")),
        (r"EBITDA (?:%|percent) margin|EBITDA margin", ("ebitda_margin",)),
        (r"\bEBITDA\b(?!R)", ("ebitda",)),
        (r"(?:D&A|depreciation and amortization)[^?]*?(?:%|percent) margin", ("da_margin",)),
        (r"COGS (?:%|percent) margin|cost of goods sold as a % of revenue", ("cogs_margin",)),
        (r"gross margin", ("gross_margin",)),
        (r"operating (?:income )?(?:%|percent) margin|operating margin", ("operating_margin",)),
        (r"net (?:profit|income) margin|net earnings as a percent of sales", ("net_margin",)),
        (r"capex as a % of revenue", ("capex_pct_revenue",)),
        (r"capital[- ]intensive", ("capex_pct_revenue", "fixed_asset_ratio", "roa")),
        (r"capital expenditure|\bcapex\b", ("capex",)),
        (r"effective tax rate", ("effective_tax_rate",)),
        (r"(?:dividend )?payout ratio", ("payout_ratio",)),
        (r"retention ratio", ("retention_ratio",)),
        (r"\bCAGR\b", ("revenue_cagr",)),
        (r"change in (?:unadjusted )?operating income", ("operating_income_growth",)),
        (r"revenue growth|growth rate|change in revenue|high growth", ("revenue_growth",)),
        (r"operations, investing, and financing", ("cfo", "cfi", "cff")),
        (r"cash flow from operating|cash from operations", ("cfo",)),
        (r"total current liabilities", ("current_liabilities",)),
        (r"total current assets", ("current_assets",)),
        (r"total assets", ("total_assets",)),
        (r"\bPPNE\b|PP&E|property, plant,? and equipment", ("ppe_net",)),
        (r"inventor(?:y|ies)", ("inventory",)),
        (r"accounts payable", ("accounts_payable",)),
        (r"\bnet AR\b|accounts receivable", ("accounts_receivable",)),
        (r"\bCOGS\b|cost of goods sold", ("cogs",)),
        (r"cash dividends|paid out in .*dividends", ("dividends_paid",)),
        (r"net income", ("net_income",)),
        (r"cash (?:&|and) cash equivalents", ("cash",)),
        (r"\bdebt\b", ("debt",)),
    ]
]
# Prose cues: the answer is an explanation, a breakdown the facts table doesn't have
# (segments, regions, categories), or a figure XBRL doesn't carry (non-GAAP, guidance).
PROSE = re.compile(
    r"\b(?:drove|driv(?:e|er|ers|ing)|why|segment|region|categor|geograph|guidance|outlook"
    r"|adjusted|non[- ]?GAAP|forecast|expect|restructuring|acquisition|legal|customer"
    r"|debt securities|per share|liquidat|event|largest|highest|lowest|biggest|worst"
    r"|perform(?:ed)? the best)",  # not bare "best": Best Buy, "to the best of your abilities"
    _I,
)
JUDGMENT = re.compile(
    r"not (?:a )?(?:useful|relevant|meaningful)|not something|healthy|capital[- ]intensive"
    r"|improving|consistent"
    r"|high growth|reasonably",
    _I,
)
# FinanceBench's boilerplate "If <metric> is not useful here, state that and explain why" is an
# instruction, not a prose cue: it is ignored when looking for prose cues.
INSTRUCTION = re.compile(r"\bIf [^.?]*\b(?:not|isn't)\b[^.?]*[.?]", _I)
# A comparison asked "as of" one year needs the year before too.
NEEDS_PRIOR = re.compile(r"improving|consistent|high growth|growth|grow\b|increase|decrease", _I)
# A metric that already contains another: the question's definition of the inner one is part of
# the outer one ("CCC ... DPO is defined as ...").
SUBSUMES = {"ebitda_margin": {"ebitda"}, "ccc": {"dpo"}, "fcf_conversion": {"fcf"}}
RANGE = re.compile(r"FY\s*(\d{4})\s*(?:-|–|to|through)\s*FY\s*(\d{4})", _I)
PURCHASES = re.compile(r"change in inventory", _I)


def rules_periods(text: str) -> tuple[Period, ...]:
    years, quarters = parse_periods(text)
    ys = set(years) - {y for y, _ in quarters}
    for m in RANGE.finditer(text):
        a, b = sorted((int(m[1]), int(m[2])))
        ys |= set(range(a, b + 1))
    out: set[Period] = {(y, None) for y in ys} | {(y, q) for y, q in quarters}
    return tuple(sorted(out, key=_period_key))


def rules_plan(text: str) -> RoutePlan:
    rest, metrics = text, []
    for pat, ms in METRIC_PATTERNS:
        if pat.search(rest):
            metrics += [m for m in ms if m not in metrics]
            rest = pat.sub(" ", rest)
    covered = {i for m in metrics if m in REGISTRY for i in REGISTRY[m].inputs}
    covered |= {i for m in metrics for i in SUBSUMES.get(m, set())}
    metrics = [m for m in metrics if m not in covered]  # parts of another metric aren't asked for
    periods = rules_periods(text)
    if not metrics or PROSE.search(INSTRUCTION.sub(" ", text)):
        return RoutePlan("prose", periods=periods)
    annual = [p for p in periods if p[1] is None]
    if len(periods) == 1 and annual and NEEDS_PRIOR.search(text):
        periods = ((annual[0][0] - 1, None), annual[0])
    route = "mixed" if JUDGMENT.search(text) else "fact"
    options: dict[str, dict[str, Any]] = {}
    if PURCHASES.search(text):
        options |= {m: {"denominator": "purchases"} for m in metrics if m in ("dpo", "ccc")}
    aggregate = "average" if re.search(r"year average", text, _I) and len(periods) > 1 else "none"
    return RoutePlan(route, tuple(metrics), periods, options, aggregate)


# --- the llm router's prompt (versioned: never edit in place, add route@n+1) ----------------


def _vocabulary() -> str:
    items = sorted(m for m in METRICS if m not in REGISTRY)
    derived = sorted(REGISTRY)
    return (
        "Statement items: " + ", ".join(items) + ".\nDerived metrics: " + ", ".join(derived) + "."
    )


@dataclass(frozen=True)
class RoutePrompt:
    system: str
    template: str  # {vocabulary}, {question}

    def render(self, question: str) -> str:
        return self.template.format(vocabulary=_vocabulary(), question=question)


ROUTE_V1 = RoutePrompt(
    system=(
        "You plan how to answer a question about a US public company's SEC filings. Reply "
        "with one JSON object and nothing else."
    ),
    template=(
        "Metric names you may use:\n{vocabulary}\n\n"
        "Question: {question}\n\n"
        'Reply with {{"route": ..., "metrics": [...], "periods": [...], "options": {{...}},'
        ' "aggregate": ...}} where:\n'
        '- route is "fact" if the answer comes entirely from financial-statement line items '
        '(balance sheet, income statement, cash flow) and arithmetic; "mixed" if it needs such '
        "a metric plus a judgment or explanation (is it healthy, is it useful for this company, "
        'is it improving); "prose" for anything else: drivers and reasons, segments, regions, '
        "product categories, guidance, non-GAAP or adjusted figures, events, legal matters;\n"
        "- metrics are names from the list above (empty for prose);\n"
        '- periods are the fiscal periods the answer needs data from, as "FY2022" or '
        '"Q2 FY2023", including every year in a range and the prior year of any average;\n'
        '- options only when the question defines a formula differently: {{"dpo": '
        '{{"denominator": "purchases"}}}} when DPO or CCC divides by COGS plus the change in '
        'inventory; {{"<turnover metric>": {{"average": false}}}} when it uses the ending '
        "balance;\n"
        '- aggregate is "average" when the question asks for an average over several years, '
        'else "none".'
    ),
)
ROUTE_PROMPTS: dict[str, RoutePrompt] = {"route@1": ROUTE_V1}

JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_llm_plan(text: str) -> RoutePlan:
    m = JSON_OBJECT.search(text)
    if m is None:
        raise ValueError(f"route reply has no JSON object: {text[:200]!r}")
    data = json.loads(m[0])
    if not isinstance(data, dict):
        raise ValueError(f"route reply is not a JSON object: {m[0][:200]!r}")
    return RoutePlan.from_json(data)
