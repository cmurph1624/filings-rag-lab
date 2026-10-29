"""Plain-SQL migrations: apply migrations/NNN_*.sql in order, once each."""

from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def pending(applied: set[str], directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Migration files not yet applied, in filename order."""
    return [p for p in sorted(directory.glob("*.sql")) if p.name not in applied]


def migrate(database_url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations, each in its own transaction. Returns the names applied."""
    done: list[str] = []
    with psycopg.connect(database_url) as conn:
        conn.execute(_LEDGER)
        conn.commit()
        rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
        for path in pending({r[0] for r in rows}, directory):
            with conn.transaction():
                conn.execute(path.read_text().encode())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            done.append(path.name)
    return done
