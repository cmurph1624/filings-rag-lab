from filings_rag.ingest.base import IngestPipeline
from filings_rag.ingest.naive_fixed import NaiveFixed
from filings_rag.ingest.table_preserving import TablePreserving

# name -> version -> pipeline. Add versions; never edit a built one.
PIPELINES: dict[str, dict[int, IngestPipeline]] = {
    "naive-fixed": {1: NaiveFixed(version=1, window_words=350, overlap_words=50)},
    "table-preserving": {1: TablePreserving(version=1)},  # defaults pinned by its test
}


def get_pipeline(spec: str) -> IngestPipeline:
    """`name@version`, e.g. `naive-fixed@1`."""
    name, _, ver = spec.partition("@")
    try:
        return PIPELINES[name][int(ver)]
    except (KeyError, ValueError) as e:
        known = [f"{n}@{v}" for n, vs in PIPELINES.items() for v in vs]
        raise ValueError(f"unknown pipeline {spec!r}; known: {known}") from e
