from typing import Any

import pytest

from filings_rag.edgar import resolve
from filings_rag.edgar.resolve import DocRef, parse_doc_name


@pytest.mark.parametrize(
    ("name", "ref"),
    [
        ("3M_2018_10K", DocRef("10K", 2018, "FY", None)),
        ("AMCOR_2023Q2_10Q", DocRef("10Q", 2023, "Q2", None)),
        ("Pfizer_2023Q2_10Q", DocRef("10Q", 2023, "Q2", None)),
        ("JOHNSON_JOHNSON_2022Q4_EARNINGS", DocRef("EARNINGS", 2022, "Q4", None)),
        ("AMCOR_2022_8K_dated-2022-07-01", DocRef("8K", 2022, "FY", "2022-07-01")),
        ("FOOTLOCKER_2022_8K_dated_2022-08-19", DocRef("8K", 2022, "FY", "2022-08-19")),
    ],
)
def test_parse_doc_name(name: str, ref: DocRef) -> None:
    assert parse_doc_name(name) == ref


def test_parse_doc_name_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_doc_name("3M_annual_report")


def test_link_accession() -> None:
    link = "https://x/content/0001558370-19-000470/0001558370-19-000470.pdf"
    assert resolve.link_accession(link) == "0001558370-19-000470"
    assert resolve.link_accession("https://cdn/CIK-0000874761/3be55db1.pdf") is None
    assert resolve.link_accession(None) is None


def _fact(accn: str, form: str, fy: int, fp: str) -> dict[str, Any]:
    return {"accn": accn, "form": form, "fy": fy, "fp": fp}


FACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        _fact("A-10K-22", "10-K", 2022, "FY"),
                        _fact("A-10K-22", "10-K", 2022, "FY"),
                        _fact("A-10K-22", "10-K", 2021, "FY"),  # outvoted
                        _fact("A-10Q-23Q2", "10-Q", 2023, "Q2"),
                        _fact("A-10KA", "10-K/A", 2022, "FY"),  # amendments ignored
                    ]
                }
            }
        }
    }
}


def _filing(accn: str, form: str, date: str = "2023-01-01", items: str = "") -> dict[str, Any]:
    return {
        "accessionNumber": accn,
        "form": form,
        "filingDate": date,
        "reportDate": date,
        "primaryDocument": f"{accn}.htm",
        "items": items,
    }


class FakeSource:
    def __init__(self) -> None:
        self.filings = [
            _filing("A-10K-22", "10-K"),
            _filing("A-10Q-23Q2", "10-Q"),
            _filing("A-10KA", "10-K/A"),
            _filing("A-8K-1", "8-K", "2022-07-01"),
            _filing("A-8K-2", "8-K", "2022-08-19"),
            _filing("A-8K-3", "8-K", "2022-08-19"),
            _filing("0000000001-23-000001", "10-K"),
        ]

    def submissions(self, cik: int) -> list[dict[str, Any]]:
        return self.filings

    def companyfacts(self, cik: int) -> Any:
        return FACTS


def _doc(name: str, link: str = "") -> dict[str, Any]:
    return {"doc_name": name, "company": "Acme", "doc_link": link}


def test_resolve_10k_by_fiscal_year() -> None:
    row = resolve.resolve(_doc("ACME_2022_10K"), 1, FakeSource(), {})
    assert (row.status, row.accession, row.source) == ("resolved", "A-10K-22", "edgar")
    assert row.primary_document == "A-10K-22.htm"


def test_resolve_10q_by_fiscal_quarter() -> None:
    row = resolve.resolve(_doc("ACME_2023Q2_10Q"), 1, FakeSource(), {})
    assert row.accession == "A-10Q-23Q2"


def test_resolve_8k_by_date_and_ambiguity() -> None:
    src = FakeSource()
    assert resolve.resolve(_doc("ACME_2022_8K_dated-2022-07-01"), 1, src, {}).accession == "A-8K-1"
    row = resolve.resolve(_doc("ACME_2022_8K_dated-2022-08-19"), 1, src, {})
    assert row.status == "ambiguous" and row.accession == ""


def test_override_wins() -> None:
    overrides = {"ACME_2022_8K_dated-2022-08-19": "A-8K-3"}
    row = resolve.resolve(_doc("ACME_2022_8K_dated-2022-08-19"), 1, FakeSource(), overrides)
    assert (row.status, row.accession, row.note) == ("resolved", "A-8K-3", "override")


def test_link_accession_beats_fiscal_match() -> None:
    link = "https://ir/content/0000000001-23-000001/x.pdf"
    row = resolve.resolve(_doc("ACME_2022_10K", link), 1, FakeSource(), {})
    assert row.accession == "0000000001-23-000001"


def test_earnings_without_override_falls_back() -> None:
    row = resolve.resolve(_doc("ACME_2022Q4_EARNINGS"), 1, FakeSource(), {})
    assert (row.status, row.source) == ("fallback", "fb_pdf")


def test_unknown_company_and_missing_filing() -> None:
    assert resolve.resolve(_doc("ACME_2022_10K"), None, FakeSource(), {}).status == "unresolved"
    row = resolve.resolve(_doc("ACME_2019_10K"), 1, FakeSource(), {})
    assert row.status == "unresolved"
