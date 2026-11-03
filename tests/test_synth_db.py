import os

import psycopg
import pytest
from psycopg.types.json import Jsonb

from filings_rag import db
from filings_rag.benchmark import synth
from filings_rag.edgar.fetch import upsert_document

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="no DATABASE_URL"),
]
DOC = "ZZSYNTH_2022_10K"
SET = "zz-synth@1"


def q(i: int, benchmark: str = SET) -> synth.SynthQuestion:
    return synth.SynthQuestion(
        f"zz_synth/{i}", benchmark, "Zz", DOC, "synthetic", None, f"Q{i}?", "1", None, []
    )


def clean(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    conn.execute("DELETE FROM run_questions WHERE question_id LIKE 'zz_synth/%'")
    conn.execute("DELETE FROM runs WHERE name = 'zz-synth'")
    conn.execute("DELETE FROM questions WHERE id LIKE 'zz_synth/%' OR id = 'financebench_zz'")
    conn.execute("DELETE FROM documents WHERE doc_name = %s", (DOC,))
    conn.commit()


def test_load_upserts_replaces_stale_and_protects_runs() -> None:
    url = os.environ["DATABASE_URL"]
    db.migrate(url)
    with psycopg.connect(url) as conn:
        clean(conn)
        try:
            upsert_document(
                conn,
                {
                    "accession": "0000000000-99-000009", "doc_name": DOC, "cik": 1, "ticker": "Z",
                    "company": "Zz", "form_type": "10-K", "fiscal_year": 2022,
                    "fiscal_period": "FY", "period_end": None, "filing_date": "2023-01-01",
                    "raw_dir": "raw/1/z", "parts": Jsonb([]), "page_offsets": [0], "body": "x",
                    "extractor": "x@1",
                },
            )  # fmt: skip
            conn.commit()
            assert synth.load(conn, [q(1), q(2), q(3)]) == 3
            assert synth.load(conn, [q(2), q(3)]) == 2  # q1 is stale and unused: removed
            ids = {
                r[0] for r in conn.execute("SELECT id FROM questions WHERE benchmark = %s", (SET,))
            }
            assert ids == {"zz_synth/2", "zz_synth/3"}

            run = conn.execute(
                "INSERT INTO runs (name, config, config_hash, git_sha, git_dirty)"
                " VALUES ('zz-synth', '{}', 'h', 's', false) RETURNING id"
            ).fetchone()
            assert run is not None
            conn.execute(
                "INSERT INTO run_questions (run_id, question_id, git_sha) VALUES (%s, %s, 's')",
                (run[0], "zz_synth/3"),
            )
            conn.commit()
            with pytest.raises(ValueError, match="delete those runs first"):
                synth.load(conn, [q(2)])
            conn.rollback()
            assert synth.load(conn, [q(2), q(3)]) == 2  # still there
        finally:
            conn.rollback()
            clean(conn)


def test_load_rejects_unfetched_filings_and_open_subset_ids() -> None:
    url = os.environ["DATABASE_URL"]
    db.migrate(url)
    with psycopg.connect(url) as conn:
        with pytest.raises(ValueError, match="not fetched"):
            synth.load(conn, [q(1)])
        conn.rollback()
