"""Decomposed retrieval (P2 PR 6): one retrieval per period a question names, merged.

A question about two or three periods ("FY2022 vs FY2021", "FY2018 - FY2020") retrieves
separately over each period's filings (the scope's `groups`), so one year's filing can't fill
the whole top k. The ranked lists are merged round-robin (each list's best, then each list's
second, ...), duplicates dropped, cut to k. Spend is summed: query embedding per sub-query, and
each component in `aux` (the reranker) by tokens and cost.
"""

from typing import Any

from filings_rag.retrieve.base import Hit, Retrieval


def merge(parts: list[Retrieval], k: int) -> Retrieval:
    if not parts:
        raise ValueError("nothing to merge")
    hits: list[Hit] = []
    seen: set[int] = set()
    for rank in range(max(len(p.hits) for p in parts)):
        for p in parts:
            if rank < len(p.hits) and p.hits[rank].chunk_id not in seen:
                seen.add(p.hits[rank].chunk_id)
                hits.append(p.hits[rank])
    aux: dict[str, dict[str, Any]] = {}
    for p in parts:
        for component, spend in p.aux.items():
            if component not in aux:
                aux[component] = dict(spend)
                continue
            for key in ("tokens", "cost_usd"):
                if key in spend:
                    aux[component][key] = aux[component].get(key, 0) + spend[key]
    return Retrieval(hits[:k], sum(p.embed_tokens for p in parts), parts[0].embedder, aux)
