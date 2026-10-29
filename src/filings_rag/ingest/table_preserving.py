"""Tables kept whole, prose windowed like naive-fixed.

P0 found tables split across chunks (a balance sheet's assets in one chunk, its totals in the
next). The extractor (`edgar-html@1`) renders each table row as one line with cells joined by
" | ", so tables are found in the body text and no new extractor version is needed:

- a table is a run of at least `min_rows` row lines; up to `max_gap` label lines in a row may sit
  inside it. A label is a non-row line of at most `label_words` words that doesn't end like a
  sentence ("Assets", "Adjustments to reconcile net income ... to net cash provided by
  operating activities");
- up to `caption_lines` short lines just before it ("Consolidated Balance Sheets",
  "$ in millions") are its caption; a sentence or a bare page number ends the caption, so the
  previous table's footer ("See accompanying notes.", "60") stays with that table;
- a prose stretch of fewer than `min_prose_words` words between tables ("See Notes to
  Consolidated Financial Statements.", a page number) joins the table before it;
- a table of at most `max_table_words` words is one chunk. A longer one is split into row
  groups; each later group's text starts with the caption and the first row (usually the
  column headings) so it still reads as part of that table. Offsets cover the group's own rows.
  `max_table_words` bounds a group's rows; a later group's text can exceed it by its repeated
  header, and each later group holds at least as many lines as that header (built behaviour of
  version 1, pinned: changing it means a new version);
- everything else is prose, windowed like naive-fixed.
"""

import re
from dataclasses import dataclass
from typing import Any

from filings_rag.ingest.base import Chunk, Doc, page_of

WORD = re.compile(r"\S+")
ROW = " | "


@dataclass(frozen=True)
class _Line:
    start: int
    end: int
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())

    @property
    def is_row(self) -> bool:
        return ROW in self.text


@dataclass
class _Segment:
    kind: str  # "table" | "prose"
    first: int  # line index, inclusive
    last: int  # line index, inclusive
    caption: int = 0  # number of caption lines at the start (tables)


def _lines(body: str) -> list[_Line]:
    out = []
    pos = 0
    for raw in body.split("\n"):
        if raw.strip():
            out.append(_Line(pos, pos + len(raw), raw))
        pos += len(raw) + 1
    return out


