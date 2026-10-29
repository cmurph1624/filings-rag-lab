"""Usage governor for `claude -p` benchmark calls on the Max subscription.

Reads the machine-wide gate that ~/dev/agent-ops/usage keeps in
`~/.local/state/usage/gate.json` (no import, just the file):

- **Stop:** `state == "flag"` (the 5-hour or weekly limit is at 75%+ with no `usage override`)
  raises UsagePaused before the call, so nothing is spent. The run loops treat it as a clean,
  resumable stop.
- **Pace:** calls go one at a time, with a gap that grows with usage (10 s, 20 s from 50%,
  40 s from 65%).
- **Account:** each call is appended to `events.jsonl` as `benchmark:filings-rag-lab`. These
  calls run with --no-session-persistence and leave no transcript, so the usage ledger only
  sees them through this file.

Fails open: a missing, unreadable or stale gate means go ahead at the base gap.
"""

import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from filings_rag.backends.base import Result

SOURCE = "benchmark:filings-rag-lab"
BASE_GAP_S = 10.0
GAPS = ((65.0, 40.0), (50.0, 20.0))  # (from this % up, seconds between calls)
STALE_AFTER_S = 30 * 60  # the poller rewrites gate.json every 5 minutes


class UsagePaused(Exception):
    """The usage governor says stop. Nothing was spent; resume the run later."""


@dataclass(frozen=True)
class Gate:
    state: str  # "ok" | "flag" | "unknown"
    pct: float | None
    reason: str


def state_dir() -> Path:
    """Same place agent-ops usage/paths.py uses."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "usage"


def read_gate(path: Path, now: float) -> Gate | None:
    """The current gate, or None when it can't be trusted (missing, unreadable, stale)."""
    try:
        g = json.loads(path.read_text())
        computed = float(g["computed_at"])
        pct = None if g.get("pct") is None else float(g["pct"])
        gate = Gate(str(g["state"]), pct, str(g.get("reason") or ""))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None if now - computed > STALE_AFTER_S else gate


def gap_for(pct: float | None) -> float:
    if pct is not None:
        for floor, gap in GAPS:
            if pct >= floor:
                return gap
    return BASE_GAP_S


class Governor:
    def __init__(
        self,
        directory: Path | None = None,
        *,
        source: str = SOURCE,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        log: Callable[[str], None] = print,
    ) -> None:
        self.dir = directory or state_dir()
        self.source = source
        self._sleep, self._clock, self._wall, self._log = sleep, clock, wall, log
        self._lock = threading.Lock()
        self._last_end: float | None = None
        self._last_gap: float | None = None
        self._warned: set[str] = set()

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self._log(f"usage governor: {message}")

    @contextmanager
    def call(self) -> Iterator[None]:
        """Hold for the duration of one model call: checks the gate, waits out the gap."""
        with self._lock:
            gate = read_gate(self.dir / "gate.json", self._wall())
            if gate is None:
                self._warn_once("unknown", "no fresh usage reading; not gating")
            elif gate.state == "flag":
                raise UsagePaused(gate.reason or "usage limit at 75%")
            gap = gap_for(gate.pct if gate else None)
            if gap != self._last_gap:
                why = gate.reason if gate else "usage unknown"
                self._log(f"usage governor: {gap:.0f}s between calls ({why})")
                self._last_gap = gap
            if self._last_end is not None:
                wait = self._last_end + gap - self._clock()
                if wait > 0:
                    self._sleep(wait)
            try:
                yield
            finally:
                self._last_end = self._clock()

    def record(self, result: Result) -> None:
        """Append the call to events.jsonl for the usage ledger. Never fails the call."""
        u = result.usage
        line = {
            "request_id": f"frl-{uuid.uuid4()}",
            "ts": datetime.fromtimestamp(self._wall(), UTC).isoformat().replace("+00:00", "Z"),
            "source": self.source,
            "model": result.model,
            "cwd": os.getcwd(),
            "usage": {
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cache_read_tokens": u.cache_read_tokens,
                "cache_write_tokens": u.cache_write_tokens,
                "cache_write_1h_tokens": u.cache_write_1h_tokens,
            },
        }
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with (self.dir / "events.jsonl").open("a") as f:
                f.write(json.dumps(line) + "\n")
        except OSError as e:
            self._warn_once(
                "events", f"can't append to events.jsonl ({e}); the run DB still has the tokens"
            )


@cache
def default_governor() -> Governor:
    """One per process, so every claude-cli backend in it shares the pacing."""
    return Governor()
