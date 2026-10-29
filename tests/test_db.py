import os
from pathlib import Path

import pytest

from filings_rag import db


def test_pending_orders_and_skips_applied(tmp_path: Path) -> None:
    for name in ["002_b.sql", "001_a.sql", "003_c.sql", "notes.txt"]:
        (tmp_path / name).write_text("SELECT 1;")
    names = [p.name for p in db.pending({"002_b.sql"}, tmp_path)]
    assert names == ["001_a.sql", "003_c.sql"]


@pytest.mark.db
@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL")
def test_migrate_is_idempotent() -> None:
    url = os.environ["DATABASE_URL"]
    db.migrate(url)
    assert db.migrate(url) == []


def test_migrate_refuses_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        db.migrate("postgresql://unused", tmp_path)
