import pytest

from filings_rag.eval.judge import VerdictError, parse_verdict
from filings_rag.eval.report import percentile
from filings_rag.eval.retrieval import hit_at, mrr, recall_at, score


def test_parse_verdict_tolerates_fences_and_case() -> None:
    assert parse_verdict('{"verdict": "correct", "rationale": "matches"}') == ("correct", "matches")
    fenced = 'Here you go:\n```json\n{"verdict": "Refused", "rationale": "declined"}\n```'
    assert parse_verdict(fenced) == ("refused", "declined")
    with pytest.raises(VerdictError):
        parse_verdict("The answer is correct.")
    with pytest.raises(VerdictError):
        parse_verdict('{"verdict": "partially correct"}')


def test_retrieval_metrics() -> None:
    # q1: two passages; one found at rank 2, the other not retrieved
    q1 = score("q1", [10, 11, 12], [{11}, {99}])
    # q2: one passage spread over two chunks; best rank wins
    q2 = score("q2", [20, 21, 22], [{22, 21}])
    # q3: nothing found
    q3 = score("q3", [30], [{31}])
    assert q1.found_at == [2, None] and q2.found_at == [2] and q3.first_rank is None
    qs = [q1, q2, q3]
    assert recall_at(qs, 1) == 0
    assert recall_at(qs, 3) == pytest.approx((0.5 + 1 + 0) / 3)
    assert hit_at(qs, 1) == 0 and hit_at(qs, 2) == pytest.approx(2 / 3)
    assert mrr(qs) == pytest.approx((0.5 + 0.5 + 0) / 3)
    assert recall_at([], 3) == 0 and mrr([]) == 0


def test_duplicate_retrieved_ids_keep_their_best_rank() -> None:
    assert score("q", [5, 6, 5], [{5}]).found_at == [1]


def test_percentile_nearest_rank() -> None:
    xs = [float(x) for x in range(1, 21)]
    assert percentile(xs, 50) == 10 and percentile(xs, 95) == 19 and percentile(xs, 100) == 20
    assert percentile([], 50) is None and percentile([3.0], 95) == 3.0
