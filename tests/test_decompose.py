import pytest

from filings_rag.retrieve import scope as S
from filings_rag.retrieve.base import Hit, Retrieval
from filings_rag.retrieve.decompose import merge
from filings_rag.retrieve.factory import resolve_spec


def r(ids_scores: list[tuple[int, float]], tokens: int = 5, cost: float = 0.01) -> Retrieval:
    hits = [Hit(i, f"D{i}", 1, sc, "t") for i, sc in ids_scores]
    return Retrieval(
        hits, 3, "voyage", {"rerank": {"model": "rerank-3", "tokens": tokens, "cost_usd": cost}}
    )


def test_round_robin_merge_interleaves_dedupes_and_sums_spend() -> None:
    m = merge([r([(1, 0.9), (2, 0.8), (3, 0.7)]), r([(4, 0.5), (1, 0.4)])], k=4)
    assert [h.chunk_id for h in m.hits] == [1, 4, 2, 3]
    assert m.embed_tokens == 6
    assert m.aux["rerank"] == {"model": "rerank-3", "tokens": 10, "cost_usd": pytest.approx(0.02)}


def test_score_merge_orders_by_score() -> None:
    m = merge([r([(1, 0.9), (2, 0.2)]), r([(4, 0.5), (5, 0.4)])], k=3, how="score")
    assert [h.chunk_id for h in m.hits] == [1, 4, 5]
    with pytest.raises(ValueError, match="decompose_merge"):
        merge([r([(1, 0.9)])], k=1, how="best")


def labels() -> list[S.DocLabel]:
    names = ["BA_2021_10K", "BA_2022_10K", "BA_2023_10K", "BA_2023Q2_10Q", "BA_2022Q4_EARNINGS"]
    return [S.DocLabel.from_doc_name(i, n, "Boeing") for i, n in enumerate(names)]


def test_period_groups_one_per_named_period() -> None:
    docs = labels()
    names = lambda gs: [[d.doc_name for d in g] for g in gs]  # noqa: E731
    two_years = S.period_groups(docs, S.Mentions("Boeing", [2021, 2022]), 0)
    assert names(two_years) == [["BA_2021_10K"], ["BA_2022_10K", "BA_2022Q4_EARNINGS"]]
    year_and_quarter = S.period_groups(docs, S.Mentions("Boeing", [2023], [(2023, 2)]), 0)
    assert names(year_and_quarter) == [["BA_2023_10K"], ["BA_2023Q2_10Q"]]
    assert len(S.period_groups(docs, S.Mentions("Boeing", [2022]), 0)) == 1
    assert S.period_groups(docs, S.Mentions("Boeing", [2015]), 0) == []


def test_decompose_options_are_hash_neutral_and_validated() -> None:
    base = {"kind": "vector", "k": 10, "scope": {"mode": "rules"}}
    plain = resolve_spec(base)
    assert resolve_spec(base | {"decompose": False, "decompose_merge": "round-robin"}) == plain
    assert resolve_spec(base | {"decompose": True})["decompose"] is True
    assert (
        resolve_spec(base | {"decompose": True, "decompose_merge": "score"})["decompose_merge"]
        == "score"
    )
    with pytest.raises(ValueError, match="needs \\[retriever.scope\\]"):
        resolve_spec({"kind": "vector", "decompose": True})
    with pytest.raises(ValueError, match="needs decompose"):
        resolve_spec(base | {"decompose_merge": "score"})
    with pytest.raises(ValueError, match="true or false"):
        resolve_spec(base | {"decompose": "yes"})
