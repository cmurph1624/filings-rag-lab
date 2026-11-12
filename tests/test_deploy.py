"""Deploy files that must agree with each other."""

import re
import subprocess

import pytest

from filings_rag.harness.answer import REPO_ROOT

DIGEST = re.compile(r"pgvector/pgvector:pg17@sha256:[0-9a-f]{64}")


def test_the_dump_uses_the_production_db_image() -> None:
    """A dump restores cleanly only into the same Postgres/pgvector build that made it."""
    compose = DIGEST.findall((REPO_ROOT / "deploy" / "compose.prod.yaml").read_text())
    assert len(compose) == 1
    for script in ("serving_dump.sh", "backup_drill.sh"):
        assert DIGEST.findall((REPO_ROOT / "scripts" / script).read_text()) == compose, script


SCRIPTS = [
    "scripts/serving_dump.sh",
    "scripts/ssm_run.sh",
    "scripts/smoke_prod.sh",
    "scripts/prod_health.sh",
    "scripts/backup_drill.sh",
    "deploy/restore_index.sh",
    "deploy/render_env.sh",
    "deploy/deploy.sh",
    "deploy/frl-compose",
    "deploy/backup.sh",
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


def test_deploy_installs_the_backup_timer_outside_the_release() -> None:
    systemd = REPO_ROOT / "deploy" / "systemd"
    service = (systemd / "frl-backup.service").read_text()
    assert "ExecStart=/usr/local/bin/frl-backup" in service
    assert "OnCalendar=" in (systemd / "frl-backup.timer").read_text()
    deploy = (REPO_ROOT / "deploy" / "deploy.sh").read_text()
    # Outside /opt/frl/current: a rollback to a release without backup.sh keeps backing up.
    assert '"$HERE/backup.sh" /usr/local/bin/frl-backup' in deploy
    assert "systemd/frl-backup.{service,timer}" in deploy
    assert "enable -q --now frl-backup.timer" in deploy


def test_backups_go_where_only_the_instance_may_put() -> None:
    """backup.sh writes under backups/, the instance role's put-only prefix; the drill and the
    health check read the same prefix."""
    assert "/backups/frl_app/" in (REPO_ROOT / "deploy" / "backup.sh").read_text()
    for script in ("backup_drill.sh", "prod_health.sh"):
        assert "--prefix backups/frl_app/" in (REPO_ROOT / "scripts" / script).read_text()
    iam = (REPO_ROOT / "infra" / "prod" / "iam.tf").read_text()
    assert '"${aws_s3_bucket.data.arn}/backups/*"' in iam
