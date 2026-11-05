"""Guardrails for a public endpoint that spends money on every answer.

- **Per-IP rate limit:** at most `rate_per_ip_hour` accepted questions per client per rolling
  hour, counted in `rate_events` by a salted hash of the address (raw addresses are never
  stored).
- **Daily spend cap:** before any spend, a question reserves `reserve_usd` by inserting its
  `served_queries` row as `reserved`; the reservation is refused if today's total (UTC) plus
  the reservation would pass `daily_cap_usd`. The row is settled to the real cost afterwards.
  The check and the insert run under one advisory lock, so concurrent requests can't both
  squeeze under the cap.
- **Concurrency:** at most `max_concurrency` questions are worked on at once; a request that
  can't start within `busy_wait_s` is turned away.

The real backstop for spend is the monthly limit on the Anthropic API key; this cap keeps a
burst from reaching it.
"""

import datetime as dt
import hashlib
import math
import threading
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg

LIMIT_DEFAULTS: dict[str, Any] = {
    "rate_per_ip_hour": 10,
    "daily_cap_usd": 3.0,
    "reserve_usd": 0.06,
    "max_concurrency": 3,
    "busy_wait_s": 30.0,
}
_CAP_LOCK = "frl_daily_cap"


class Refused(Exception):
    """A request turned away before any spend. `kind` names the reason for the client."""

    def __init__(self, kind: str, message: str, status_code: int, retry_after: int | None) -> None:
        self.kind = kind
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(message)


def ip_hash(ip: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]


def seconds_to_utc_midnight(now: dt.datetime) -> int:
    tomorrow = (now + dt.timedelta(days=1)).date()
    midnight = dt.datetime.combine(tomorrow, dt.time(), tzinfo=dt.UTC)
    return max(1, math.ceil((midnight - now).total_seconds()))


@dataclass
class Limits:
    rate_per_ip_hour: int = 10
    daily_cap_usd: float = 3.0
    reserve_usd: float = 0.06
    max_concurrency: int = 3
    busy_wait_s: float = 30.0

    def __post_init__(self) -> None:
        self._slots = threading.BoundedSemaphore(self.max_concurrency)

    @classmethod
    def from_config(cls, serve: dict[str, Any]) -> "Limits":
        return cls(**{k: serve.get(k, v) for k, v in LIMIT_DEFAULTS.items()})

    def check_rate(self, conn: psycopg.Connection[Any], client: str | None) -> None:
        """Count this request against the client's hour, or raise `Refused` (429). Commits."""
        if client is None:
            return
        # Serialize one client's requests so parallel ones can't all read a count under the
        # limit; released at the commit below.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"frl_rate:{client}",))
        conn.execute("DELETE FROM rate_events WHERE at < now() - interval '1 day'")
        n, oldest, now = conn.execute(
            "SELECT count(*), min(at), now() FROM rate_events"
            " WHERE ip_hash = %s AND at > now() - interval '1 hour'",
            (client,),
        ).fetchone() or (0, None, None)
        if n >= self.rate_per_ip_hour:
            conn.commit()
            wait = (oldest + dt.timedelta(hours=1) - now).total_seconds() if oldest else 3600
            minutes = max(1, math.ceil(wait / 60))
            raise Refused(
                "rate_limited",
                f"That's {n} questions in the last hour, the limit for now. "
                f"Try again in about {minutes} minute{'s' if minutes != 1 else ''}.",
                429,
                max(1, math.ceil(wait)),
            )
        conn.execute("INSERT INTO rate_events (ip_hash) VALUES (%s)", (client,))
        conn.commit()

    def spent_today(self, conn: psycopg.Connection[Any]) -> float:
        row = conn.execute(
            "SELECT coalesce(sum(cost_usd), 0) FROM served_queries"
            " WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
        ).fetchone()
        return float(row[0]) if row else 0.0

    def reserve(self, conn: psycopg.Connection[Any], qid: uuid.UUID, row: dict[str, Any]) -> None:
        """Insert the query's row as `reserved` at `reserve_usd`, or, over the cap, as
        `refused` at $0 and raise `Refused` (503). `row` holds the row's identifying columns
        (question, config_hash, git_sha, ip_hash, tags; trace_id optional). Commits."""
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_CAP_LOCK,))
            over = self.spent_today(conn) + self.reserve_usd > self.daily_cap_usd + 1e-9
            conn.execute(
                "INSERT INTO served_queries (id, question, status, refusal, cost_usd,"
                " config_hash, git_sha, ip_hash, tags, trace_id)"
                " VALUES (%(id)s, %(question)s, %(status)s, %(refusal)s, %(cost)s,"
                " %(config_hash)s, %(git_sha)s, %(ip_hash)s, %(tags)s, %(trace_id)s)",
                {"trace_id": None}
                | row
                | {
                    "id": qid,
                    "status": "refused" if over else "reserved",
                    "refusal": "daily_cap" if over else None,
                    "cost": 0.0 if over else self.reserve_usd,
                },
            )
            now = conn.execute("SELECT now()").fetchone()
        if over:
            raise Refused(
                "daily_cap",
                "Today's budget for answers is used up. It resets at midnight UTC.",
                503,
                seconds_to_utc_midnight(now[0] if now else dt.datetime.now(dt.UTC)),
            )

    def acquire(self) -> None:
        if not self._slots.acquire(timeout=self.busy_wait_s):
            raise Refused("busy", "Busy answering other questions. Try again in a moment.", 503, 10)

    def release(self) -> None:
        self._slots.release()