class TablePreserving:
    name = "table-preserving"

    def __init__(
        self,
        version: int = 1,
        window_words: int = 350,
        overlap_words: int = 50,
        max_table_words: int = 700,
        min_rows: int = 3,
        max_gap: int = 3,
        short_line_words: int = 12,
        label_words: int = 25,
        caption_lines: int = 3,
        min_prose_words: int = 25,
    ) -> None:
        if not 0 <= overlap_words < window_words:
            raise ValueError("overlap must be smaller than the window")
        self.version = version
        self.window = window_words
        self.overlap = overlap_words
        self.max_table_words = max_table_words
        self.min_rows = min_rows
        self.max_gap = max_gap
        self.short = short_line_words
        self.label_words = label_words
        self.caption_lines = caption_lines
        self.min_prose_words = min_prose_words

    @property
    def params(self) -> dict[str, Any]:
        return {
            "window_words": self.window,
            "overlap_words": self.overlap,
            "max_table_words": self.max_table_words,
            "min_rows": self.min_rows,
            "max_gap": self.max_gap,
            "short_line_words": self.short,
            "label_words": self.label_words,
            "caption_lines": self.caption_lines,
            "min_prose_words": self.min_prose_words,
        }

    # --- segmentation ------------------------------------------------------------------------

    def _is_short(self, line: _Line) -> bool:
        return not line.is_row and line.words <= self.short

    def _is_caption(self, line: _Line) -> bool:
        text = line.text.strip()
        return self._is_short(line) and not text.endswith(".") and not text.isdigit()

    def _is_label(self, line: _Line) -> bool:
        return (
            not line.is_row
            and line.words <= self.label_words
            and not line.text.rstrip().endswith((".", "?", "!"))
        )

    def segments(self, lines: list[_Line]) -> list[_Segment]:
        tables: list[_Segment] = []
        i, n = 0, len(lines)
        while i < n:
            if not lines[i].is_row:
                i += 1
                continue
            last, rows, gap, k = i, 1, 0, i + 1
            while k < n:
                if lines[k].is_row:
                    last, rows, gap = k, rows + 1, 0
                elif self._is_label(lines[k]) and gap < self.max_gap:
                    gap += 1
                else:
                    break
                k += 1
            if rows >= self.min_rows:
                floor = tables[-1].last + 1 if tables else 0
                first = i
                while (
                    first - 1 >= floor
                    and i - (first - 1) <= self.caption_lines
                    and self._is_caption(lines[first - 1])
                ):
                    first -= 1
                tables.append(_Segment("table", first, last, caption=i - first))
                i = last + 1
            else:
                i += 1

        segs: list[_Segment] = []
        prev = 0
        for t in tables:
            if t.first > prev:
                segs.append(_Segment("prose", prev, t.first - 1))
            segs.append(t)
            prev = t.last + 1
        if prev < n:
            segs.append(_Segment("prose", prev, n - 1))

        merged: list[_Segment] = []
        for s in segs:
            words = sum(lines[j].words for j in range(s.first, s.last + 1))
            tiny = s.kind == "prose" and words < self.min_prose_words
            if tiny and merged and merged[-1].kind == "table":
                merged[-1].last = s.last  # a footer such as "See Notes to ..."
                continue
            merged.append(s)
        return merged

    # --- chunking ----------------------------------------------------------------------------

    def _prose(self, doc: Doc, start: int, end: int) -> list[tuple[int, int, str]]:
        words = [(m.start() + start, m.end() + start) for m in WORD.finditer(doc.body[start:end])]
        out = []
        step = self.window - self.overlap
        for first in range(0, len(words), step):
            last = min(first + self.window, len(words)) - 1
            a, b = words[first][0], words[last][1]
            out.append((a, b, doc.body[a:b]))
            if last == len(words) - 1:
                break
        return out

    def _table(self, doc: Doc, lines: list[_Line], s: _Segment) -> list[tuple[int, int, str]]:
        span = lines[s.first : s.last + 1]
        a, b = span[0].start, span[-1].end
        if sum(x.words for x in span) <= self.max_table_words:
            return [(a, b, doc.body[a:b])]
        # Header for later groups: the caption plus the first row (the column headings).
        head = span[: s.caption + 1]
        header = "\n".join(x.text for x in head)
        header_words = sum(x.words for x in head)
        budget = max(self.max_table_words - header_words, self.max_table_words // 2)
        out: list[tuple[int, int, str]] = []
        group: list[_Line] = list(head)
        words = header_words
        for line in span[len(head) :]:
            if group and words + line.words > budget + header_words and len(group) > len(head):
                out.append(self._group(doc, group, header if out else None))
                group, words = [], 0
            group.append(line)
            words += line.words
        if group:
            out.append(self._group(doc, group, header if out else None))
        return out

    @staticmethod
    def _group(doc: Doc, group: list[_Line], header: str | None) -> tuple[int, int, str]:
        a, b = group[0].start, group[-1].end
        text = doc.body[a:b]
        return (a, b, f"{header}\n{text}" if header else text)

    def chunk(self, doc: Doc) -> list[Chunk]:
        lines = _lines(doc.body)
        pieces: list[tuple[int, int, str]] = []
        for s in self.segments(lines):
            if s.kind == "table":
                pieces += self._table(doc, lines, s)
            else:
                pieces += self._prose(doc, lines[s.first].start, lines[s.last].end)
        return [
            Chunk(i, a, b, page_of(a, doc.page_offsets), text)
            for i, (a, b, text) in enumerate(pieces)
        ]
