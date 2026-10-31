import os
from datetime import date
from pathlib import Path

import psycopg
import pytest

from filings_rag.facts import concepts
from filings_rag.facts import metrics as M
from filings_rag.facts.lookup import Fact, FactLookup, Period, read_offsets

ROOT = Path(__file__).resolve().parents[1]
CMAP = concepts.load(ROOT / "config" / "concepts.toml")


class Fake:
    def __init__(self, values: dict[tuple[str, Period], float]) -> None:
        self.values = values

    def get(self, item_name: str, period: Period) -> Fact | None:
        v = self.values.get((item_name, period))
        if v is None:
            return None
        return Fact(item_name, period, v, "C", "A", date(period[0], 12, 31), True)


FY22, FY21 = (2022, None), (2021, None)


def test_registry_matches_the_concept_map() -> None:
    assert set(M.REGISTRY) == set(CMAP.metrics)
    for name, m in M.REGISTRY.items():
        assert m.inputs == CMAP.metrics[name], name


def test_ratio_margin_and_formula() -> None:
    f = Fake({("operating_income", FY22): 13.5e9, ("revenue", FY22): 100e9})
    v = M.compute(f, "operating_margin", FY22)
    assert v.value == pytest.approx(0.135)
    assert v.display() == "13.5%"
    assert v.formula == "operating_income 13,500 / revenue 100,000"
    assert len(v.facts) == 2


def test_average_balance_metrics_need_the_prior_period() -> None:
    f = Fake(
        {("net_income", FY22): 10.0, ("total_assets", FY22): 120.0, ("total_assets", FY21): 80.0}
    )
    assert M.compute(f, "roa", FY22).value == pytest.approx(0.1)
    assert M.compute(f, "roa", FY22, average=False).value == pytest.approx(10 / 120)
    with pytest.raises(M.MissingInput, match="total_assets for FY2021"):
        M.compute(Fake({("net_income", FY22): 1.0, ("total_assets", FY22): 1.0}), "roa", FY22)


def test_dpo_textbook_and_purchases() -> None:
    f = Fake(
        {
            ("accounts_payable", FY22): 110.0,
            ("accounts_payable", FY21): 90.0,
            ("cogs", FY22): 365.0,
            ("inventory", FY22): 60.0,
            ("inventory", FY21): 50.0,
        }
    )
    assert M.compute(f, "dpo", FY22).value == pytest.approx(100.0)
    assert M.compute(f, "dpo", FY22, denominator="purchases").value == pytest.approx(
        365 * 100 / 375
    )
    with pytest.raises(ValueError, match="denominator"):
        M.compute(f, "dpo", FY22, denominator="sales")
    with pytest.raises(ValueError, match="no options"):
        M.compute(f, "dpo", FY22, rounding=2)


def test_quick_ratio_counts_missing_short_term_investments_as_zero() -> None:
    f = Fake(
        {
            ("cash", FY22): 30.0,
            ("accounts_receivable", FY22): 20.0,
            ("current_liabilities", FY22): 100.0,
        }
    )
    assert M.compute(f, "quick_ratio", FY22).value == pytest.approx(0.5)
    f.values[("short_term_investments", FY22)] = 10.0
    assert M.compute(f, "quick_ratio", FY22).value == pytest.approx(0.6)


def test_growth_cagr_items_and_average() -> None:
    f = Fake({("revenue", (2020, None)): 100.0, ("revenue", FY21): 110.0, ("revenue", FY22): 121.0})
    assert M.compute(f, "revenue_growth", FY22).value == pytest.approx(0.1)
    assert M.compute(f, "revenue_cagr", FY22, since=(2020, None)).value == pytest.approx(0.1)
    with pytest.raises(ValueError, match="since"):
        M.compute(f, "revenue_cagr", FY22)
    assert M.compute(f, "revenue", FY21).value == 110.0
    mean, parts = M.average(f, "revenue", [FY21, FY22])
    assert mean == pytest.approx(115.5) and len(parts) == 2


def test_zero_denominator_is_an_error_not_infinity() -> None:
    f = Fake({("operating_income", FY22): 1.0, ("revenue", FY22): 0.0})
    with pytest.raises(ZeroDivisionError, match="revenue"):
        M.compute(f, "operating_margin", FY22)


# --- against FinanceBench gold answers, on the real facts (skipped when not loaded) ---------

GOLD = [
    # question, gold doc, metric, period, options, gold answer, tolerance
    ("04417", "BESTBUY_2019_10K", "inventory", (2019, None), {}, 5409e6, 0.5e6),
    ("06655", "AMAZON_2017_10K", "dpo", (2017, None), {"denominator": "purchases"}, 93.86, 0.01),
    ("10130", "CORNING_2020_10K", "dpo", (2020, None), {"denominator": "purchases"}, 63.86, 0.01),
    ("06247", "WALMART_2018_10K", "dpo", (2018, None), {"denominator": "purchases"}, 42.69, 0.01),
    ("04080", "NIKE_2021_10K", "inventory_turnover", (2021, None), {}, 3.46, 0.005),
    ("10499", "KRAFTHEINZ_2019_10K", "inventory_turnover", (2019, None), {}, 6.25, 0.005),
    ("03471", "GENERALMILLS_2020_10K", "current_ratio", (2020, None), {}, 0.68, 0.005),
    ("04412", "LOCKHEEDMARTIN_2020_10K", "asset_turnover", (2020, None), {}, 1.33, 0.005),
    (
        "02987",
        "ACTIVISIONBLIZZARD_2019_10K",
        "fixed_asset_turnover",
        (2019, None),
        {},
        24.26,
        0.005,
    ),
    ("03856", "ADOBE_2017_10K", "ocf_ratio", (2017, None), {}, 0.83, 0.005),
    ("10136", "GENERALMILLS_2022_10K", "retention_ratio", (2022, None), {}, 0.54, 0.005),
    ("03620", "PEPSICO_2022_10K", "ebitda", (2022, None), {}, 9068e6 + 5207e6, 0.5e6),
    ("04481", "PEPSICO_2022_10K", "ebitda_margin", (2022, None), {}, 0.165, 0.0005),
    (
        "03718",
        "LOCKHEEDMARTIN_2022_10K",
        "revenue_cagr",
        (2022, None),
        {"since": (2020, None)},
        0.004,
        0.0005,
    ),
    (
        "04103",
        "GENERALMILLS_2019_10K",
        "ccc",
        (2019, None),
        {"denominator": "purchases"},
        -3.7,
        0.05,
    ),
]


@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL")
@pytest.mark.parametrize(
    ("qid", "doc", "name", "period", "opts", "gold", "tol"), GOLD, ids=[g[0] for g in GOLD]
)
def test_metrics_match_financebench_gold(
    qid: str,
    doc: str,
    name: str,
    period: Period,
    opts: dict,
    gold: float,
    tol: float,  # type: ignore[type-arg]
) -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        loaded = conn.execute(
            "SELECT 1 FROM xbrl_facts x JOIN documents d ON d.accession = x.accession"
            " WHERE d.doc_name = %s LIMIT 1",
            (doc,),
        ).fetchone()
        if loaded is None:
            pytest.skip(f"no facts loaded for {doc} (run `frl facts load`)")
        facts = FactLookup(conn, CMAP, doc, read_offsets(ROOT / "corpus" / "companies.csv"))
        v = M.compute(facts, name, period, **opts)
    assert v.value == pytest.approx(gold, abs=tol), v.formula
    assert all(f.as_filed for f in v.facts)
