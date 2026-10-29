import json
import os
from pathlib import Path

import psycopg
import pytest

from filings_rag import db
from filings_rag.benchmark import financebench as fb

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL"),
]


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_load_is_idempotent_and_keeps_only_referenced_docs(tmp_path: Path) -> None:
    doc = {
        "doc_name": "ACME_2022_10K",
        "company": "Acme",
        "gics_sector": "X",
        "doc_type": "10k",
        "doc_period": 2022,
        "doc_link": "https://x",
    }
    unused = {**doc, "doc_name": "ACME_2021_10K", "doc_period": 2021}
    _write(tmp_path / fb.DOCS_FILE, [doc, unused])
    q = {
        "financebench_id": "fb_test_1",
        "company": "Acme",
        "doc_name": "ACME_2022_10K",
        "question_type": "metrics-generated",
        "question_reasoning": "x",
        "question": "Q?",
        "answer": "A",
        "justification": "J",
        "evidence": [{"evidence_text": "e", "doc_name": "ACME_2022_10K", "evidence_page_num": 3}],
    }
    _write(tmp_path / fb.QUESTIONS_FILE, [q])

    url = os.environ["DATABASE_URL"]
    db.migrate(url)
    with psycopg.connect(url) as conn:
        assert fb.load(conn, tmp_path) == (1, 1)
        assert fb.load(conn, tmp_path) == (1, 1)
        row = conn.execute(
            "SELECT evidence->0->>'evidence_page_num' FROM questions WHERE id = 'fb_test_1'"
        ).fetchone()
        assert row == ("3",)
        assert conn.execute(
            "SELECT count(*) FROM benchmark_docs WHERE doc_name = 'ACME_2021_10K'"
        ).fetchone() == (0,)
        conn.execute("DELETE FROM questions WHERE id = 'fb_test_1'")
        conn.execute("DELETE FROM benchmark_docs WHERE doc_name = 'ACME_2022_10K'")


def test_load_rejects_unknown_doc(tmp_path: Path) -> None:
    _write(tmp_path / fb.DOCS_FILE, [])
    _write(tmp_path / fb.QUESTIONS_FILE, [{"financebench_id": "x", "doc_name": "NOPE_2020_10K"}])
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, pytest.raises(ValueError):
        fb.load(conn, tmp_path)
