from filings_rag.config import Settings
from filings_rag.rerank.base import Reranker
from filings_rag.rerank.voyage import VoyageReranker

VOYAGE_MODELS = ("rerank-3", "rerank-3-lite")


def make_reranker(model: str, settings: Settings) -> Reranker:
    """A reranker by model name; add other providers here (P3)."""
    if model in VOYAGE_MODELS:
        return VoyageReranker(settings.voyage_api_key, model)
    raise ValueError(f"unknown reranker {model!r}; known: {list(VOYAGE_MODELS)}")
