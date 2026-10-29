"""EDGAR HTTP client: required User-Agent, rate limit, retries and an on-disk cache."""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

MAX_RPS = 8.0  # SEC allows 10 req/s; stay under it.
RETRY_STATUS = {429, 500, 502, 503, 504}


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        cache_dir: Path,
        *,
        transport: httpx.BaseTransport | None = None,
        max_rps: float = MAX_RPS,
        retries: int = 4,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT must include a contact email")
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            transport=transport,
            timeout=60,
            follow_redirects=True,
        )
        self._cache_dir = cache_dir
        self._interval = 1.0 / max_rps
        self._retries = retries
        self._clock = clock
        self._sleep = sleep
        self._last = -float("inf")

    def _cache_path(self, url: str) -> Path:
        u = urlparse(url)
        return self._cache_dir / u.netloc / u.path.lstrip("/")

    def _throttle(self) -> None:
        wait = self._last + self._interval - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last = self._clock()

    def get_bytes(self, url: str, *, cache: bool = True) -> bytes:
        path = self._cache_path(url)
        if cache and path.exists():
            return path.read_bytes()
        for attempt in range(self._retries + 1):
            self._throttle()
            try:
                resp = self._http.get(url)
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
        if cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(resp.content)
            tmp.replace(path)  # atomic: a killed run never leaves a truncated cache entry
        return resp.content

    def get_json(self, url: str, *, cache: bool = True) -> Any:
        return json.loads(self.get_bytes(url, cache=cache))

    def submissions(self, cik: int) -> list[dict[str, Any]]:
        """Every filing for a CIK (recent block plus the paged history), one dict per filing."""
        base = "https://data.sec.gov/submissions/"
        data = self.get_json(f"{base}CIK{cik:010d}.json")
        blocks = [data["filings"]["recent"]]
        blocks += [self.get_json(base + f["name"]) for f in data["filings"].get("files", [])]
        rows: list[dict[str, Any]] = []
        for block in blocks:
            keys = list(block)
            rows += [
                dict(zip(keys, vals, strict=True)) for vals in zip(*block.values(), strict=True)
            ]
        return rows

    def companyfacts(self, cik: int) -> Any:
        return self.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
