from filings_rag.config import Settings
from filings_rag.embed.base import Embedder
from filings_rag.embed.voyage import VoyageEmbedder


def make_embedder(name: str, dims: int, settings: Settings) -> Embedder:
    """The embedder a pipeline was built with (pipelines.embedder), for embedding queries."""
    if name.startswith("voyage-"):
        return VoyageEmbedder(settings.voyage_api_key, name, dims=dims)
    raise ValueError(f"no embedder for {name!r}")
