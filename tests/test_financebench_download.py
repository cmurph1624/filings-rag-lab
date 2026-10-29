from pathlib import Path
from typing import Any

import httpx
import pytest

from filings_rag.benchmark import financebench as fb


def test_download_is_keyed_by_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def fake_get(url: str, **_: Any) -> httpx.Response:
        urls.append(url)
        return httpx.Response(200, content=b'{"a": 1}\n', request=httpx.Request("GET", url))

    # A stale file from an older pin sits at the root; it must not be used.
    (tmp_path / fb.QUESTIONS_FILE).write_text("stale")
    monkeypatch.setattr(fb.httpx, "get", fake_get)
    dest = fb.download(tmp_path)
    assert dest == tmp_path / fb.COMMIT
    assert (dest / fb.QUESTIONS_FILE).read_bytes() == b'{"a": 1}\n'
    assert all(fb.COMMIT in u for u in urls) and len(urls) == 2
    assert not list(dest.glob("*.tmp"))

    fb.download(tmp_path)  # second call is served from disk
    assert len(urls) == 2
