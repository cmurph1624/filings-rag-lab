import hashlib

import pytest

from filings_rag.edgar.resolve import DocRef, parse_doc_name
from filings_rag.retrieve import scope as S
from filings_rag.retrieve.factory import resolve_spec


@pytest.mark.parametrize(
    ("text", "years", "quarters"),
    [  # phrasings taken from the FinanceBench open subset
        ("based on FY2022 data?", [2022], []),
        ("based on FY 2022 data?", [2022], []),
        ("quick ratio for FY22?", [2022], []),
        ("grow its PPNE between FY20 and FY21?", [2020, 2021], []),
        ("between FY 2023 and Q2 of FY2024?", [2023, 2024], [(2024, 2)]),
        ("lowest net revenue in 2021 Q1?", [2021], [(2021, 1)]),
        ("In 2022 Q2, which of JPM's business segments", [2022], [(2022, 2)]),
        ("short term investments for MGM in H1 FY2023?", [2023], [(2023, 2)]),
        ("biggest drop in Q22023 year over year revenues", [2023], [(2023, 2)]),
        ("As of Q2'2023, is Pfizer spinning off", [2023], [(2023, 2)]),
        ("As of FY2023Q1, why did Pepsico raise", [2023], [(2023, 1)]),
        ("in the second fiscal quarter of 2023 decrease", [2023], [(2023, 2)]),
        ("in Q4 of FY2023?", [2023], [(2023, 4)]),
        ("The FY2015 - FY2017 3 year average", [2015, 2017], []),  # named years, not the range
        ("on May 26, 2023?", [2023], []),
        ("8k filing dated 1st July 2022?", [2022], []),
        ("expect to pay for its retirees in 2024?", [2024], []),
        ("Is Boeing's business subject to cyclicality?", [], []),
        ("revenue of $2,000 million and 20230 units", [], []),
    ],
)
def test_parse_periods(text: str, years: list[int], quarters: list[tuple[int, int]]) -> None:
    assert S.parse_periods(text) == (years, quarters)


COMPANIES = [
    S.Company("JPMorgan", "JPM", ("J.P. Morgan",)),
    S.Company("Johnson & Johnson", "JNJ", ("JnJ",)),
    S.Company("Coca-Cola", "KO", ("Coca Cola",)),
    S.Company("Boeing", "BA", ()),
    S.Company("Block", "XYZ", ("Square",)),
]
PATTERNS = S.company_patterns(COMPANIES)


@pytest.mark.parametrize(
    ("text", "company"),
    [
        ("Are JPM's gross margins historically consistent?", "JPMorgan"),
        ("Is growth in JnJ's adjusted EPS expected", "Johnson & Johnson"),
        ("the FY2017 return on assets for Coca Cola?", "Coca-Cola"),
        ("what is Boeing's year end FY2018 net PP&E", "Boeing"),
        ("Block (formerly known as Square)", "Block"),
        ("a basket of goods", None),  # tickers only match in capitals ("ba" in "basket")
        ("What drove the reduction in SG&A expense", None),
    ],
)
def test_find_company(text: str, company: str | None) -> None:
    assert S.find_company(text, PATTERNS) == company


def test_doc_names_with_a_period_end_suffix_parse() -> None:
    assert parse_doc_name("ULTABEAUTY_2023_10K_2024-02-03") == DocRef("10K", 2023, "FY", None)


def _labels() -> list[S.DocLabel]:
    names = ["BA_2021_10K", "BA_2022_10K", "BA_2023_10K", "BA_2023Q2_10Q", "BA_2022Q4_EARNINGS"]
    return [S.DocLabel.from_doc_name(i, n, "Boeing") for i, n in enumerate(names)] + [
        S.DocLabel.from_doc_name(9, "KO_2022_10K", "Coca-Cola")
    ]


def _names(docs: list[S.DocLabel]) -> list[str]:
    return [d.doc_name for d in docs]


def test_select_matches_years_quarters_and_lookahead() -> None:
    labels = _labels()
    docs, level = S.select(labels, S.Mentions("Boeing", [2022]), 0)
    assert level == "filings" and _names(docs) == ["BA_2022_10K", "BA_2022Q4_EARNINGS"]
    docs, _ = S.select(labels, S.Mentions("Boeing", [2021]), 1)
    assert _names(docs) == ["BA_2021_10K", "BA_2022_10K", "BA_2022Q4_EARNINGS"]
    docs, _ = S.select(labels, S.Mentions("Boeing", [2023], [(2023, 2)]), 0)
    assert _names(docs) == ["BA_2023_10K", "BA_2023Q2_10Q"]
    docs, _ = S.select(labels, S.Mentions("Boeing", [2022], [(2022, 2)]), 0)
    assert "BA_2023Q2_10Q" not in _names(docs)  # quarters match on (year, quarter) only


def test_select_falls_back_to_company_then_corpus() -> None:
    labels = _labels()
    docs, level = S.select(labels, S.Mentions("Boeing", [2015]), 0)
    assert level == "company" and len(docs) == 5
    docs, level = S.select(labels, S.Mentions("Boeing"), 0)
    assert level == "company"
    docs, level = S.select(labels, S.Mentions(None, [2022]), 0)
    assert level == "corpus" and docs == labels


