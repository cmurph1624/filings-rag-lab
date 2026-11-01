# P2 decomposed retrieval (retrieval-only)

**FinanceBench open subset, 150 questions. These are not full-set FinanceBench results.**

**What it does:** with `[retriever] decompose = true`, a question that names more than one
period retrieves separately over each period's filings, then merges the lists. The scope
already splits its filings by period (`Scope.groups`), and each sub-retrieval gets its own
`rerank-3` over 50 candidates. Two merge orders are available:

- **`round-robin`** (the default): the best hit from each period, then the second-best, and
  so on.
- **`decompose_merge = "score"`:** all hits sorted by score, which is comparable across periods
  because it is the same reranker scoring the same query.

**What it costs:** spend is summed, so query embedding and reranking are paid once per period.

**Which questions it touches:** 51 of 150 here, those whose scope narrows to filings for two or
more periods.

All runs use the retrieval from run 698: `structured@1` + rules scope (`forecast_lookback`,
FinanceBench labels) + `rerank-3`.

| | run | recall@1 | recall@3 | recall@10 | hit@10 | MRR | rerank cost |
|---|---|---|---|---|---|---|---|
| no decomposition | 698 | 0.463 | 0.677 | 0.796 | 0.852 | 0.641 | $0.17 |
| decompose, round-robin | 901 | 0.292 | 0.595 | 0.795 | 0.846 | 0.516 | $0.23 |
| **decompose, score merge** | **902** | **0.463** | 0.666 | **0.810** | **0.859** | **0.641** | $0.23 |

On the 51 decomposed questions:

| | recall@10 | hit@10 | MRR |
|---|---|---|---|
| run 698 | 0.817 | 0.902 | 0.724 |
| run 901 (round-robin) | 0.814 | 0.882 | 0.357 |
| run 902 (score) | **0.856** | **0.922** | 0.722 |

What the runs show:

- **Round-robin hurts.** It gives rank 1 to the earliest period's best chunk, even when the
  evidence is in the other year, so MRR halves on these questions (0.724 → 0.357) with no recall
  gain.
- **Score merge helps a little, with no losses.** recall@10 rises from 0.817 to 0.856 on the
  decomposed questions, and MRR is unchanged. Three questions gain evidence and none lose it:
  01930 Amcor sales FY2022 vs FY2023, 04412 Lockheed asset turnover, and 07966 Activision capex
  over 3 years.
- **The headroom was small.** PR 1's baseline already had multi-period questions at 80.0%
  accuracy, against 81.0% for single-period ones. Several multi-period questions are also fact
  questions, which PR 5's Facts section answers without retrieval.

**Ladder step 4** (`configs/p2-07-decompose-facts.toml`) adds `decompose = true` and
`decompose_merge = "score"` to step 3.
