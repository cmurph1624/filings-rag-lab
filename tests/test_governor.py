import json
import subprocess
from pathlib import Path

import pytest

from filings_rag.backends.base import Result
from filings_rag.backends.claude_cli import ClaudeCliBackend
from filings_rag.backends.factory import make_backend
from filings_rag.backends.governor import (
    Governor,
    UsagePaused,
    default_governor,
    gap_for,
    read_gate,
)
from filings_rag.pricing import Usage

NOW = 1_800_000_000.0
CLI_OK = json.dumps(
    {"result": "42", "usage": {"input_tokens": 7, "output_tokens": 3}, "stop_reason": "end_turn"}
)


def write_gate(d: Path, state: str, pct: float | None, age_s: float = 60) -> None:
    gate = {
        "state": state,
        "pct": pct,
        "reason": f"weekly limit at {pct}%",
        "computed_at": NOW - age_s,
    }
    (d / "gate.json").write_text(json.dumps(gate))


class Clock:
    """A fake monotonic clock that sleep() advances."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def governor(d: Path, clock: Clock | None = None, logs: list[str] | None = None) -> Governor:
    clock = clock or Clock()
    out = logs if logs is not None else []
    return Governor(d, sleep=clock.sleep, clock=clock, wall=lambda: NOW, log=out.append)


def test_read_gate_fails_open(tmp_path: Path) -> None:
    f = tmp_path / "gate.json"
    assert read_gate(f, NOW) is None  # missing
    f.write_text("{oops")
    assert read_gate(f, NOW) is None  # unreadable
    write_gate(tmp_path, "flag", 80.0, age_s=31 * 60)
    assert read_gate(f, NOW) is None  # stale
    write_gate(tmp_path, "flag", 80.0)
    g = read_gate(f, NOW)
    assert g is not None and (g.state, g.pct) == ("flag", 80.0)


@pytest.mark.parametrize(
    ("pct", "gap"), [(None, 10), (0, 10), (49.9, 10), (50, 20), (64, 20), (65, 40), (90, 40)]
)
def test_gap_grows_with_usage(pct: float | None, gap: float) -> None:
    assert gap_for(pct) == gap


def test_calls_are_spaced_by_the_gap(tmp_path: Path) -> None:
    clock, logs = Clock(), []
    g = governor(tmp_path, clock, logs)
    write_gate(tmp_path, "ok", 30.0)
    with g.call():
        clock.t += 3  # the call itself
    with g.call():  # first call: no wait; second: 10 s after the first ended
        pass
    write_gate(tmp_path, "ok", 70.0)
    with g.call():
        pass
    assert clock.slept == [10, 40]
    assert sum("between calls" in m for m in logs) == 2  # logged when the gap changes


def test_flag_stops_before_the_call(tmp_path: Path) -> None:
    write_gate(tmp_path, "flag", 80.0)
    calls = []

    def runner(*a: object, **k: object) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return subprocess.CompletedProcess([], 0, CLI_OK, "")

    b = ClaudeCliBackend("claude-sonnet-5-5", runner=runner, governor=governor(tmp_path))
    with pytest.raises(UsagePaused, match="80"):
        b.generate("s", "p")
    assert calls == [] and not (tmp_path / "events.jsonl").exists()


def test_an_override_or_unknown_reading_keeps_running(tmp_path: Path) -> None:
    logs: list[str] = []
    g = governor(tmp_path, logs=logs)
    with g.call():  # no gate file at all
        pass
    write_gate(tmp_path, "unknown", None)
    with g.call():
        pass
    write_gate(tmp_path, "ok", 80.0)  # gate says ok at 80%: an override is active
    with g.call():
        pass
    assert any("not gating" in m for m in logs)


def test_each_call_is_appended_for_the_usage_ledger(tmp_path: Path) -> None:
    def runner(*a: object, **k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, CLI_OK, "")

    b = ClaudeCliBackend("claude-sonnet-5-5", runner=runner, governor=governor(tmp_path))
    b.generate("s", "p")
    b.generate("s", "p")
    lines = [json.loads(x) for x in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert len(lines) == 2 and lines[0]["request_id"] != lines[1]["request_id"]
    e = lines[0]
    assert e["source"] == "benchmark:filings-rag-lab" and e["model"] == "claude-sonnet-5-5"
    assert e["ts"] == "2027-01-15T08:00:00Z"
    # the shape agent-ops usage/ledger.py event_call reads
    assert e["usage"] == {
        "input_tokens": 7, "output_tokens": 3, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "cache_write_1h_tokens": 0,
    }  # fmt: skip


def test_an_unwritable_events_file_does_not_fail_the_call(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl").mkdir()  # can't be opened for append
    logs: list[str] = []
    g = governor(tmp_path, logs=logs)
    g.record(Result("x", Usage(input_tokens=1), 0.1, "m", "end_turn"))
    assert any("events.jsonl" in m for m in logs)


def test_factory_shares_one_governor() -> None:
    a = make_backend({"backend": "claude-cli", "model": "m"})
    b = make_backend({"backend": "claude-cli", "model": "n"})
    assert isinstance(a, ClaudeCliBackend) and isinstance(b, ClaudeCliBackend)
    assert a.governor is b.governor is default_governor()
