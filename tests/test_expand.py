from typing import Any

from filings_rag.edgar import expand
from filings_rag.edgar.resolve import ManifestRow


def _f(accn: str, form: str, report: str, filed: str = "") -> dict[str, Any]:
    return {
        "accessionNumber": accn,
        "form": form,
        "reportDate": report,
        "filingDate": filed or report,
        "primaryDocument": f"{accn}.htm",
    }


# A retailer whose year ends around 1 February and is named for the calendar year it ends in
# (Best Buy). XBRL calls the 2021 filing 2020; the period end says 2021.
RETAILER = [
    _f("k19", "10-K", "2019-02-02"),
    _f("k20", "10-K", "2020-02-01"),
    _f("k21", "10-K", "2021-01-30"),
    _f("k21a", "10-K/A", "2021-01-30"),  # amendments are never distractors
    _f("q1", "10-Q", "2021-05-01"),
    _f("q2", "10-Q", "2021-07-31"),
    _f("q3", "10-Q", "2021-10-30"),
    _f("k22", "10-K", "2022-01-29"),
]
RETAILER_XBRL = {
    "k19": ("10-K", 2019, "FY"),
    "k20": ("10-K", 2020, "FY"),
    "k21": ("10-K", 2020, "FY"),  # wrong in the filing itself
}


def test_fiscal_labels_use_period_ends_not_per_filing_xbrl() -> None:
    labels = expand.fiscal_labels(RETAILER, RETAILER_XBRL)
    assert labels["k21"] == ("10-K", 2021, "FY")
    assert labels["k22"] == ("10-K", 2022, "FY")  # no XBRL needed once the offset is known
    assert "k21a" not in labels
    # The quarters after the fiscal-2021 10-K belong to fiscal 2022.
    assert [labels[q] for q in ("q1", "q2", "q3")] == [
        ("10-Q", 2022, "Q1"),
        ("10-Q", 2022, "Q2"),
        ("10-Q", 2022, "Q3"),
    ]


def test_fiscal_labels_learn_a_year_before_offset() -> None:
    # Foot Locker and Ulta name a year ending in January for the calendar year before.
    filings = [_f("a", "10-K", "2020-02-01"), _f("b", "10-K", "2021-01-30")]
    xbrl = {"a": ("10-K", 2019, "FY"), "b": ("10-K", 2020, "FY")}
    assert expand.fiscal_labels(filings, xbrl)["b"] == ("10-K", 2020, "FY")


def test_fiscal_labels_count_a_first_of_january_year_end_as_the_year_before() -> None:
    # J&J's fiscal 2022 ended on 2023-01-01; with no XBRL the offset defaults to 0.
    assert expand.fiscal_labels([_f("jnj", "10-K", "2023-01-01")], {})["jnj"] == (
        "10-K",
        2022,
        "FY",
    )


def test_fiscal_labels_skip_a_quarter_with_no_earlier_10k() -> None:
    assert expand.fiscal_labels([_f("q", "10-Q", "2021-03-31")], {}) == {}


def _distractors(gold: set[str], taken: set[str], quarterly: bool = True) -> Any:
    keys = expand.wanted(expand.Window(range(2019, 2023), range(2022, 2023)), quarterly)
    return expand.distractors("Retailer", 7, "RETAILER", keys, RETAILER, RETAILER_XBRL, gold, taken)


def test_distractors_skip_gold_slots_and_report_gaps() -> None:
    rows, notes = _distractors(gold={"k20"}, taken=set())
    assert [r.doc_name for r in rows] == [
        "RETAILER_2019_10K",
        "RETAILER_2021_10K",
        "RETAILER_2022_10K",
        "RETAILER_2022Q1_10Q",
        "RETAILER_2022Q2_10Q",
        "RETAILER_2022Q3_10Q",
    ]
    assert notes == []
    r = rows[1]
    assert (r.accession, r.cik, r.form, r.fiscal_year, r.status) == (
        "k21",
        7,
        "10-K",
        2021,
        "resolved",
    )
    assert r.report_date == "2021-01-30" and r.primary_document == "k21.htm"
    assert r.note == "distractor 10-K FY2021"

    rows, notes = _distractors(gold=set(), taken=set(), quarterly=False)
    assert len(rows) == 4 and all(r.form == "10-K" for r in rows)


def test_distractors_note_missing_years() -> None:
    keys = expand.wanted(expand.Window(range(2018, 2020), range(0)), False)
    rows, notes = expand.distractors("R", 7, "R", keys, RETAILER, RETAILER_XBRL, set(), set())
    assert [r.doc_name for r in rows] == ["R_2019_10K"]
    assert notes == ["R: no 10-K FY2018"]


def test_distractor_name_taken_by_a_gold_label_gets_the_period_end() -> None:
    taken = {"RETAILER_2021_10K"}
    rows, _ = _distractors(gold=set(), taken=taken, quarterly=False)
    renamed = [r for r in rows if r.accession == "k21"][0]
    assert renamed.doc_name == "RETAILER_2021_10K_2021-01-30"
    assert "period end appended" in renamed.note
    assert renamed.doc_name in taken


def _gold(doc_name: str, company: str, form: str) -> ManifestRow:
    return ManifestRow(doc_name, company, 1, form, 2022, "FY", "", "", "", "", "", "edgar", "", "")


def test_prefixes_and_quarterly_companies_come_from_the_gold_manifest() -> None:
    gold = [
        _gold("Pfizer_2023Q2_10Q", "Pfizer", "10-Q"),
        _gold("JOHNSON_JOHNSON_2022_10K", "Johnson & Johnson", "10-K"),
        _gold("AMCOR_2022_8K_dated-2022-07-01", "Amcor", "8-K"),
    ]
    assert expand.company_prefixes(gold) == {
        "Pfizer": "Pfizer",
        "Johnson & Johnson": "JOHNSON_JOHNSON",
        "Amcor": "AMCOR",
    }
    assert expand.quarterly_companies(gold) == {"Pfizer"}