def test_parse_llm_mentions() -> None:
    names = [c.name for c in COMPANIES]
    reply = (
        'Sure:\n```json\n{"company": "boeing", "years": [2022, "2021"],'
        ' "quarters": [[2023, 2]]}\n```'
    )
    assert S.parse_llm_mentions(reply, names) == S.Mentions("Boeing", [2021, 2022], [(2023, 2)])
    assert S.parse_llm_mentions('{"company": "Airbus", "years": []}', names).company is None
    with pytest.raises(ValueError, match="no JSON"):
        S.parse_llm_mentions("I can't tell.", names)


def test_resolve_scope_defaults_and_hash_neutral_none() -> None:
    base = {"kind": "vector", "k": 10}
    assert resolve_spec({**base, "scope": {"mode": "none"}}) == resolve_spec(base)
    assert resolve_spec({**base, "scope": {"mode": "rules"}})["scope"] == {
        "mode": "rules",
        "lookahead": 0,
    }
    llm = resolve_spec({**base, "scope": {"mode": "llm", "lookahead": 1}})["scope"]
    assert llm["model"] == "claude-haiku-5-5" and llm["prompt"] == "scope@1"
    with pytest.raises(ValueError, match="unknown scope mode"):
        resolve_spec({**base, "scope": {"mode": "magic"}})
    with pytest.raises(ValueError, match="options"):
        resolve_spec({**base, "scope": {"mode": "oracle", "lookahead": 1}})
    with pytest.raises(ValueError, match="unknown scope prompt"):
        resolve_spec({**base, "scope": {"mode": "llm", "prompt": "scope@9"}})


def test_scope_prompts_are_not_edited_in_place() -> None:
    p = S.get_scope_prompt("scope@1")
    digest = hashlib.sha256(f"{p.system}\0{p.template}".encode()).hexdigest()[:16]
    assert digest == "8921b9c7e76fb10c"


def test_a_figure_that_looks_like_a_year_only_widens_the_years() -> None:
    assert S.parse_periods("FY2022 guidance of 2024 stores") == ([2022, 2024], [])


def test_malformed_llm_fields_are_dropped_not_fatal() -> None:
    reply = '{"company": 7, "years": [2022, "soon"], "quarters": [[2023], [2023, 5], [2023, 2]]}'
    assert S.parse_llm_mentions(reply, ["Boeing"]) == S.Mentions(None, [2022], [(2023, 2)])


def test_scalar_years_and_quarters_are_ignored() -> None:
    reply = '{"company": "Boeing", "years": 2023, "quarters": 3}'
    assert S.parse_llm_mentions(reply, ["Boeing"]) == S.Mentions("Boeing", [], [])
    assert S.parse_llm_mentions('{"company": "Boeing", "years": "2023"}', ["Boeing"]).years == []


@pytest.mark.parametrize(
    ("text", "forward"),
    [
        ("What production rate changes is Boeing forecasting for FY2023?", True),
        ("Is growth in JnJ's adjusted EPS expected to accelerate in FY2023?", True),
        ("Why did Pepsico raise full year guidance for FY2023?", True),
        ("What was the outlook for 2024?", True),
        ("What was Boeing's FY2022 revenue?", False),
        ("Did Pfizer grow its PPNE between FY20 and FY21?", False),
    ],
)
def test_is_forward_looking(text: str, forward: bool) -> None:
    assert S.is_forward_looking(text) is forward


def test_select_lookback_adds_prior_annual_filings_only() -> None:
    labels = _labels()
    docs, level = S.select(labels, S.Mentions("Boeing", [2023]), 0, lookback=1)
    assert level == "filings"
    assert _names(docs) == ["BA_2022_10K", "BA_2023_10K", "BA_2022Q4_EARNINGS"]


def test_select_no_year_latest_takes_the_latest_labelled_year() -> None:
    labels = _labels()
    docs, level = S.select(labels, S.Mentions("Boeing"), 0, no_year="latest")
    assert level == "latest" and _names(docs) == ["BA_2023_10K", "BA_2023Q2_10Q"]
    docs, level = S.select(labels, S.Mentions("Boeing", [2015]), 0, no_year="latest")
    assert level == "company"  # a named period that matches nothing still falls back


def test_label_offset_shifts_a_doc_label() -> None:
    d = S.DocLabel.from_doc_name(1, "ULTABEAUTY_2023_10K_2024-02-03", "Ulta Beauty", 1)
    assert (d.year, d.period) == (2024, "FY")


def test_period_options_are_hash_neutral_at_their_off_values() -> None:
    base = {"kind": "vector", "k": 10}
    plain = resolve_spec({**base, "scope": {"mode": "rules"}})
    off = {"forecast_lookback": 0, "no_year": "company", "fiscal_labels": "doc_name"}
    assert resolve_spec({**base, "scope": {"mode": "rules", **off}}) == plain
    on = resolve_spec(
        {**base, "scope": {"mode": "rules", "forecast_lookback": 1, "no_year": "latest"}}
    )["scope"]
    assert on == {"mode": "rules", "lookahead": 0, "forecast_lookback": 1, "no_year": "latest"}
    with pytest.raises(ValueError, match="no_year"):
        resolve_spec({**base, "scope": {"mode": "rules", "no_year": "all"}})
    with pytest.raises(ValueError, match="options"):
        resolve_spec({**base, "scope": {"mode": "oracle", "no_year": "latest"}})
    with pytest.raises(ValueError, match="forecast_lookback"):
        resolve_spec({**base, "scope": {"mode": "rules", "forecast_lookback": -1}})
