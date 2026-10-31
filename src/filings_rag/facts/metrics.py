"""Metrics library (`metrics@1`): deterministic values from XBRL facts.

Each metric is computed for one FinanceBench period from items in the concept map, and returns
its value, a formula with the numbers filled in, and the facts it used, so an answer can cite
its sources. Definitions are standard textbook ones (P2 decision 4), not FinanceBench's gold
conventions. Where a question spells out a different formula, the caller picks the matching
option (e.g. `dpo` with `denominator="purchases"`, `inventory_turnover` with `average=False`).

- Ratios and margins are fractions (0.135, not 13.5); `unit` says how to show them.
- Average-balance metrics (roa, asset and fixed-asset turnover, inventory turnover, dio, dso,
  dpo, ccc) average the period's closing balance with the prior period's.
- Growth compares with the prior period. CAGR needs `since`, the first period.
- Missing inputs raise MissingInput, naming the item and period. No metric guesses or
  substitutes a different line, except where an option says so (quick ratio's optional
  short-term investments).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from filings_rag.facts.lookup import Fact, Period, period_label

VERSION = "metrics@1"
DAYS = 365


class MissingInput(Exception):
    def __init__(self, item: str, period: Period) -> None:
        super().__init__(f"no {item} for {period_label(period)}")
        self.item = item
        self.period = period


class Facts(Protocol):
    def get(self, item_name: str, period: Period) -> Fact | None: ...


@dataclass
class _Ctx:
    facts: Facts
    used: list[Fact] = field(default_factory=list)

    def v(self, item: str, p: Period, *, optional: bool = False) -> float:
        f = self.facts.get(item, p)
        if f is None:
            if optional:
                return 0.0
            raise MissingInput(item, p)
        if f not in self.used:
            self.used.append(f)
        return f.value

    def avg(self, item: str, p: Period) -> float:
        return (self.v(item, p) + self.v(item, prior(p))) / 2


@dataclass(frozen=True)
class MetricValue:
    metric: str
    period: Period
    value: float
    unit: str  # usd | ratio (x) | pct (a fraction shown as %) | days
    formula: str  # with the numbers filled in
    facts: tuple[Fact, ...]

    def display(self) -> str:
        if self.unit == "usd":
            return f"${self.value / 1e6:,.0f}M"
        if self.unit == "pct":
            return f"{100 * self.value:.1f}%"
        if self.unit == "days":
            return f"{self.value:.2f} days"
        return f"{self.value:.2f}"


def prior(p: Period) -> Period:
    return (p[0] - 1, p[1])


def _m(x: float) -> str:
    return f"{x / 1e6:,.0f}"


def _div(a: float, b: float, what: str) -> float:
    if b == 0:
        raise ZeroDivisionError(f"{what} is zero")
    return a / b


Fn = Callable[[_Ctx, Period, dict[str, Any]], tuple[float, str]]


@dataclass(frozen=True)
class Metric:
    name: str
    unit: str
    inputs: tuple[str, ...]  # required items; must match concepts.toml [metrics]
    fn: Fn
    options: dict[str, tuple[Any, ...]] = field(
        default_factory=dict
    )  # name -> choices, first = default


REGISTRY: dict[str, Metric] = {}


def metric(
    name: str, unit: str, inputs: tuple[str, ...], **options: tuple[Any, ...]
) -> Callable[[Fn], Fn]:
    def register(fn: Fn) -> Fn:
        REGISTRY[name] = Metric(name, unit, inputs, fn, options)
        return fn

    return register


def _ratio(num: str, den: str) -> Fn:
    def fn(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
        a, b = c.v(num, p), c.v(den, p)
        return _div(a, b, den), f"{num} {_m(a)} / {den} {_m(b)}"

    return fn


for _name, _num, _den, _unit in [
    ("current_ratio", "current_assets", "current_liabilities", "ratio"),
    ("operating_margin", "operating_income", "revenue", "pct"),
    ("net_margin", "net_income", "revenue", "pct"),
    ("cogs_margin", "cogs", "revenue", "pct"),
    ("da_margin", "d_and_a", "revenue", "pct"),
    ("capex_pct_revenue", "capex", "revenue", "pct"),
    ("capital_intensity", "total_assets", "revenue", "ratio"),
    ("fixed_asset_ratio", "ppe_net", "total_assets", "pct"),
    ("ocf_ratio", "cfo", "current_liabilities", "ratio"),
    ("effective_tax_rate", "income_tax", "pretax_income", "pct"),
    ("payout_ratio", "dividends_paid", "net_income", "ratio"),
]:
    metric(_name, _unit, (_num, _den))(_ratio(_num, _den))


@metric("working_capital", "usd", ("current_assets", "current_liabilities"))
def _working_capital(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
    a, b = c.v("current_assets", p), c.v("current_liabilities", p)
    return a - b, f"current_assets {_m(a)} - current_liabilities {_m(b)}"


@metric("quick_ratio", "ratio", ("cash", "accounts_receivable", "current_liabilities"))
def _quick_ratio(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
    """(cash + short-term investments + receivables) / current liabilities. Short-term
    investments count as 0 when the filing reports none."""
    cash, sti = c.v("cash", p), c.v("short_term_investments", p, optional=True)
    ar, cl = c.v("accounts_receivable", p), c.v("current_liabilities", p)
    return (
        _div(cash + sti + ar, cl, "current_liabilities"),
        f"(cash {_m(cash)} + short_term_investments {_m(sti)} + accounts_receivable {_m(ar)})"
        f" / current_liabilities {_m(cl)}",
    )


@metric("gross_margin", "pct", ("revenue", "cogs"))
def _gross_margin(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
    rev, cogs = c.v("revenue", p), c.v("cogs", p)
    return _div(
        rev - cogs, rev, "revenue"
    ), f"(revenue {_m(rev)} - cogs {_m(cogs)}) / revenue {_m(rev)}"


@metric("ebitda", "usd", ("operating_income", "d_and_a"))
def _ebitda(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
    oi, da = c.v("operating_income", p), c.v("d_and_a", p)
    return oi + da, f"operating_income {_m(oi)} + d_and_a {_m(da)}"


@metric("ebitda_margin", "pct", ("operating_income", "d_and_a", "revenue"))
def _ebitda_margin(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
    oi, da, rev = c.v("operating_income", p), c.v("d_and_a", p), c.v("revenue", p)
    return (
        _div(oi + da, rev, "revenue"),
        f"(operating_income {_m(oi)} + d_and_a {_m(da)}) / revenue {_m(rev)}",
    )


@metric("fcf", "usd", ("cfo", "capex"))
def _fcf(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
    cfo, capex = c.v("cfo", p), c.v("capex", p)
    return cfo - capex, f"cfo {_m(cfo)} - capex {_m(capex)}"


@metric("fcf_conversion", "ratio", ("cfo", "capex", "net_income"))
def _fcf_conversion(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
    cfo, capex, ni = c.v("cfo", p), c.v("capex", p), c.v("net_income", p)
    return (
        _div(cfo - capex, ni, "net_income"),
        f"(cfo {_m(cfo)} - capex {_m(capex)}) / net_income {_m(ni)}",
    )


@metric("retention_ratio", "ratio", ("dividends_paid", "net_income"))
def _retention(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
    d, ni = c.v("dividends_paid", p), c.v("net_income", p)
    return 1 - _div(d, ni, "net_income"), f"1 - dividends_paid {_m(d)} / net_income {_m(ni)}"


def _turnover(num: str, bal: str) -> Fn:
    def fn(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
        n = c.v(num, p)
        if o["average"]:
            b = c.avg(bal, p)
            return _div(n, b, bal), f"{num} {_m(n)} / average {bal} {_m(b)}"
        b = c.v(bal, p)
        return _div(n, b, bal), f"{num} {_m(n)} / {bal} {_m(b)}"

    return fn


for _name, _num, _bal in [
    ("roa", "net_income", "total_assets"),
    ("asset_turnover", "revenue", "total_assets"),
    ("fixed_asset_turnover", "revenue", "ppe_net"),
    ("inventory_turnover", "cogs", "inventory"),
]:
    metric(_name, "pct" if _name == "roa" else "ratio", (_num, _bal), average=(True, False))(
        _turnover(_num, _bal)
    )


def _days(c: _Ctx, p: Period, bal: str, flow: str) -> tuple[float, str]:
    b, f = c.avg(bal, p), c.v(flow, p)
    return DAYS * _div(b, f, flow), f"{DAYS} x average {bal} {_m(b)} / {flow} {_m(f)}"


def _dpo_value(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
    """365 x average payables / COGS (textbook), or / purchases (COGS + change in inventory)."""
    ap, cogs = c.avg("accounts_payable", p), c.v("cogs", p)
    if o["denominator"] == "purchases":
        d_inv = c.v("inventory", p) - c.v("inventory", prior(p))
        den = cogs + d_inv
        return (
            DAYS * _div(ap, den, "purchases"),
            f"{DAYS} x average accounts_payable {_m(ap)} / (cogs {_m(cogs)} + change in"
            f" inventory {_m(d_inv)})",
        )
    return DAYS * _div(
        ap, cogs, "cogs"
    ), f"{DAYS} x average accounts_payable {_m(ap)} / cogs {_m(cogs)}"


metric("dpo", "days", ("accounts_payable", "cogs", "inventory"), denominator=("cogs", "purchases"))(
    _dpo_value
)


@metric(
    "ccc",
    "days",
    ("inventory", "cogs", "accounts_receivable", "revenue", "accounts_payable"),
    denominator=("cogs", "purchases"),
)
def _ccc(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
    """DIO + DSO - DPO; `denominator` is DPO's."""
    dio, f1 = _days(c, p, "inventory", "cogs")
    dso, f2 = _days(c, p, "accounts_receivable", "revenue")
    dpo, f3 = _dpo_value(c, p, o)
    return dio + dso - dpo, f"DIO ({f1}) + DSO ({f2}) - DPO ({f3})"


