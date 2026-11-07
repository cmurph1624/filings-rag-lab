"""Deploy files that must agree with each other."""

import re
import subprocess

import pytest

from filings_rag.harness.answer import REPO_ROOT

DIGEST = re.compile(r"pgvector/pgvector:pg17@sha256:[0-9a-f]{64}")


def test_the_dump_uses_the_production_db_image() -> None:
    """A dump restores cleanly only into the same Postgres/pgvector build that made it."""
    compose = DIGEST.findall((REPO_ROOT / "deploy" / "compose.prod.yaml").read_text())
    dump = DIGEST.findall((REPO_ROOT / "scripts" / "serving_dump.sh").read_text())
    assert len(compose) == 1 and dump == compose


@pytest.mark.parametrize("script", ["scripts/serving_dump.sh", "deploy/restore_index.sh"])
def test_scripts_parse(script: str) -> None:
    subprocess.run(["bash", "-n", str(REPO_ROOT / script)], check=True)
