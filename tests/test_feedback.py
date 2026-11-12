"""prod-feedback@1: pulled thumbs-downs become draft cases, gold is filled by hand."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from filings_rag.benchmark import feedback
from filings_rag.benchmark.synth import read_jsonl
from filings_rag.cli import app
from filings_rag.harness import config as run_config
from filings_rag.harness.answer import REPO_ROOT
from filings_rag.serve import config as serve_config

QID = "d789206a-0003-40b4-bab2-7a83ae238931"


def row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "query_id": QID,
        "asked_at": "2026-11-12T16:56:01.1+00:00",
        "question": "What was 3M's FY2018 capex?",
        "answer": "about $1.6 billion",
        "rating": "down",
        "comment": "wrong year",
        "scope": {"level": "filings", "docs": ["3M_2018_10K"]},
        "git_sha": "ad0b4641734c4b507b9b99c01dcbb8f98b9873db",
        "tags": ["prod"],
    }
    return base | over


def test_a_draft_keeps_the_question_and_leaves_the_gold_empty() -> None:
    q = feedback.draft(row())
    assert q.id == "prodfeedback1/d789206a0003" and q.benchmark == feedback.SET
    assert q.question == "What was 3M's FY2018 capex?"
    assert q.doc_name == "3M_2018_10K"  # the scope's one filing, as a starting point
    assert q.answer == "" and q.company == ""
    assert QID in (q.justification or "") and "ad0b4641" in (q.justification or "")
    assert feedback.problems(q) == ["no answer", "no company"]


def test_no_filing_is_guessed_when_the_scope_picked_several() -> None:
    q = feedback.draft(row(scope={"docs": ["3M_2018_10K", "3M_2019_10K"]}))
    assert q.doc_name == ""


@pytest.mark.parametrize(
    ("over", "why"), [({"tags": ["smoke"]}, "smoke"), ({"rating": "up"}, "not down")]
)
def test_smoke_traffic_and_thumbs_up_never_become_cases(over: dict[str, object], why: str) -> None:
    with pytest.raises(ValueError, match=why):
        feedback.draft(row(**over))


def test_picking_by_id_prefix_needs_one_exact_match() -> None:
    other = row(query_id="d789206b-0000-0000-0000-000000000000")
    rows = [row(), other]
    assert feedback.pick(rows, ["D789206A"]) == [row()]
    with pytest.raises(ValueError, match="matches 2"):
        feedback.pick([row(), row()], ["d789206a"])
    with pytest.raises(ValueError, match="shorter than 8"):
        feedback.pick(rows, ["d789"])
    with pytest.raises(ValueError, match="matches 0"):
        feedback.pick(rows, ["ffffffff"])


def test_merging_never_overwrites_a_hand_filled_case() -> None:
    filled = feedback.draft(row())
    filled = type(filled)(**{**filled.__dict__, "answer": "$1,577 million", "company": "3M"})
    merged, added = feedback.merge([filled], [feedback.draft(row()), feedback.draft(row())])
    assert added == 0 and merged == [filled] and feedback.problems(filled) == []


def test_cli_appends_drafts_and_load_refuses_them(tmp_path: Path) -> None:
    pulled = tmp_path / "pulled.jsonl"
    pulled.write_text(json.dumps(row()) + "\n")
    cases = tmp_path / "cases.jsonl"
    runner = CliRunner()
    res = runner.invoke(app, ["bench", "from-feedback", str(pulled), QID[:8], "--out", str(cases)])
    assert res.exit_code == 0, res.output
    assert "remove anything personal" in res.output
    assert [q.id for q in read_jsonl(cases)] == ["prodfeedback1/d789206a0003"]
    again = runner.invoke(
        app, ["bench", "from-feedback", str(pulled), QID[:8], "--out", str(cases)]
    )
    assert "added 0" in again.output and len(read_jsonl(cases)) == 1
    load = runner.invoke(app, ["bench", "load-feedback", "--path", str(cases)])
    assert load.exit_code == 1 and "no answer" in load.output  # no database touched


def test_the_replay_config_is_what_the_site_serves() -> None:
    """configs/p4-prod-feedback.toml must stay serve.toml's effective config, so a case
    reproduces what the user saw."""
    replay = run_config.load(REPO_ROOT / "configs" / "p4-prod-feedback.toml")
    served = serve_config.load(REPO_ROOT / "configs" / "serve.toml").run

    def strip(raw: dict[str, object]) -> dict[str, object]:
        return {k: v for k, v in raw.items() if k not in {"name", "questions", "execution"}}

    assert replay.questions == feedback.SET
    assert strip(replay.raw) == strip(served.raw)
