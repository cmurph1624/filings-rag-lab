from filings_rag.eval import noise


def test_agreement_counts_unstable_questions_and_pairwise_flips() -> None:
    a = {"q1": "correct", "q2": "refused", "q3": "correct", "q4": "incorrect"}
    b = {"q1": "correct", "q2": "correct", "q3": "incorrect", "q4": "incorrect"}
    c = {"q1": "correct", "q2": "correct", "q3": "correct", "q4": "incorrect", "q5": "correct"}
    ag = noise.agreement([("a", a), ("b", b), ("c", c)])
    assert ag.common == 4  # q5 isn't judged in every source
    assert set(ag.unstable) == {"q2", "q3"}
    assert ag.agreement == 0.5
    assert ag.accuracies == [(2, 4), (2, 4), (4, 5)]
    assert ("a", "b", 1, 1) in ag.pairwise and ("b", "c", 1, 0) in ag.pairwise
    assert noise.flip_kinds(ag.unstable) == {"correct/refused": 1, "correct/incorrect": 1}


def test_identical_share() -> None:
    m1 = {"q1": [1, 2, 3], "q2": [4, 5], "q3": [9]}
    m2 = {"q1": [1, 2, 3], "q2": [5, 4], "q3": [9]}
    assert noise.identical_share([m1, m2]) == (2, 3, ["q2"])  # order matters
    assert noise.identical_share([m1, m1, m1]) == (3, 3, [])


def test_identical_share_skip_empty() -> None:
    m1 = {"q1": None, "q2": {"v": 1}, "q3": {"v": 2}}
    m2 = {"q1": None, "q2": {"v": 1}, "q3": None}
    assert noise.identical_share([m1, m2]) == (2, 3, ["q3"])
    assert noise.identical_share([m1, m2], skip_empty=True) == (1, 2, ["q3"])


def test_render_lists_unstable_questions() -> None:
    gen = noise.agreement([("run 1", {"financebench_id_00001": "correct"}),
                           ("run 2", {"financebench_id_00001": "refused"})])  # fmt: skip
    text = noise.render(gen, None, (1, 1, []), (1, 1, []), (1, 1, []))
    assert "| 00001 | correct / refused | – |" in text
    assert "Spread: 1 questions" in text


def _rv(run_id: int, n: int, answered: int, h: str = "h1", judge: str = "j") -> noise.RunVerdicts:
    return noise.RunVerdicts(run_id, judge, {f"q{i}": "correct" for i in range(n)}, h, answered)


def test_check_comparable() -> None:
    assert noise.check_comparable([_rv(1, 3, 3), _rv(2, 3, 3)]) == []
    dup = noise.check_comparable([_rv(1, 3, 3), _rv(1, 3, 3)])
    assert len(dup) == 1 and "duplicate" in dup[0]
    assert "different configs" in noise.check_comparable([_rv(1, 3, 3), _rv(2, 3, 3, "h2")])[0]
    partial = noise.check_comparable([_rv(1, 3, 3), _rv(2, 2, 3)])
    assert partial == ["run 2 under j: 2 judged of 3 answered"]


def test_check_comparable_replicates() -> None:
    reps = [_rv(1, 3, 3, judge="j"), _rv(1, 3, 3, judge="j#r2"), _rv(1, 1, 3, judge="j#r3")]
    assert noise.check_comparable(reps, replicates=True) == [
        "run 1 under j#r3: 1 judged of 3 answered"
    ]
