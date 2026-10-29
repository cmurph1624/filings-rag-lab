"""Voyage embeddings over the REST API, batched under the per-request limits."""

import time
from collections.abc import Callable, Iterator

import httpx

from filings_rag.embed.base import Embeddings, InputType

URL = "https://api.voyageai.com/v1/embeddings"
MAX_ITEMS = 1000
MAX_TOKENS = 120_000
CHARS_PER_TOKEN = 3  # conservative for numeric financial text; real counts come back in usage
RETRY_STATUS = {429, 500, 502, 503, 504}


def batches(texts: list[str], max_items: int, max_est_tokens: int) -> Iterator[list[str]]:
    batch: list[str] = []
    est = 0
    for t in texts:
        n = len(t) // CHARS_PER_TOKEN + 1
        if batch and (len(batch) >= max_items or est + n > max_est_tokens):
            yield batch
            batch, est = [], 0
        batch.append(t)
        est += n
    if batch:
        yield batch


class VoyageEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str = "voyage-finance-2",
        *,
        dims: int = 1024,
        transport: httpx.BaseTransport | None = None,
        retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        max_items: int = 128,
        max_est_tokens: int = 100_000,  # headroom under the 120K hard limit
    ) -> None:
        if not api_key:
            raise ValueError("VOYAGE_API_KEY is not set")
        self.name = model
        self.dims = dims
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"}, transport=transport, timeout=120
        )
        self._retries = retries
        self._sleep = sleep
        self._max_items = min(max_items, MAX_ITEMS)
        self._max_est_tokens = min(max_est_tokens, MAX_TOKENS)

    def _post(self, batch: list[str], input_type: InputType) -> Embeddings:
        body = {"input": batch, "model": self.name, "input_type": input_type, "truncation": False}
        for attempt in range(self._retries + 1):
            try:
                resp = self._http.post(URL, json=body)
            except httpx.TransportError:
                if attempt == self._retries:
                    raise
                self._sleep(2.0**attempt)
                continue
            if resp.status_code in RETRY_STATUS and attempt < self._retries:
                self._sleep(2.0**attempt)
                continue
            resp.raise_for_status()
            break
        data = resp.json()
        rows = sorted(data["data"], key=lambda r: r["index"])
        vectors = [r["embedding"] for r in rows]
        if len(vectors) != len(batch) or any(len(v) != self.dims for v in vectors):
            raise ValueError(f"unexpected embedding shape from {self.name}")
        return Embeddings(vectors, int(data["usage"]["total_tokens"]))

    def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        vectors: list[list[float]] = []
        tokens = 0
        for batch in batches(texts, self._max_items, self._max_est_tokens):
            out = self._post(batch, input_type)
            vectors += out.vectors
            tokens += out.tokens
        return Embeddings(vectors, tokens)
