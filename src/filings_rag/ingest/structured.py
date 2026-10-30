"""table-preserving chunks, each prefixed with a context header and tagged with its section.

PR 6 found that a chunk that is mostly numbers embeds poorly against a question in words, and
a chunk of 10-K text doesn't say which company, year or part of the filing it comes from. Each
chunk's text (what is embedded and what the model reads) starts with one header line:

    Best Buy · 10-K · FY2019 · Item 8. Financial Statements and Supplementary Data ·
    Consolidated Balance Sheets        (one line in the chunk)

- company: `documents.company`; form and period: the filing's FinanceBench-style label (from
  `doc_name`, the same labels query scoping matches), e.g. "10-Q · FY2024 Q2",
  "8-K · 2023-05-30", "earnings release · FY2023 Q4";
- section: the latest Item heading before the chunk ("Item 7. Management's Discussion…"; 8-K
  items like "Item 2.02. Results of Operations…"), or the exhibit part ("EX-99.1"), which
  starts a new section and a new chunk. Table-of-contents entries are table rows, so they
  never count;
- heading: for a table, its first heading-like caption line ("Table of Contents" and dates are
  skipped); for prose, the latest heading-like line before the chunk or in its first three
  lines (a short line that doesn't end like a sentence).

`Chunk.section` stores "item › heading" for citations and the run record. Offsets still point at
the body text without the header.
"""

import re
from bisect import bisect_right
from typing import Any

from filings_rag.edgar.resolve import parse_doc_name
from filings_rag.ingest.base import Chunk, Doc
from filings_rag.ingest.table_preserving import ROW, TablePreserving, _Line, _lines, _Segment

ITEM = re.compile(r"^\s*(?:PART\s+[IV]+\s*[,.\-–—]?\s*)?ITEM\s*(\d{1,2}[A-C]?|\d\.\d{2})\b", re.I)
EXHIBIT = re.compile(r"^\[(EX-[\w.\-]+)\s")
SEP = " · "
FORMS = {"10K": "10-K", "10Q": "10-Q", "8K": "8-K", "EARNINGS": "earnings release"}
NOT_HEADINGS = {"table of contents", "n/a", "none", "not applicable"}
DATE = re.compile(r"^[A-Z][a-z]+\.? \d{1,2}, \d{4}$")  # "March 28, 2019": a signature date


def _clip(text: str, limit: int) -> str:
    text = text.strip().rstrip(".:")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def period_label(doc_name: str) -> tuple[str, str]:
    """(form, period) for the header, from a FinanceBench-style doc_name."""
    ref = parse_doc_name(doc_name)
    form = FORMS.get(ref.kind, ref.kind)
    if ref.dated:
        return form, ref.dated
    period = f"FY{ref.fiscal_year}" + ("" if ref.fiscal_period == "FY" else f" {ref.fiscal_period}")
    return form, period


class Structured(TablePreserving):
    name = "structured"

    def __init__(
        self,
        version: int = 1,
        *,
        max_item_words: int = 25,
        max_heading_words: int = 10,
        max_title_chars: int = 80,
        **table_params: Any,
    ) -> None:
        super().__init__(version, **table_params)
        self.max_item_words = max_item_words
        self.max_heading_words = max_heading_words
        self.max_title_chars = max_title_chars

    @property
    def params(self) -> dict[str, Any]:
        return super().params | {
            "header": "company · form · period · item · heading",
            "max_item_words": self.max_item_words,
            "max_heading_words": self.max_heading_words,
            "max_title_chars": self.max_title_chars,
        }

    def segments(self, lines: list[_Line]) -> list[_Segment]:
        """table-preserving's segments, with prose also split at exhibit headers: an exhibit is
        its own document, so no chunk mixes it with the filing before it."""
        out: list[_Segment] = []
        for seg in super().segments(lines):
            if seg.kind != "prose":
                out.append(seg)
                continue
            start = seg.first
            for j in range(seg.first + 1, seg.last + 1):
                if EXHIBIT.match(lines[j].text):
                    out.append(_Segment("prose", start, j - 1))
                    start = j
            out.append(_Segment("prose", start, seg.last))
        return out

    def _is_heading(self, text: str) -> bool:
        t = text.strip()
        return (
            ROW not in t
            and 0 < len(t.split()) <= self.max_heading_words
            and not t.endswith((".", "?", "!", ","))
            and not t.replace(",", "").replace(".", "").isdigit()
            and t.lower() not in NOT_HEADINGS
            and not DATE.match(t)
            and t[0].isupper()
        )

    def marks(self, body: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
        """(item marks, heading marks): (offset, label) in body order."""
        items: list[tuple[int, str]] = []
        headings: list[tuple[int, str]] = []
        for line in _lines(body):
            t = line.text
            ex = EXHIBIT.match(t)
            if ex:
                items.append((line.start, ex[1]))
            elif ROW not in t and ITEM.match(t) and len(t.split()) <= self.max_item_words:
                items.append((line.start, _clip(t, self.max_title_chars)))
            elif self._is_heading(t):
                headings.append((line.start, _clip(t, self.max_title_chars)))
        return items, headings

    def chunk(self, doc: Doc) -> list[Chunk]:
        base = super().chunk(doc)
        items, headings = self.marks(doc.body)
        item_at = [o for o, _ in items]
        head_at = [o for o, _ in headings]
        company = doc.meta.get("company") or ""
        doc_name = doc.meta.get("doc_name")
        try:
            form, period = period_label(doc_name) if doc_name else ("", "")
        except ValueError:  # not a FinanceBench-style name: header without form and period
            form, period = "", ""
        out = []
        for c in base:
            i = bisect_right(item_at, c.char_start) - 1
            item = items[i][1] if i >= 0 else ""
            caption = next(
                (ln for ln in c.text.split("\n")[: self.caption_lines + 1] if self._is_heading(ln)),
                None,
            )
            if ROW in c.text and caption is not None:  # a table: its caption
                heading = _clip(caption, self.max_title_chars)
            else:
                # the latest heading before the chunk, or one in its first lines (a chunk that
                # opens with "Item 7. …" then "Liquidity and Capital Resources")
                peek = c.char_start + len("\n".join(c.text.split("\n")[:3]))
                h = bisect_right(head_at, peek) - 1
                # a heading counts only if it comes after the current item heading
                ok = h >= 0 and (i < 0 or headings[h][0] >= items[i][0])
                heading = headings[h][1] if ok else ""
            section = " › ".join(x for x in (item, heading) if x) or None
            header = SEP.join(x for x in (company, form, period, item, heading) if x)
            text = f"{header}\n{c.text}" if header else c.text
            out.append(Chunk(c.ord, c.char_start, c.char_end, c.page, text, section))
        return out
