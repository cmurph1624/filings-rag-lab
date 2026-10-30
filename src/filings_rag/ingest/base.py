"""Ingest pipelines: named, versioned chunking strategies that each build their own index."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Doc:
    id: int
    body: str
    page_offsets: list[int]
    # Filing metadata for pipelines that use it (structured@1's context headers):
    # doc_name, company.
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    ord: int
    char_start: int
    char_end: int
    page: int  # 1-based page where the chunk starts
    text: str  # what is embedded and shown to the model
    section: str | None = None  # e.g. "Item 8. Financial Statements … › Balance Sheets"


class IngestPipeline(Protocol):
    name: str
    version: int

    @property
    def params(self) -> dict[str, Any]:
        """Everything that shapes the output; stored and checked so a version never drifts."""
        ...

    def chunk(self, doc: Doc) -> list[Chunk]: ...


def chunk_table(name: str, version: int) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in name)
    return f"chunks__{safe}__v{version}"


def page_of(offset: int, page_offsets: list[int]) -> int:
    """1-based page containing `offset` (page_offsets[i] = start of page i+1)."""
    lo, hi = 0, len(page_offsets) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if page_offsets[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1
