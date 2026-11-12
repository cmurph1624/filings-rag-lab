"""The answer renderer (static/markdown.js), run in node: answers are Markdown from the model."""

import json
import shutil
import subprocess

import pytest

from filings_rag.serve.app import STATIC

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="no node")


def render(text: str, known: list[str] | None = None) -> str:
    script = (
        f"eval(require('fs').readFileSync({json.dumps(str(STATIC / 'markdown.js'))}, 'utf8'));"
        "const [t, k] = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "process.stdout.write(renderAnswer(t, k));"
    )
    out = subprocess.run(
        ["node", "-e", script],
        input=json.dumps([text, known or []]),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def test_the_answer_from_the_screenshot_renders_as_a_list_with_bold_and_citations() -> None:
    text = (
        "**Assessment: 3M is moderately capital-intensive.**\n\n"
        "- **Capex intensity:** Capex was $1,749M, or 5.1% of revenue [F1].\n"
        "- **Depreciation:** about 5.3% of sales [1].\n\n"
        "Manufacturing requires continuing investment."
    )
    html = render(text, ["1", "F1"])
    assert "**" not in html
    assert "<p><strong>Assessment: 3M is moderately capital-intensive.</strong></p>" in html
    assert "<ul><li><strong>Capex intensity:</strong> Capex was $1,749M" in html
    assert '<a href="#src-F1">[F1]</a>' in html and '<a href="#src-1">[1]</a>' in html
    assert html.count("<li>") == 2
    assert "<p>Manufacturing requires continuing investment.</p>" in html


def test_model_text_is_escaped_never_markup() -> None:
    html = render('<script>alert(1)</script> **<img src=x onerror="y">**')
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html
    assert "<strong>&lt;img src=x onerror=&quot;y&quot;&gt;</strong>" in html


def test_citations_link_only_to_sources_that_exist_and_split_lists() -> None:
    html = render("See [1, F2] and [9].", ["1", "F2"])
    assert '<a href="#src-1">[1]</a><a href="#src-F2">[F2]</a>' in html
    assert "[9]" in html and "#src-9" not in html


def test_numbered_lists_tables_headings_code_and_italics() -> None:
    text = (
        "## Summary\n"
        "1. First *point*\n"
        "2. Second, uses `a**b**c`\n\n"
        "| Year | Capex |\n"
        "|---|---:|\n"
        "| FY2022 | $1,749M [F1] |\n"
        "| FY2021 | $1,603M |\n"
    )
    html = render(text, ["F1"])
    assert "<h3>Summary</h3>" in html
    items = "<li>First <em>point</em></li><li>Second, uses <code>a**b**c</code></li>"
    assert f"<ol>{items}</ol>" in html
    assert "<thead><tr><th>Year</th><th>Capex</th></tr></thead>" in html
    assert '<td>$1,749M <a href="#src-F1">[F1]</a></td>' in html
    assert html.count("<tr>") == 3


def test_arithmetic_stars_and_single_newlines_stay_plain() -> None:
    html = render("Margin = 5 * 3 * 2\nnext line")
    assert "<em>" not in html
    assert "<p>Margin = 5 * 3 * 2<br>next line</p>" in html
