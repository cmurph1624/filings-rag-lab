"""EDGAR HTML/iXBRL → plain text, keeping page boundaries.

Block elements end a line; table rows become one line with cells joined by " | ". Hidden
elements (iXBRL header, display:none) are dropped. A CSS page break starts a new page, so
page numbers roughly line up with the printed filing (and with FinanceBench's PDF pages).
"""

import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from bs4.element import NavigableString, Tag

EXTRACTOR = "edgar-html@1"

BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "table", "section", "article", "blockquote",
    "pre", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "center", "font-block",
}  # fmt: skip
SKIP = {"script", "style", "head", "title", "ix:header", "noscript"}
HIDDEN = re.compile(r"display\s*:\s*none", re.I)
BREAK_BEFORE = re.compile(r"(page-break-before\s*:\s*always|break-before\s*:\s*page)", re.I)
BREAK_AFTER = re.compile(r"(page-break-after\s*:\s*always|break-after\s*:\s*page)", re.I)
SPACE = re.compile(r"[\s ​  ]+")


@dataclass
class Extracted:
    text: str
    page_offsets: list[int]  # char offset where each page starts; [0] for one page


class _Renderer:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.page_starts: list[int] = []  # index into lines where a new page starts
        self._buf: list[str] = []

    def end_line(self) -> None:
        line = SPACE.sub(" ", "".join(self._buf)).strip()
        if line:
            self.lines.append(line)
        self._buf = []

    def page_break(self) -> None:
        self.end_line()
        if self.lines and (not self.page_starts or self.page_starts[-1] != len(self.lines)):
            self.page_starts.append(len(self.lines))

    def walk(self, node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                if type(child) is NavigableString:  # skip comments, CDATA, doctype
                    self._buf.append(str(child))
            elif isinstance(child, Tag):
                self.element(child)

    def element(self, el: Tag) -> None:
        name = el.name.lower()
        style = str(el.get("style") or "")
        if name in SKIP or HIDDEN.search(style):
            return
        if BREAK_BEFORE.search(style):
            self.page_break()
        if name == "tr":
            self.end_line()
            cells = []
            for cell in el.find_all(["td", "th"], recursive=False):
                sub = _Renderer()
                sub.walk(cell)
                sub.end_line()
                text = " ".join(sub.lines)
                if text:
                    cells.append(text)
            if cells:
                self.lines.append(" | ".join(cells))
        elif name in BLOCK:
            self.end_line()
            self.walk(el)
            self.end_line()
        else:
            self.walk(el)
        if BREAK_AFTER.search(style):
            self.page_break()


def html_to_text(html: bytes | str) -> Extracted:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    r = _Renderer()
    r.walk(soup)
    r.end_line()
    offsets = [0]
    pos = 0
    starts = set(r.page_starts)
    for i, line in enumerate(r.lines):
        if i in starts and i > 0:
            offsets.append(pos)
        pos += len(line) + 1
    return Extracted("\n".join(r.lines), offsets)
