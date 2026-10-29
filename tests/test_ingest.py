import pytest

from filings_rag.ingest.base import Doc, chunk_table, page_of
from filings_rag.ingest.naive_fixed import NaiveFixed
from filings_rag.ingest.registry import get_pipeline


def test_page_of() -> None:
    offsets = [0, 100, 250]
    assert [page_of(o, offsets) for o in (0, 99, 100, 249, 250, 10_000)] == [1, 1, 2, 2, 3, 3]
    assert page_of(5, [0]) == 1


def test_chunk_table_name_is_safe() -> None:
    assert chunk_table("naive-fixed", 1) == "chunks__naive_fixed__v1"
    assert chunk_table("a b;drop", 2) == "chunks__a_b_drop__v2"


def test_naive_fixed_windows_overlap_and_offsets() -> None:
    body = " ".join(f"w{i}" for i in range(23))  # 23 words
    doc = Doc(1, body, [0, body.index("w12")])
    chunks = NaiveFixed(window_words=10, overlap_words=3).chunk(doc)
    words = [c.text.split() for c in chunks]
    assert words[0] == [f"w{i}" for i in range(10)]
    assert words[1][0] == "w7" and words[2][0] == "w14"  # stride 7
    assert words[-1][-1] == "w22" and len(chunks) == 3  # stops once the last word is covered
    assert all(body[c.char_start : c.char_end] == c.text for c in chunks)
    assert [c.ord for c in chunks] == [0, 1, 2]
    assert [c.page for c in chunks] == [1, 1, 2]


def test_naive_fixed_short_and_empty_docs() -> None:
    p = NaiveFixed(window_words=10, overlap_words=3)
    assert [c.text for c in p.chunk(Doc(1, "  just three words ", [0]))] == ["just three words"]
    assert p.chunk(Doc(1, "", [0])) == []


def test_naive_fixed_rejects_bad_overlap() -> None:
    with pytest.raises(ValueError):
        NaiveFixed(window_words=5, overlap_words=5)


def test_registry() -> None:
    p = get_pipeline("naive-fixed@1")
    assert (p.name, p.version, p.params) == (
        "naive-fixed", 1, {"window_words": 350, "overlap_words": 50},
    )  # fmt: skip
    for bad in ("naive-fixed@9", "nope@1", "naive-fixed"):
        with pytest.raises(ValueError):
            get_pipeline(bad)
