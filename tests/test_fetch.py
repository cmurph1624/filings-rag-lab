# ruff: noqa: E501
import pytest

from filings_rag.edgar.fetch import FilingFile, build_body, filing_files, select_files

INDEX = b"""<html><body>
<table class="tableFile" summary="Document Format Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>8-K</td><td><a href="/ix?doc=/Archives/edgar/data/1/000000000123000005/acme-8k.htm">acme-8k.htm</a> iXBRL</td><td>8-K</td><td>50</td></tr>
<tr><td>2</td><td>EX-99.1</td><td><a href="/Archives/edgar/data/1/000000000123000005/ex991.htm">ex991.htm</a></td><td>EX-99.1</td><td>5</td></tr>
<tr><td>3</td><td>AR</td><td><a href="/Archives/edgar/data/1/000000000123000005/ex13.htm">ex13.htm</a></td><td>EX-13</td><td>5</td></tr>
<tr><td>3</td><td></td><td><a href="/Archives/edgar/data/1/000000000123000005/logo.jpg">logo.jpg</a></td><td>GRAPHIC</td><td>5</td></tr>
<tr><td></td><td>Complete submission text file</td><td><a href="/Archives/edgar/data/1/000000000123000005/x.txt">x.txt</a></td><td></td><td>9</td></tr>
</table>
<table class="tableFile" summary="Data Files"><tr><td>4</td><td>x</td><td><a href="/a.xsd">a.xsd</a></td><td>EX-101.SCH</td></tr></table>
</body></html>"""


def test_filing_files_reads_document_table_only() -> None:
    files = filing_files(INDEX)
    assert [f.filename for f in files] == [
        "acme-8k.htm",
        "ex991.htm",
        "ex13.htm",
        "logo.jpg",
        "x.txt",
    ]
    assert (
        files[0].url == "https://www.sec.gov/Archives/edgar/data/1/000000000123000005/acme-8k.htm"
    )


def test_select_primary_then_ex99() -> None:
    picked = select_files(filing_files(INDEX), "acme-8k.htm")
    assert [f.type for f in picked] == ["8-K", "EX-99.1", "EX-13"]
    with pytest.raises(ValueError):
        select_files(filing_files(INDEX), "missing.htm")


def test_build_body_offsets() -> None:
    a = FilingFile("1", "8-K", "a.htm", "u")
    b = FilingFile("2", "EX-99.1", "b.htm", "u")
    body, parts, pages = build_body(
        [(a, b"<p>cover</p>"), (b, b"<p>p1</p><hr style='page-break-after:always'><p>p2</p>")]
    )
    assert body[parts[0]["start"] : parts[0]["end"]] == "cover"
    assert body[parts[1]["start"] : parts[1]["end"]] == "p1\np2"
    assert "[EX-99.1 b.htm]" in body
    assert [body[p:].split("\n")[0] for p in pages] == ["cover", "p1", "p2"]
