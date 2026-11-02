from datetime import date

import pytest

from filings_rag.eval.periods import Expected, classify, expected_for, mentions, states


def fy(year: int, end: date | None, offset: int = 0) -> Expected:
    return expected_for((year, None), offset, {(year, None): end} if end else {}, None)


def kinds(text: str) -> list[tuple[str, int, int | None]]:
    return [(m.kind, m.year, m.quarter) for m in mentions(text)]


def test_mentions_reads_each_form_once() -> None:
    assert kinds("In Q2 FY2023 revenue rose") == [("quarter", 2023, 2)]
    assert kinds("second quarter of fiscal 2023") == [("quarter", 2023, 2)]
    assert kinds("2023 Q1 and FY22") == [("quarter", 2023, 1), ("fiscal", 2022, None)]
    assert kinds("year ended January 28, 2023") == [("date", 2023, None)]
    assert kinds("as of 2023-06-30") == [("date", 2023, None)]
    assert kinds("FY2019 – FY2021 CAGR") == [("fiscal", y, None) for y in (2019, 2020, 2021)]
    assert kinds("in 2022, up from 2021") == [("bare", 2022, None), ("bare", 2021, None)]


def test_numbers_that_look_like_years_are_not_periods() -> None:
    assert mentions("$2,024 million, 2,019 units, $2019M, 20.21%, 2022.5 and 2023%") == []


def test_label_year_company_year_and_period_end_all_state_the_period() -> None:
    ulta = fy(2023, date(2023, 1, 28), offset=1)  # Ulta calls FinanceBench's FY2023 fiscal 2022
    for text in ("FY2023", "fiscal 2022", "the year ended January 28, 2023", "in 2023"):
        assert classify(text, ulta) == "right", text
    assert classify("FY2021 revenue", ulta) == "wrong"
    assert classify("Revenue was $10B.", ulta) == "none"


def test_a_date_inside_the_fiscal_year_states_it() -> None:
    pep = fy(2023, date(2023, 12, 30))
    assert classify("at the May 3, 2023 annual meeting", pep) == "right"
    assert classify("as of December 31, 2022", pep) == "wrong"  # the prior year's end
    assert classify("as of January 3, 2024", pep) == "right"  # within the slack of the end


def test_quarters_need_the_right_quarter_or_the_year() -> None:
    q2 = expected_for((2023, 2), 0, {(2023, 2): date(2023, 6, 30)}, None)
    assert classify("in Q2 2023", q2) == "right"
    assert classify("in the three months ended June 30, 2023", q2) == "right"
    assert classify("FY2023 guidance", q2) == "right"
    assert classify("in Q1 2023", q2) == "wrong"
    assert classify("as of March 31, 2023", q2) == "wrong"  # end of Q1


def test_without_a_period_end_only_years_count() -> None:
    e = fy(2022, None)
    assert e.end is None and classify("on May 3, 2022", e) == "right"
    assert classify("in 2023", e) == "wrong"


@pytest.mark.parametrize("text", ["FY2022", "2022", "December 31, 2022"])
def test_states_matches_a_calendar_year(text: str) -> None:
    e = fy(2022, date(2022, 12, 31))
    assert any(states(m, e) for m in mentions(text))