def _growth(item: str) -> Fn:
    def fn(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
        now, before = c.v(item, p), c.v(item, prior(p))
        return _div(now, before, f"prior {item}") - 1, f"{item} {_m(now)} / prior {_m(before)} - 1"

    return fn


metric("revenue_growth", "pct", ("revenue",))(_growth("revenue"))
metric("operating_income_growth", "pct", ("operating_income",))(_growth("operating_income"))


@metric("revenue_cagr", "pct", ("revenue",), since=(None,))
def _cagr(c: _Ctx, p: Period, o: dict[str, Any]) -> tuple[float, str]:
    since = o["since"]
    if since is None or since[0] >= p[0]:
        raise ValueError("revenue_cagr needs `since`, a period before the end period")
    years = p[0] - since[0]
    end, start = c.v("revenue", p), c.v("revenue", since)
    return (
        _div(end, start, "starting revenue") ** (1 / years) - 1,
        f"(revenue {_m(end)} / revenue {period_label(since)} {_m(start)})^(1/{years}) - 1",
    )


def compute(facts: Facts, name: str, period: Period, **options: Any) -> MetricValue:
    """One metric (or a single item, as itself) for one period."""
    ctx = _Ctx(facts)
    if name not in REGISTRY:
        value = ctx.v(name, period)  # a plain statement item
        return MetricValue(name, period, value, "usd", f"{name} {_m(value)}", tuple(ctx.used))
    m = REGISTRY[name]
    unknown = options.keys() - m.options.keys()
    if unknown:
        raise ValueError(f"{name} has no options {sorted(unknown)}")
    opts = {k: choices[0] for k, choices in m.options.items()} | options
    for k, choices in m.options.items():
        if choices != (None,) and opts[k] not in choices:
            raise ValueError(f"{name} {k} must be one of {list(choices)}")
    value, formula = m.fn(ctx, period, opts)
    return MetricValue(name, period, value, m.unit, formula, tuple(ctx.used))


def average(
    facts: Facts, name: str, periods: list[Period], **options: Any
) -> tuple[float, list[MetricValue]]:
    """The mean of a metric over several periods (e.g. a 3-year average margin)."""
    values = [compute(facts, name, p, **options) for p in periods]
    return sum(v.value for v in values) / len(values), values
