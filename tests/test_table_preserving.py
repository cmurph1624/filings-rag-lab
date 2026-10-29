from filings_rag.ingest.base import Doc
from filings_rag.ingest.registry import get_pipeline
from filings_rag.ingest.table_preserving import TablePreserving, _lines

PROSE = " ".join(f"word{i}" for i in range(400)) + "."
BALANCE = """3M Company and Subsidiaries
Consolidated Balance Sheet
(Millions) | 2018 | 2017
Assets
Current assets
Cash and cash equivalents | $ | 2,853 | $ | 3,053
Accounts receivable | 5,020 | 4,911
Adjustments to reconcile net income including noncontrolling interest to net cash provided
Total current assets | 14,277 | 14,393
Total assets | $ | 36,500 | $ | 37,987
See accompanying notes.
60"""
INCOME = """Consolidated Statement of Income
(Millions) | 2018 | 2017
Net sales | 32,765 | 31,657
Operating income | 7,207 | 7,692
Net income | 5,363 | 4,869"""


def _doc(body: str) -> Doc:
    return Doc(1, body, [0])


def test_registered_version_params_are_pinned() -> None:
    assert get_pipeline("table-preserving@1").params == {
        "window_words": 350, "overlap_words": 50, "max_table_words": 700, "min_rows": 3,
        "max_gap": 3, "short_line_words": 12, "label_words": 25, "caption_lines": 3,
        "min_prose_words": 25,
    }  # fmt: skip


def test_a_table_is_one_chunk_with_its_caption_labels_and_footer() -> None:
    # The footer ("See accompanying notes.", page number) is a tiny stretch before the next
    # table, so it stays with the balance sheet; the next table takes its own caption.
    body = PROSE + "\n" + BALANCE + "\n" + INCOME + "\n" + PROSE
    chunks = TablePreserving().chunk(_doc(body))
    tables = [c for c in chunks if "Total assets" in c.text]
    assert len(tables) == 1
    t = tables[0]
    assert t.text.startswith("3M Company and Subsidiaries\nConsolidated Balance Sheet")
    assert "Adjustments to reconcile" in t.text  # a long label line doesn't end the table
    assert t.text.endswith("See accompanying notes.\n60")
    assert body[t.char_start : t.char_end] == t.text
    assert all("Total assets" not in c.text for c in chunks if c is not t)
    income = [c for c in chunks if "Net sales" in c.text]
    assert len(income) == 1 and income[0].text.startswith("Consolidated Statement of Income")
    assert [c.ord for c in chunks] == list(range(len(chunks)))


def test_prose_is_windowed_like_naive() -> None:
    chunks = TablePreserving(window_words=100, overlap_words=20).chunk(_doc(PROSE))
    assert [len(c.text.split()) for c in chunks] == [100, 100, 100, 100, 80]
    assert chunks[1].text.split()[0] == "word80"


def test_two_rows_are_not_a_table_and_a_sentence_ends_one() -> None:
    body = PROSE + "\nA | B\nC | D\n" + PROSE
    segs = TablePreserving().segments(_lines(body))
    assert [s.kind for s in segs] == ["prose"]
    sentence = " ".join(["This sentence"] * 15) + " ends like prose."  # 33 words, not a label
    stops = f"Row a | 1\nRow b | 2\n{sentence}\nRow c | 3"
    segs = TablePreserving(min_rows=2).segments(_lines(stops))
    assert [s.kind for s in segs] == ["table", "prose"]


def test_a_long_table_splits_by_rows_repeating_caption_and_header() -> None:
    rows = "\n".join(f"Line item {i} | {i},000 | {i},500" for i in range(60))
    body = "Segment Revenue\n(Millions) | 2022 | 2021\n" + rows
    chunks = TablePreserving(max_table_words=120).chunk(_doc(body))
    assert len(chunks) > 1
    assert chunks[0].text.startswith("Segment Revenue\n(Millions)")
    for c in chunks[1:]:
        assert c.text.startswith("Segment Revenue\n(Millions) | 2022 | 2021\nLine item")
        assert len(c.text.split()) <= 120 + 10
    items = [ln for c in chunks for ln in c.text.split("\n") if ln.startswith("Line item")]
    assert items == [f"Line item {i} | {i},000 | {i},500" for i in range(60)]  # each row once


def test_a_fourth_label_line_in_a_row_ends_the_table() -> None:
    rows = "Row a | 1\nRow b | 2\nRow c | 3\n"
    # Labels long enough that the stretch after the table isn't folded back in as a footer.
    labels = [f"Section label number {n} of this statement" for n in range(4)]  # 7 words each
    three = rows + "\n".join(labels[:3]) + "\nRow d | 4"
    four = rows + "\n".join(labels) + "\nRow d | 4"
    assert [(s.kind, s.last) for s in TablePreserving().segments(_lines(three))] == [("table", 6)]
    segs = TablePreserving().segments(_lines(four))
    assert [(s.kind, s.first, s.last) for s in segs] == [("table", 0, 2), ("prose", 3, 7)]


def test_a_footer_after_a_long_table_lands_in_its_last_group() -> None:
    rows = "\n".join(f"Line item {i} | {i},000 | {i},500" for i in range(60))
    body = "Segment Revenue\n(Millions) | 2022 | 2021\n" + rows + "\nSee accompanying notes.\n7"
    chunks = TablePreserving(max_table_words=120).chunk(_doc(body))
    assert chunks[-1].text.endswith("Line item 59 | 59,000 | 59,500\nSee accompanying notes.\n7")
    assert sum("See accompanying notes." in c.text for c in chunks) == 1
