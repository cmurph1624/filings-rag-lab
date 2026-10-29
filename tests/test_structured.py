from filings_rag.eval.report import citation_counts
from filings_rag.harness.prompts import citations, get_prompt
from filings_rag.ingest.base import Doc
from filings_rag.ingest.registry import get_pipeline
from filings_rag.ingest.structured import Structured, period_label
from filings_rag.ingest.table_preserving import TablePreserving
from filings_rag.retrieve.base import Hit

PROSE = " ".join(f"word{i}" for i in range(60)) + "."
BODY = "\n".join(
    [
        "Table of Contents",
        "Item 7. | Management's Discussion and Analysis. | 25",  # TOC rows never count
        "Item 8. | Financial Statements and Supplementary Data. | 47",
        "Item 9. | Changes in Accountants. | 90",
        "Item 7. Management's Discussion and Analysis of Financial Condition.",
        "Liquidity and Capital Resources",
        PROSE,
        "Item 8. Financial Statements and Supplementary Data.",
        "Table of Contents",
        "Consolidated Balance Sheets",
        "$ in millions",
        "(Millions) | 2019 | 2018",
        "Cash | 1,980 | 1,101",
        "Inventories | 5,409 | 5,209",
        "Total assets | 12,901 | 13,049",
        "March 28, 2019",
        PROSE,
        "[EX-99.1 press.htm]",
        PROSE,
    ]
)
META = {"doc_name": "BESTBUY_2019_10K", "company": "Best Buy"}


def _chunks() -> list:  # type: ignore[type-arg]
    return Structured().chunk(Doc(1, BODY, [0], META))


def test_params_extend_table_preserving_and_are_pinned() -> None:
    p = get_pipeline("structured@1").params
    assert p == TablePreserving().params | {
        "header": "company · form · period · item · heading",
        "max_item_words": 25,
        "max_heading_words": 10,
        "max_title_chars": 80,
    }


def test_chunks_are_table_preserving_plus_a_header_and_exhibit_breaks() -> None:
    body = BODY.split("\n[EX-99.1")[0]  # without an exhibit, the chunks are table-preserving's
    base = TablePreserving().chunk(Doc(1, body, [0]))
    ours = Structured().chunk(Doc(1, body, [0], META))
    assert [(c.char_start, c.char_end) for c in ours] == [(c.char_start, c.char_end) for c in base]
    for b, c in zip(base, ours, strict=True):
        assert c.text == c.text.split("\n", 1)[0] + "\n" + b.text
        assert body[c.char_start : c.char_end] == b.text
    exhibit_starts = [c for c in _chunks() if c.text.split("\n")[1].startswith("[EX-99.1")]
    assert len(exhibit_starts) == 1  # the exhibit begins its own chunk


def test_headers_and_sections() -> None:
    by_text = {c.text.split("\n", 1)[1].split("\n")[0]: c for c in _chunks()}
    mdna = next(c for c in _chunks() if "word0" in c.text and "Liquidity" in c.text)
    assert mdna.text.split("\n")[0] == (
        "Best Buy · 10-K · FY2019 · Item 7. Management's Discussion and Analysis of Financial "
        "Condition · Liquidity and Capital Resources"
    )
    table = next(c for c in _chunks() if "Total assets" in c.text)
    assert (
        table.section
        == "Item 8. Financial Statements and Supplementary Data › Consolidated Balance Sheets"
    )
    assert table.text.startswith(
        "Best Buy · 10-K · FY2019 · Item 8. Financial Statements and Supplementary Data · "
        "Consolidated Balance Sheets\n"
    )
    exhibit = [c for c in _chunks() if c.section and c.section.startswith("EX-99.1")]
    assert exhibit and exhibit[-1].text.startswith("Best Buy · 10-K · FY2019 · EX-99.1")
    assert by_text  # every chunk has a header line


def test_period_labels() -> None:
    assert period_label("BESTBUY_2024Q2_10Q") == ("10-Q", "FY2024 Q2")
    assert period_label("PEPSICO_2023_8K_dated-2023-05-30") == ("8-K", "2023-05-30")
    assert period_label("ULTABEAUTY_2023Q4_EARNINGS") == ("earnings release", "FY2023 Q4")
    assert period_label("ULTABEAUTY_2023_10K_2024-02-03") == ("10-K", "FY2023")


def test_answer_v2_shows_sections_and_v1_is_unchanged() -> None:
    hits = [Hit(7, "BESTBUY_2019_10K", 50, 0.9, "Cash | 1,980", "Item 8 › Balance Sheets")]
    v2 = get_prompt("answer@2").render("Cash?", hits)
    assert "[1] BESTBUY_2019_10K · Item 8 › Balance Sheets, page 50\nCash | 1,980" in v2
    v1 = get_prompt("answer@1").render("Cash?", hits)
    assert "[1] BESTBUY_2019_10K, page 50\nCash | 1,980" in v1
    plain = get_prompt("answer@2").render("Cash?", [Hit(7, "D", 1, 0.9, "t")])
    assert "[1] D, page 1\nt" in plain


def test_citations_parse_forms_and_drop_out_of_range() -> None:
    assert citations("Cash was $1,980M [2]. Inventories [1][4] and [2, 3].", 4) == [2, 1, 4, 3]
    assert citations("See [12] and [0].", 10) == []
    assert citations("No citations here.", 10) == []


def test_citation_counts() -> None:
    hits = [{"chunk_id": 10}, {"chunk_id": 11}, {"chunk_id": 12}]
    answers = [
        ("q1", "A [1][3].", hits),  # 10 labelled, 12 not
        ("q2", "B [2].", hits),  # unlabelled question: counts as citing only
        ("q3", "I cannot answer from the excerpts.", hits),
    ]
    assert citation_counts(answers, {"q1": {10}, "q3": {11}}) == (2, 2, 1)
