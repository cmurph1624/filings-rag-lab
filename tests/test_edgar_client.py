from pathlib import Path

import httpx
import pytest

from filings_rag.edgar.client import EdgarClient

UA = "Filings RAG Lab test@example.com"


def test_requires_contact_email(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        EdgarClient("no email here", tmp_path)


def test_sends_user_agent_retries_and_caches(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    slept: list[float] = []
    client = EdgarClient(UA, tmp_path, transport=httpx.MockTransport(handler), sleep=slept.append)
    url = "https://data.sec.gov/submissions/CIK0000000001.json"
    assert client.get_json(url) == {"ok": True}
    assert client.get_json(url) == {"ok": True}  # served from cache
    assert len(calls) == 2
    assert all(r.headers["User-Agent"] == UA for r in calls)
    assert (tmp_path / "data.sec.gov/submissions/CIK0000000001.json").exists()
    assert 1.0 in slept  # backoff after the 429


def test_throttles_to_max_rps(tmp_path: Path) -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    client = EdgarClient(
        UA,
        tmp_path,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"{}")),
        max_rps=4,
        clock=lambda: now[0],
        sleep=sleep,
    )
    for i in range(3):
        client.get_bytes(f"https://www.sec.gov/f{i}", cache=False)
    assert slept == pytest.approx([0.25, 0.25])


def test_submissions_merges_paged_history(tmp_path: Path) -> None:
    pages = {
        "/submissions/CIK0000000007.json": {
            "filings": {
                "recent": {"accessionNumber": ["a2"], "form": ["10-K"]},
                "files": [{"name": "CIK0000000007-submissions-001.json"}],
            }
        },
        "/submissions/CIK0000000007-submissions-001.json": {
            "accessionNumber": ["a1"],
            "form": ["10-Q"],
        },
    }
    client = EdgarClient(
        UA,
        tmp_path,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=pages[r.url.path])),
        sleep=lambda s: None,
    )
    rows = client.submissions(7)
    assert [(r["accessionNumber"], r["form"]) for r in rows] == [("a2", "10-K"), ("a1", "10-Q")]
