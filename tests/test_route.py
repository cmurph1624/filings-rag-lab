from datetime import date

import pytest

from filings_rag.facts.lookup import Fact, Period
from filings_rag.harness.config import from_dict
from filings_rag.harness.prompts import get_prompt
from filings_rag.retrieve.base import Hit
from filings_rag.route import evaluate as E
from filings_rag.route import facts as F
from filings_rag.route.plan import RoutePlan, parse_llm_plan, rules_plan
from filings_rag.route.router import resolve_router

FY = lambda y: (y, None)  # noqa: E731


@pytest.mark.parametrize(
    ("question", "route", "metrics", "periods"),
    [
        (
            "What is Amazon's FY2017 days payable outstanding (DPO)? DPO is defined as: 365 * "
            "(average accounts payable between FY2016 and FY2017) / (FY2017 COGS + change in "
            "inventory between FY2016 and FY2017).",
            "fact", ("dpo",), (FY(2016), FY(2017)),
        ),
        (
            "Does Corning have positive working capital based on FY2022 data? If working capital "
            "is not a useful or relevant metric for this company, then please state that and "
            "explain why.",
            "mixed", ("working_capital",), (FY(2022),),
        ),
        (
            "What is the FY2015 - FY2017 3 year average net profit margin (as a %) for Best Buy?",
            "fact", ("net_margin",), (FY(2015), FY(2016), FY(2017)),
        ),
        ("What drove gross margin change as of FY2022 for JnJ?", "prose", (), (FY(2022),)),
        (
            "Which Best Buy product category performed the best in Q2 of FY2024?",
            "prose", (), ((2024, 2),),
        ),
        (
            "Does Adobe have an improving operating margin profile as of FY2022?",
            "mixed", ("operating_margin",), (FY(2021), FY(2022)),
        ),
        (
            "What is the FY2022 unadjusted EBITDA % margin for PepsiCo? Calculate unadjusted "
            "EBITDA using unadjusted operating income and D&A (from cash flow statement).",
            "fact", ("ebitda_margin",), (FY(2022),),
        ),
    ],
)  # fmt: skip
def test_rules_plan(
    question: str, route: str, metrics: tuple[str, ...], periods: tuple[Period, ...]
) -> None:
    plan = rules_plan(question)
    assert (plan.route, plan.metrics, plan.periods) == (route, metrics, periods)


def test_rules_plan_options_and_aggregate() -> None:
    plan = rules_plan("FY2019 DPO = 365 * avg AP / (FY2019 COGS + change in inventory)")
    assert plan.options == {"dpo": {"denominator": "purchases"}}
    avg = rules_plan(
        "What is the FY2018 - FY2020 3 year average of capex as a % of revenue for MGM?"
    )
    assert avg.aggregate == "average" and avg.metrics == ("capex_pct_revenue",)


def test_llm_plan_parsing_is_lenient() -> None:
    plan = parse_llm_plan(
        'Sure: {"route": "fact", "metrics": ["dpo", "ebit"], "periods": ["FY2017", "2016"],'
        ' "options": {"dpo": {"denominator": "purchases"}, "ebit": {}}, "aggregate": "sum"}'
    )
    assert plan == RoutePlan("fact", ("dpo",), (FY(2017),), {"dpo": {"denominator": "purchases"}})
    assert parse_llm_plan('{"route": "fact", "metrics": []}').route == "prose"
    assert parse_llm_plan('{"route": "prose", "metrics": ["dpo"]}').metrics == ()
    with pytest.raises(ValueError):
        parse_llm_plan("no json here")


def test_plan_json_round_trip() -> None:
    plan = RoutePlan("mixed", ("quick_ratio",), (FY(2022), (2023, 2)), {}, "none")
    assert RoutePlan.from_json(plan.to_json()) == plan


def test_resolve_router_and_hash_neutrality() -> None:
    assert resolve_router(None) is None
    assert resolve_router({"mode": "llm"})["model"] == "claude-haiku-5-5"  # type: ignore[index]
    with pytest.raises(ValueError, match="router mode"):
        resolve_router({"mode": "magic"})
    with pytest.raises(ValueError, match="options"):
        resolve_router({"mode": "rules", "model": "x"})
    base = {"name": "x", "pipeline": "naive-fixed@1", "retriever": {"kind": "vector", "k": 10}}
    routed = from_dict(base | {"router": {"mode": "rules"}})
    assert from_dict(base).hash != routed.hash
    assert from_dict(base).router is None and routed.router == {"mode": "rules"}


