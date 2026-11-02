import pytest

from filings_rag.eval.report import RunSummary, fixed_broken, mcnemar_p


def summary(run_id: int, verdicts: dict[str, str]) -> RunSummary:
    s = RunSummary(
        run_id=run_id, name="n", config_hash="h", git="g", pipeline="p", corpus="c",
        retriever="r", scope="s", rerank="x", generation="g", judge="j", total=6,
    )  # fmt: skip
    s.verdict_of = verdicts
    return s


def test_fixed_and_broken_count_only_correct_against_not_correct() -> None:
    base = summary(1, {"a": "correct", "b": "correct", "c": "incorrect", "d": "incorrect"})
    other = summary(2, {"a": "correct", "b": "incorrect", "c": "correct", "e": "correct"})
    # c and e (not judged in base) are fixed; b is broken; d (unjudged in other) stays wrong
    assert fixed_broken(other, base) == (2, 1)
    assert fixed_broken(base, base) == (0, 0)
    # a refused answer is not correct
    refused = summary(3, {"a": "refused", "b": "correct"})
    assert fixed_broken(refused, base) == (0, 1)


def test_mcnemar_exact_values() -> None:
    assert mcnemar_p(0, 0) == 1.0
    assert mcnemar_p(5, 5) == 1.0
    # 8 vs 2: P(X<=2 | n=10) = (1+10+45)/1024, doubled
    assert mcnemar_p(8, 2) == pytest.approx(2 * 56 / 1024)
    assert mcnemar_p(2, 8) == mcnemar_p(8, 2)
    # all discordant pairs one way: 2 * 0.5**n
    assert mcnemar_p(6, 0) == pytest.approx(2 / 64)
