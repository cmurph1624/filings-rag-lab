# P2 route labels (`routes@1`) and the per-route baseline

**FinanceBench open subset, 150 questions. These are not full-set FinanceBench results.**

`corpus/route_labels.csv` labels every question with the route an ideal router would pick. Rules
and the metric vocabulary are in `src/filings_rag/eval/routes.py`. The labels were written by hand
on 2026-10-31, before any P2 router or facts table existed, and are frozen as `routes@1`.

| route | n | what it means |
|---|---|---|
| fact | 63 | answer comes entirely from non-dimensional statement lines plus arithmetic: all 50 metrics-generated questions, and 13 others (e.g. PP&E growth, effective tax rate, cash-flow comparisons) |
| mixed | 17 | a statement-line metric plus a judgment: "does X have positive working capital… if not a useful metric, explain", quick ratio, capital intensity, improving margins |
| prose | 70 | drivers, segments and regions (dimensional), guidance and non-GAAP figures, 8-K events, legal and business descriptions |

50 questions need data from more than one period (`multi_period`).

Judgment calls:

- **Banks, gross and operating margin** (JPM 00206, AmEx 00720, 00723): labelled prose. The gold
  answer is that the metric doesn't apply, so no facts are needed.
- **Segment, region and category figures** (JPM segments, Best Buy categories, MGM regions,
  Boeing segment share): prose. SEC `companyfacts` has no dimensional facts (P2 design decision 1).
- **"Directly outlined in the income statement"** (restructuring, 01319 and 01328): prose. The
  question is about presentation on the statement's face, which XBRL concepts don't record.
- **Questions with no period** take their periods from the gold document.

## Baseline by route (P1 runs)

`frl report 136 --vs 635 --vs 636 --vs 637 --vs 638 --vs 616`:

| subset | run 136 naive | 635 + scope | 636 + rerank | 637 tables | 638 structured | 616 + cite |
|---|---|---|---|---|---|---|
| route: fact (63) | 68.3% | 76.2% | 87.3% | 82.5% | **92.1%** (58) | 90.5% |
| route: mixed (17) | 23.5% | 41.2% | 52.9% | 47.1% | **52.9%** (9) | 47.1% |
| route: prose (70) | 62.9% | 65.7% | 75.7% | 74.3% | **77.1%** (54) | 78.6% |
| multi-period (50) | 50.0% | 58.0% | 72.0% | 74.0% | **80.0%** (40) | 78.0% |
| single period (100) | 66.0% | 72.0% | 81.0% | 75.0% | **81.0%** (81) | 81.0% |

What it says for P2:

- **Mixed questions are the weakest group** at 52.9% (9/17) in run 638. They are where the fact
  route and standard metric definitions should help most. Five of the eight misses are definition
  disagreements, and those may stay misses (P2 decision 4).
- **Fact questions are at 92.1%** (58/63). There the fact route is mostly about determinism and
  traceability, as the plan expects.
- **Multi-period questions have caught up** (80.0% vs 81.0% single period). Decomposition (P2 PR
  6) has little headroom on accuracy, so it should be judged on its retrieval-only gains.
