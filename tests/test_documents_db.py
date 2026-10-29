import os
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from filings_rag import db
from filings_rag.edgar.fetch import upsert_document

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL"),
]

ACC = "0000000000-99-000001"


def _values(extractor: str, body: str) -> dict[str, Any]:
    return {
        "accession": ACC, "doc_name": None, "cik": 1, "ticker": "T", "company": "Test",
        "form_type": "10-K", "fiscal_year": 2022, "fiscal_period": "FY", "period_end": None,
        "filing_date": "2023-01-01", "raw_dir": "raw/1/x", "parts": Jsonb([]),
        "page_offsets": [0], "body": body, "extractor": extractor,
    }  # fmt: skip


def test_new_extractor_version_adds_a_row_instead_of_overwriting() -> None:
    url = os.environ["DATABASE_URL"]
    db.migrate(url)
    with psycopg.connect(url) as conn:
        try:
            v1 = upsert_document(conn, _values("x@1", "old"))
            assert upsert_document(conn, _values("x@1", "old, refreshed")) == v1
            v2 = upsert_document(conn, _values("x@2", "new"))
            assert v2 != v1
            rows = conn.execute(
                "SELECT extractor, body FROM documents WHERE accession = %s ORDER BY extractor",
                (ACC,),
            ).fetchall()
            assert rows == [("x@1", "old, refreshed"), ("x@2", "new")]
        finally:
            conn.rollback()