def test_choose_anchor_prefers_the_latest_periods_filing() -> None:
    docs = ["3M_2023Q2_10Q", "3M_2023_10K", "3M_2022_10K", "3M_2023Q4_EARNINGS"]
    assert (
        F.choose_anchor(docs, RoutePlan("mixed", ("quick_ratio",), ((2023, 2),))) == "3M_2023Q2_10Q"
    )
    assert F.choose_anchor(docs, RoutePlan("fact", ("cash",), (FY(2022),))) == "3M_2022_10K"
    assert F.choose_anchor(docs, RoutePlan("fact", ("cash",), (FY(2015),))) == "3M_2023_10K"
    assert (
        F.choose_anchor(["3M_2023Q4_EARNINGS"], RoutePlan("fact", ("cash",), (FY(2023),))) is None
    )


def test_targets() -> None:
    p = (FY(2016), FY(2017))
    assert F.targets("dpo", p) == [(FY(2017), {})]
    assert F.targets("revenue_growth", p) == [(FY(2017), {})]
    assert F.targets("net_margin", p) == [(FY(2016), {}), (FY(2017), {})]
    assert F.targets("revenue_cagr", (FY(2020), FY(2021), FY(2022))) == [
        (FY(2022), {"since": FY(2020)})
    ]
    assert F.targets("dpo", (FY(2017),)) == [(FY(2017), {})]


class Fake:
    def __init__(self, values: dict[tuple[str, Period], float]) -> None:
        self.values = values

    def get(self, item_name: str, period: Period) -> Fact | None:
        v = self.values.get((item_name, period))
        return (
            None
            if v is None
            else Fact(item_name, period, v, "C", "A", date(period[0], 12, 31), True)
        )


def test_compute_plan_average_errors_and_render() -> None:
    facts = Fake({("net_income", FY(y)): 10.0 * y % 7 + 1 for y in (2015, 2016)} | {
        ("revenue", FY(y)): 100.0 for y in (2015, 2016, 2017)})  # fmt: skip
    plan = RoutePlan("fact", ("net_margin",), (FY(2015), FY(2016), FY(2017)), {}, "average")
    results = F.compute_plan(facts, plan)
    assert [r.period for r in results] == ["FY2015", "FY2016", "FY2017"]  # one failed: no average
    assert results[2].error == "no net_income for FY2017"
    block = F.render_block("BBY_2017_10K", results)
    assert block.startswith("Facts (computed from XBRL financial data; anchor filing BBY_2017_10K)")
    assert "[F3] net_margin FY2017: unavailable (no net_income for FY2017)" in block
    facts.values[("net_income", FY(2017))] = 4.0
    results = F.compute_plan(facts, plan)
    assert results[-1].period == "FY2015–FY2017 average" and results[-1].value == pytest.approx(
        sum(facts.values[("net_income", FY(y))] / 100 for y in (2015, 2016, 2017)) / 3
    )
    assert F.render_block("X", []) == ""


def test_answer4_puts_facts_before_excerpts_and_older_prompts_ignore_them() -> None:
    hits = [Hit(1, "AMAZON_2017_10K", 40, 0.9, "x")]
    out = get_prompt("answer@4").render(
        "q", hits, {}, "Facts (...):\n\n[F1] dpo FY2017 = 93.86 days\n\n"
    )
    assert out.startswith("Facts (...):") and "Excerpts:\n\n[1] AMAZON_2017_10K, page 40" in out
    assert "Facts" not in get_prompt("answer@1").render("q", hits, {}, "Facts (...)")


@pytest.mark.parametrize(
    ("answer", "value", "unit", "ok"),
    [
        ("$5409.00", 5409e6, "usd", True),
        ("2.8%", 0.028, "pct", True),
        ("93.86", 93.857, "days", True),
        ("-3.7", -3.71, "days", True),
        ("0.01", 0.0125, "pct", True),  # ROA given as a fraction
        ("$9068.00", 5207e6, "usd", False),
    ],
)
def test_fact_matches_gold(answer: str, value: float, unit: str, ok: bool) -> None:
    gold = E.gold_number(answer)
    assert gold is not None and E.fact_matches_gold(value, unit, gold) is ok


def test_gold_number_only_for_bare_numbers() -> None:
    assert E.gold_number("Yes, it rose from 20% to 23%") is None
    assert E.gold_number("$4.60") == (4.6, 2, False)
