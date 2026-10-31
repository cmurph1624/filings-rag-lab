import os
from datetime import date
from pathlib import Path

import psycopg
import pytest

from filings_rag.facts import concepts, coverage
from filings_rag.facts import load as facts_load

CONCEPTS = Path(__file__).resolve().parents[1] / "config" / "concepts.toml"


def test_committed_concept_map_resolves_every_route_metric() -> None:
    cmap = concepts.load(CONCEPTS)
    assert cmap.version == "concepts@1"
    assert cmap.inputs("inventory") == ("inventory",)
    assert cmap.inputs("dpo") == ("accounts_payable", "cogs", "inventory")
    with pytest.raises(KeyError):
        cmap.inputs("ebit")


def test_concept_map_validation(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text('version = "x"\n[items.a]\nkind = "flow"\nconcepts = ["A"]\n')
    with pytest.raises(ValueError, match="kind"):
        concepts.load(p)
    p.write_text('[items.a]\nkind = "instant"\nconcepts = ["A"]\n[metrics]\nm = ["b"]\n')
    with pytest.raises(ValueError, match="unknown items"):
        concepts.load(p)
    p.write_text('[items.a]\nkind = "instant"\nconcepts = ["A"]\n')
    with pytest.raises(ValueError, match="route-label metrics"):
        concepts.load(p)


def companyfacts() -> dict:  # type: ignore[type-arg]
    fact = {"end": "2019-02-02", "val": 5409000000, "accn": "A-19", "fy": 2019, "fp": "FY",
            "form": "10-K", "filed": "2019-03-28"}  # fmt: skip
    return {
        "facts": {
            "us-gaap": {
                "InventoryNet": {"units": {"USD": [fact, {**fact, "frame": "CY2018Q4I"}]}},
            },
            "ifrs-full": {"Inventories": {"units": {"USD": [fact]}}},
        }
    }


def test_fact_rows_keep_known_taxonomies_and_drop_repeats() -> None:
    rows = list(facts_load.fact_rows(764478, companyfacts()))
    assert len(rows) == 1
    assert rows[0][:8] == (764478, "A-19", "us-gaap", "InventoryNet", "USD", 5409000000, None,
                           "2019-02-02")  # fmt: skip


def test_fact_matches_instants_years_and_quarters() -> None:
    end = date(2023, 1, 28)
    assert coverage.fact_matches("instant", None, None, date(2023, 1, 30), end)
    assert not coverage.fact_matches("instant", None, date(2022, 1, 30), end, end)
    assert coverage.fact_matches("duration", None, date(2022, 1, 30), end, end)
    assert not coverage.fact_matches("duration", None, date(2022, 7, 31), end, end)  # half year
    assert coverage.fact_matches("duration", 2, date(2022, 10, 30), end, end)
    assert not coverage.fact_matches("instant", None, None, date(2022, 1, 29), end)


def test_period_end_dates_apply_the_offset_to_distractors_only() -> None:
    docs = [
        ("ULTABEAUTY_2023_10K", date(2023, 1, 28), True),
        ("ULTABEAUTY_2021_10K", date(2022, 1, 29), False),
        ("BESTBUY_2024Q2_10Q", date(2023, 7, 29), True),
    ]
    ends = coverage.period_end_dates(docs, 1)
    assert ends[(2023, None)] == date(2023, 1, 28)
    assert ends[(2022, None)] == date(2022, 1, 29)  # Ulta's fiscal 2021 is FinanceBench FY2022
    assert ends[(2024, 2)] == date(2023, 7, 29)


def test_target_end_falls_back_to_shifting_the_gold_period() -> None:
    gold = ((2017, None), date(2018, 1, 28))
    assert coverage.target_end((2015, None), {}, gold) == date(2016, 1, 28)
    assert coverage.target_end((2017, 2), {}, gold) is None  # a quarter from an annual filing
    assert coverage.parse_period("Q2 FY2023") == (2023, 2)


@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL")
def test_load_company_replaces_a_companys_rows() -> None:
    cik = 999_999_901  # zz fixture: no real CIK
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        try:
            assert facts_load.load_company(conn, cik, companyfacts()).rows == 1
            s = facts_load.load_company(conn, cik, companyfacts())
            assert (s.rows, s.duplicates) == (1, 1)
            n = conn.execute("SELECT count(*) FROM xbrl_facts WHERE cik = %s", (cik,)).fetchone()
            assert n == (1,)
        finally:
            conn.execute("DELETE FROM xbrl_facts WHERE cik = %s", (cik,))
            conn.commit()
