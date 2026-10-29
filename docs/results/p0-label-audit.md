# P0 label audit: right answers without labelled evidence

The P0 baseline (run 60) answered 19 questions correctly even though none of the chunks labelled
with their FinanceBench evidence passage was in the top 10. That made the "none retrieved" row of
the baseline's cross-tab (19 correct of 42) suspicious. If those answers came from outside
knowledge or a lenient judge, the 81% vs 45% finding would be weaker.

I read all 19: the answer, its retrieved chunks, and the gold evidence.

## What happened

| where the answer came from | n | questions |
|---|---|---|
| The same figure in **another passage of the right filing**: MD&A tables, segment notes, a market-risk section, an earnings release's key-financials table | 11 | `00605` `00669` `00684` `00859` `00956` `01107` `01928` `01964` `03029` `03849` `04700` |
| The same figure in a **sibling filing for the same period** (the 10-K repeating the Q4 earnings release) | 2 | `00382` `01912` |
| A **period-insensitive** question answered from another year's filing (Amcor's industry, 3M's dividend record) | 2 | `01148` `01858` |
| **Reasoning, not a figure**: "gross/operating margin isn't a useful metric for AmEx", "Verizon is capital intensive" from capex and management's own words | 3 | `00720` `00723` `00215` |
| A **label threshold near-miss**: the chunk just before the labelled one holds 19% of the passage, under the 25% cutoff | 1 | `01226` |

None of the 19 shows outside knowledge leaking in. Every figure in these answers is in a retrieved
excerpt.

Two verdicts are lenient:

- **`00669` (J&J gross margin):** the answer hedges on the drivers.
- **`00215` (Verizon):** it reaches the gold conclusion without the gold's assets-to-revenue
  ratio.

Both were already flagged in `p0-baseline.md`.

An example from the "another passage" row is `00859`. The gold evidence is the derivatives
note's notional table. The top-ranked chunk is the market-risk section of the same 10-K, which
repeats "cross currency swaps … $32.5 billion".

## What it means for the metrics

- **Evidence recall is a conservative proxy.** It measures whether FinanceBench's chosen passage
  came back, not whether *some* passage with the answer did. Filings repeat key figures: statement
  → MD&A → notes → press release. So the "none retrieved" row mixes real misses with retrievals
  that found a duplicate.
- **It errs in the other direction too.** A chunk counts as a hit when it holds ≥ 25% of a passage,
  which a half balance sheet does: Best Buy, PayPal and Walmart in `p0-baseline.md`.
- **The P0 finding stands.** Accuracy with all evidence retrieved (81%) is far above accuracy
  without it (45%). That 45% is itself inflated by duplicates, which makes the true gap wider, not
  narrower.

## Decision: keep labels as they are (no labels@2)

A rule that also labelled chunks containing the gold answer's figures would catch the duplicates.
But gold answers are often short numbers ("$1577.00", "0.68") that appear in many unrelated
places, and some answers are words ("Customer deposits"). That would trade a conservative metric
for a noisy one.

Pipelines in P1 are all labelled from the same FinanceBench passages by the same rule, so
comparisons between them stay fair. Answer accuracy remains the end metric, and evidence recall
stays a diagnostic. The report now says recall is conservative and links here.

The one threshold near-miss (19% vs 25%) isn't enough reason to retune the cutoff, which would
add false positives elsewhere.
