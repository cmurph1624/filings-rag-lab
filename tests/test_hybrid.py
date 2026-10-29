import pytest

from filings_rag.retrieve.base import Hit
from filings_rag.retrieve.factory import make_retriever, resolve_spec
from filings_rag.retrieve.hybrid import rrf


def _hits(*ids: int) -> list[Hit]:
    return [Hit(i, f"D{i}", 1, 0.0, f"t{i}") for i in ids]


def test_rrf_rewards_agreement_and_breaks_ties_on_chunk_id() -> None:
    fused = rrf([_hits(1, 2, 3), _hits(3, 4, 1)], k=4, rrf_k=60)
    assert [h.chunk_id for h in fused] == [1, 3, 2, 4]
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 63)
    assert fused[2].score == fused[3].score  # 2 and 4 both rank 2 on one side only
    assert [h.chunk_id for h in rrf([_hits(1, 2, 3), _hits(3, 4, 1)], k=2)] == [1, 3]


def test_rrf_keeps_one_side_when_the_other_is_empty() -> None:
    assert [h.chunk_id for h in rrf([_hits(5, 6), []], k=10)] == [5, 6]
    assert rrf([[], []], k=10) == []


def test_resolve_text_and_hybrid_defaults() -> None:
    assert resolve_spec({"kind": "text"}) == {
        "kind": "text", "k": 10, "ranking": "bm25", "k1": 1.2, "b": 0.75,
    }  # fmt: skip
    assert resolve_spec({"kind": "text", "ranking": "ts_rank_cd"}) == {
        "kind": "text", "k": 10, "ranking": "ts_rank_cd",
    }  # fmt: skip
    assert resolve_spec({"kind": "hybrid", "k": 5}) == {
        "kind": "hybrid", "k": 5, "candidates": 50, "rrf_k": 60, "search": "exact",
        "ranking": "bm25", "k1": 1.2, "b": 0.75,
    }  # fmt: skip
    assert resolve_spec({"kind": "hybrid", "search": "hnsw"})["ef_search"] == 100
    with pytest.raises(ValueError, match="options"):
        resolve_spec({"kind": "text", "search": "exact"})
    scoped = resolve_spec({"kind": "hybrid", "scope": {"mode": "rules"}})
    assert scoped["scope"] == {"mode": "rules", "lookahead": 0}


def test_hybrid_candidates_must_cover_k() -> None:
    with pytest.raises(ValueError, match="candidates"):
        make_retriever(None, {"kind": "hybrid", "k": 20, "candidates": 10}, "t", None, [])  # type: ignore[arg-type]
