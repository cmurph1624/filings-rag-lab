"""Voyage rerankers over the REST API (POST /v1/rerank).

Model and price checked against docs.voyageai.com on 2026-10-29: rerank-3 ($0.05/M tokens) and
rerank-3-lite ($0.02/M). Truncation is off, so an over-long chunk fails loudly instead of being
silently scored on a prefix; P1 candidates are ~50 chunks of ~350 words, far below the limits.
"""

import time
from collections.abc import Callable

import httpx

from filings_rag.rerank.base import Reranked

URL = "https://api.voyageai.com/v1/rerank"
MAX_DOCUMENTS = 1000
RETRY_STATUS = {429, 500, 502, 503, 504}


class VoyageReranker:
    def __init__(
        self,
        api_key: str,
        model: str = "rerank-3",
        *,
        transport: httpx.BaseTransport | None = None,
        retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("VOYAGE_API_KEY is not set")
        self.name = model
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"}, transport=transport, timeout=120
        )
        self._retries = retries
        self._sleep = sleep

    def rerank(self, query: str, texts: list[str], top_k: int) -> Reranked:
        if not texts:
            return Reranked([], 0)
        if len(texts) > MAX_DOCUMENTS:
            raise ValueError(f"at most {MAX_DOCUMENTS} documents per rerank request")
        body = {
            "query": query,
            "documents": texts,
            "model": self.name,
            "top_k": min(top_k, len(texts)),
            "truncation": False,
        }
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
        order = [(int(r["index"]), float(r["relevance_score"])) for r in data["data"]]
        if any(not 0 <= i < len(texts) for i, _ in order):
            raise ValueError(f"rerank returned an index out of range from {self.name}")
        order.sort(key=lambda r: (-r[1], r[0]))  # documented as sorted; don't rely on it
        return Reranked(order, int(data["usage"]["total_tokens"]))
