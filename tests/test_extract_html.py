from filings_rag.extract.html import html_to_text

DOC = """<html><head><title>t</title><style>p{}</style></head><body>
<div style="display:none"><ix:header><ix:hidden>dei:Secret</ix:hidden></ix:header></div>
<p>Item 7.   Management&#8217;s&nbsp;Discussion</p>
<!-- a comment -->
<table>
  <tr><td>Net sales</td><td style="display:none"></td><td>$</td><td><span>34,229</span></td></tr>
  <tr><td></td><td></td></tr>
  <tr><td><p>Cost</p><p>of sales</p></td><td>19,232</td></tr>
</table>
<p>1</p>
<hr style="page-break-after: always"/>
<p>Page <b>two</b> text</p>
<div style="page-break-before:always">Page three</div>
</body></html>"""


def test_blocks_tables_and_hidden_content() -> None:
    ex = html_to_text(DOC)
    lines = ex.text.split("\n")
    assert lines[0] == "Item 7. Management’s Discussion"
    assert "Net sales | $ | 34,229" in lines
    assert "Cost of sales | 19,232" in lines
    assert "Secret" not in ex.text and "a comment" not in ex.text and "p{}" not in ex.text
    assert "Page two text" in lines


def test_page_offsets_mark_page_starts() -> None:
    ex = html_to_text(DOC)
    assert len(ex.page_offsets) == 3
    assert ex.page_offsets[0] == 0
    assert ex.text[ex.page_offsets[1] :].startswith("Page two text")
    assert ex.text[ex.page_offsets[2] :].startswith("Page three")


def test_single_page_and_no_leading_break() -> None:
    ex = html_to_text('<div style="page-break-before:always">only</div>')
    assert ex.page_offsets == [0] and ex.text == "only"
