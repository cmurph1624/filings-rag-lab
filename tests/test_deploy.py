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


SCRIPTS = [
    "scripts/serving_dump.sh",
    "scripts/ssm_run.sh",
    "scripts/smoke_prod.sh",
    "deploy/restore_index.sh",
    "deploy/render_env.sh",
    "deploy/deploy.sh",
    "deploy/frl-compose",
]


@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_parse(script: str) -> None:
    subprocess.run(["bash", "-n", str(REPO_ROOT / script)], check=True)


def test_scripts_are_executable() -> None:
    for script in SCRIPTS:
        assert (REPO_ROOT / script).stat().st_mode & 0o111, script


def test_the_server_reads_every_secret_terraform_creates() -> None:
    """render_env.sh names each /frl/prod parameter that ssm.tf creates, and no others."""
    ssm = (REPO_ROOT / "infra" / "prod" / "ssm.tf").read_text()
    block = re.search(r"secrets = toset\(\[(.*?)\]\)", ssm, re.S)
    assert block is not None
    created = set(re.findall(r'"([a-z_]+)"', block.group(1))) | {"acme_email"}
    render = (REPO_ROOT / "deploy" / "render_env.sh").read_text()
    required = re.search(r"required = \[(.*?)\]", render, re.S)
    assert required is not None
    read = set(re.findall(r'"([a-z_]+)"', required.group(1)))
    read |= set(re.findall(r'"[A-Z_]+": "([a-z_]+)"', render))  # the optional ones
    assert read == created


def test_workflows_deploy_from_the_production_environment_only() -> None:
    for name in ("deploy.yml", "restore-index.yml"):
        text = (REPO_ROOT / ".github" / "workflows" / name).read_text()
        assert "environment: production" in text and "workflow_dispatch" in text, name
        assert "runs-on: ubuntu-24.04-arm" in text, name  # the server is arm64
