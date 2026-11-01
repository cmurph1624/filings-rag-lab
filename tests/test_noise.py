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


def test_render_lists_unstable_questions() -> None:
    gen = noise.agreement([("run 1", {"financebench_id_00001": "correct"}),
                           ("run 2", {"financebench_id_00001": "refused"})])  # fmt: skip
    text = noise.render(gen, None, (1, 1, []), (1, 1, []))
    assert "| 00001 | correct / refused | – |" in text
    assert "Spread: 1 questions" in text
