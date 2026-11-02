"""Decomposed retrieval (P2 PR 6): one retrieval per period a question names, merged.

A question about two or three periods ("FY2022 vs FY2021", "FY2018 - FY2020") retrieves
separately over each period's filings (the scope's `groups`), so one year's filing can't fill
the whole top k. The ranked lists are merged, duplicates dropped, cut to k:
- "round-robin" (`decompose = true`): each list's best, then each list's second, ...
- "score" (`decompose_merge = "score"`): by the hits' scores, which are comparable across the
  lists because they come from the same retriever (and reranker) for the same query.
Spend is summed: query embedding per sub-query, and each component in `aux` (the reranker) by
tokens and cost.
"""

from typing import Any

from filings_rag.retrieve.base import Hit, Retrieval

MERGES = ("round-robin", "score")


def merge(parts: list[Retrieval], k: int, how: str = "round-robin") -> Retrieval:
    if not parts:
        raise ValueError("nothing to merge")
    if how not in MERGES:
        raise ValueError(f"decompose_merge must be one of {MERGES}")
    if how == "score":
        ordered = sorted((h for p in parts for h in p.hits), key=lambda h: -h.score)
    else:
        ordered = [
            p.hits[rank]
            for rank in range(max(len(p.hits) for p in parts))
            for p in parts
            if rank < len(p.hits)
        ]
    hits: list[Hit] = []
    seen: set[int] = set()
    for h in ordered:
        if h.chunk_id not in seen:
            seen.add(h.chunk_id)
            hits.append(h)
    aux: dict[str, dict[str, Any]] = {}
    for p in parts:
        for component, spend in p.aux.items():
            if component not in aux:
                aux[component] = dict(spend)
                continue
            for key in ("tokens", "cost_usd", "cached_tokens"):
                if key in spend:
                    aux[component][key] = aux[component].get(key, 0) + spend[key]
    return Retrieval(hits[:k], sum(p.embed_tokens for p in parts), parts[0].embedder, aux)
