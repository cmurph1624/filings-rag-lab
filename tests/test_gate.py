from dataclasses import replace
from pathlib import Path

import pytest

from filings_rag.eval import gate
from filings_rag.eval.retrieval import QuestionRetrieval

ALLOWED = {"recall_at_10": 0.01, "hit_at_10": 0.01, "mrr": 0.01, "fact_correct": 0}


def _m(ranks: dict[str, int | None], facts: dict[str, bool] | None = None,
       words: float = 1000.0) -> gate.Measured:  # fmt: skip
    return gate.Measured(
        1, "p@1", {q: QuestionRetrieval(q, [r]) for q, r in ranks.items()}, facts, words
    )


QIDS = [f"financebench_id_{i:05d}" for i in range(149)]  # the gate set scores 149
BASE = {q: 1 for q in QIDS}


def test_identical_runs_pass() -> None:
    r = gate.compare("c", _m(BASE, {QIDS[0]: True}), _m(BASE, {QIDS[0]: True}), ALLOWED)
    assert r.ok and not r.lost and not r.facts_lost


def test_one_question_lost_is_within_the_allowance_two_are_not() -> None:
    one = gate.compare("c", _m(BASE), _m(BASE | {QIDS[0]: None}), ALLOWED)
    assert one.ok and one.lost == [(QIDS[0], 1, None)]
    two = gate.compare("c", _m(BASE), _m(BASE | {QIDS[0]: None, QIDS[1]: 11}), ALLOWED)
    assert not two.ok
    assert {r.metric for r in two.rows if not r.ok} == {"recall_at_10", "hit_at_10", "mrr"}


def test_unscored_questions_count_as_not_found() -> None:
    cand = {q: 1 for q in QIDS[2:]}  # the candidate's labels lost two questions
    r = gate.compare("c", _m(BASE), _m(cand), ALLOWED)
    assert r.unscored == QIDS[:2] and not r.ok


def test_any_lost_fact_fails() -> None:
    facts = {QIDS[0]: True, QIDS[1]: True, QIDS[2]: False}
    r = gate.compare("c", _m(BASE, facts), _m(BASE, facts | {QIDS[1]: False}), ALLOWED)
    assert not r.ok and r.facts_lost == [QIDS[1]]
    better = gate.compare("c", _m(BASE, facts), _m(BASE, facts | {QIDS[2]: True}), ALLOWED)
    assert better.ok


def test_render_flags_much_more_context() -> None:
    r = gate.compare("c", _m(BASE, words=1000), _m(BASE, words=1500), ALLOWED)
    text = gate.render([r], "fb-gate@1")
    assert "## Retrieval gate: PASS" in text and "1.5× the text" in text
    fail = gate.compare("c", _m(BASE), _m({q: None for q in QIDS}), ALLOWED)
    assert "## Retrieval gate: FAIL" in gate.render([r, fail], "fb-gate@1")
    assert "**breach**" in gate.render([fail], "fb-gate@1")


def test_load_config_checks_metrics(tmp_path: Path) -> None:
    p = tmp_path / "gate.toml"
    p.write_text(
        'corpus = "fb-gate@1"\n[allowed_drop]\nrecall_at_10 = 0.01\nhit_at_10 = 0.01\n'
        'mrr = 0.01\nfact_correct = 0\n[[check]]\nconfig = "configs/x.toml"\nbaseline = 7\n'
    )
    g = gate.load_config(p, tmp_path)
    assert g.checks == [gate.Check(tmp_path / "configs/x.toml", 7)]
    p.write_text(p.read_text().replace("mrr = 0.01\n", "mrr = 0.01\nprecision = 1\n"))
    with pytest.raises(ValueError, match="unknown metrics"):
        gate.load_config(p, tmp_path)


def test_repo_gate_config_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    g = gate.load_config(root / "config/gate.toml", root)
    assert all(c.config.exists() for c in g.checks)


def test_a_passage_the_candidate_cannot_label_counts_as_not_found() -> None:
    q = QIDS[0]
    base = _m(BASE)
    base.retrieval[q] = QuestionRetrieval(q, [1, 2], (0, 1))
    cand = _m(BASE)
    cand.retrieval[q] = QuestionRetrieval(q, [2], (1,))  # passage 0 lost its label
    r = gate.compare("c", base, cand, ALLOWED)
    assert r.passages_lost == 1
    recall = next(x for x in r.rows if x.metric == "recall_at_10")
    assert recall.candidate < recall.baseline  # not flat: the lost passage stays in the pool
    assert "1 of the baseline's 150 labelled evidence passages" in gate.render([r], "g")


def test_passages_align_by_evidence_index_not_position() -> None:
    q = QIDS[0]
    base, cand = _m(BASE), _m(BASE)
    base.retrieval[q] = QuestionRetrieval(q, [1, 2], (0, 1))
    cand.retrieval[q] = QuestionRetrieval(q, [1, None], (0, 1))  # passage 1 dropped out
    r = gate.compare("c", base, cand, ALLOWED)
    assert r.passages_lost == 0
    assert next(x for x in r.rows if x.metric == "recall_at_10").candidate < 1.0


def test_render_names_the_question_set() -> None:
    r = gate.compare("c", _m(BASE), _m(BASE), ALLOWED)
    assert "FinanceBench open subset (150 questions)." in gate.render([r], "g")
    synth = gate.compare("s", replace(_m(BASE), questions="synth-prose@1"), _m(BASE), ALLOWED)
    text = gate.render([synth], "g")
    assert "Synthetic set `synth-prose@1`" in text
    assert "open subset (150" not in text


def test_short_ids() -> None:
    assert gate._short("financebench_id_00005") == "00005"
    assert gate._short("synthprose1/3M_2018_10K/12") == "synthprose1/3M_2018_10K/12"
