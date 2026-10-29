"""Baseline: fixed windows of whitespace-separated words with overlap. Tables and sections are
ignored on purpose; this is the number every later pipeline has to beat."""

import re
from typing import Any

from filings_rag.ingest.base import Chunk, Doc, page_of

WORD = re.compile(r"\S+")


class NaiveFixed:
    name = "naive-fixed"

    def __init__(self, version: int = 1, window_words: int = 350, overlap_words: int = 50) -> None:
        if not 0 <= overlap_words < window_words:
            raise ValueError("overlap must be smaller than the window")
        self.version = version
        self.window = window_words
        self.overlap = overlap_words

    @property
    def params(self) -> dict[str, Any]:
        return {"window_words": self.window, "overlap_words": self.overlap}

    def chunk(self, doc: Doc) -> list[Chunk]:
        words = [(m.start(), m.end()) for m in WORD.finditer(doc.body)]
        chunks: list[Chunk] = []
        step = self.window - self.overlap
        for i, first in enumerate(range(0, len(words), step)):
            last = min(first + self.window, len(words)) - 1
            start, end = words[first][0], words[last][1]
            chunks.append(
                Chunk(i, start, end, page_of(start, doc.page_offsets), doc.body[start:end])
            )
            if last == len(words) - 1:
                break
        return chunks
