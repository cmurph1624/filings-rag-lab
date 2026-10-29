# P0 baseline: naive-fixed@1 + vector top-10

**FinanceBench open subset, 150 questions. These are not full-set FinanceBench results.**
Run 60, config `6cc9a6708ce5ab47` (`configs/p0-naive.toml`), 2026-10-29. Full report:
[`p0-report.md`](p0-report.md) (`frl report 60`).

| | |
|---|---|
| Accuracy (Opus 5.5 judge) | **66.7%** (100/150): 27 incorrect, 23 refused |
| By type | metrics-generated 76%, domain-relevant 64%, novel-generated 60% |
| Retrieval | recall@10 0.670, hit@10 0.718, MRR 0.412, recall@1 0.262 |
| Cost per correct answer | **$0.032** plain API, **$0.016** Batch API ($0.058 as recorded by `claude -p`) |
| Total cost to answer 150 | $3.19 plain API, $1.60 Batch API; judging $1.13 plain API |
| Latency | retrieval p50 0.2 s; generation p50 4.4 s, p95 12.2 s |

## Setup

- **Corpus:** the 84 filings the 150 questions reference, from EDGAR HTML (FinanceBench's PDFs
  were not needed). One shared index over all of them: no company, form or year filter.
- **Ingest `naive-fixed@1`:** 350-word windows with 50 words of overlap, tables flattened to
  `a | b | c` rows, embedded with `voyage-finance-2`. That gives 17,570 chunks.
- **Retrieval:** exact cosine top-10.
- **Generation:** `claude-cli` Sonnet 5.5, prompt `answer@1`. It answers only from the numbered
  excerpts and says so when it can't.
- **Judge:** `claude-cli` Opus 5.5, prompt `judge@1`. It grades against the gold answer and its
  reasoning as correct, incorrect or refused.

### How this compares, and why it's higher than expected

The FinanceBench paper reports that GPT-4-Turbo with a retrieval system "incorrectly answered or
refused to answer 81% of questions" on the same 150-case sample, so about 19% correct
([arXiv 2311.11944](https://arxiv.org/abs/2311.11944)). Our 66.7% is not a like-for-like win:

- **The model is two years newer.**
- **The embedder is finance-tuned.**
- **The corpus is smaller.** Ours is the 84 documents these questions use, so there are far fewer
  distractors than a full filing library. On average 7 of the 10 retrieved chunks already come
  from the right document. P1 adds other fiscal years of the same companies, which is where
  naive top-k is expected to break.

The honest reading: this is a **ceiling for naive retrieval on an easy corpus**, and the
failures below show where it already breaks.

## Retrieval is the bottleneck

Accuracy split by whether the question's evidence passages were in the top 10:

| evidence in top 10 | questions | correct | incorrect | refused | accuracy |
|---|---|---|---|---|---|
| all passages | 93 | 75 | 13 | 5 | **81%** |
| some passages | 14 | 6 | 1 | 7 | 43% |
| none | 42 | 19 | 12 | 11 | **45%** |
| no labels | 1 | 0 | 1 | 0 | – |

31 of the 50 misses had some or all of the evidence missing. When retrieval delivers the
evidence, the generator gets 4 in 5 right.

The 19 correct answers in the "none" row haven't been inspected one by one yet. The likely
explanation is that another chunk of the right filing repeats the figure (MD&A text, a summary
table) without matching the labelled passage. The labels only know FinanceBench's evidence
pages. Checking this is the first P1 task, because it decides whether "none" really means
missed.

## Failure buckets (all 50 misses, each assigned its primary cause by hand)

| bucket | n | what happened |
|---|---|---|
| Evidence not retrieved | 19 | The right filing's chunks came back, but not the passage that holds the answer. Mostly refusals ("the excerpts don't contain…") or answers built from nearby figures. |
| Wrong period or company retrieved | 4 | Other fiscal years or other companies crowded out the right one. |
| Multi-input calculation missing an input | 7 | Ratio questions (DPO, CCC, inventory turnover, 3-year averages) need 2–4 statement lines across two years. Retrieval found some, and the model correctly refused rather than guess. |
| Table split across chunks | 3 | The labelled chunk was retrieved but holds only half the balance sheet. The needed rows are in the next chunk. |
| Over-cautious with evidence present | 2 | The model wouldn't assert an absence ("no acquisitions") or a ranking from the excerpts. |
| Definition or judgment differs from gold | 8 | It computed a different but defensible metric, or drew the opposite qualitative conclusion. |
| Hedged between answers | 4 | It gave the gold answer as one of two readings; the judge counts a hedge as incorrect. |
| Gold or judge debatable | 3 | Rounding at the tolerance edge, or a gold answer that reads a past event as ongoing. |

### Examples

**Evidence not retrieved (19).**
- `00302` *Did Pfizer grow its PP&E between FY20 and FY21?* All 10 chunks are from the right
  10-K, none is the balance sheet, and the model refuses.
- Also in this bucket: `00216` Verizon quick ratio, `00552` Microsoft debt, `00585` Boeing tax
  rate (it found a rate with the sign flipped), `00591` Adobe FCF conversion, `00603`/`00606`
  Ulta, `00678` Boeing gross margin, `00799` Amcor quick ratio, `01028` AmEx regions, `01079`
  Amcor acquisitions, `01328` PepsiCo restructuring (answered 0, gold $411M), `01865` 3M
  segment growth, `02049` JPM VaR, `02119` JPM liquidation value, `02416` Pfizer acquisitions,
  `03471` General Mills working capital, `07507` Adobe operating income, `00499` 3M capital
  intensity.

**Wrong period or company (4).**
- `00601` *What drove the reduction in Ulta's SG&A as a percent of sales in FY2023?* None of the
  10 chunks is from Ulta's earnings release. The answer is about **Amcor**'s exchange rates.
- `00685` Best Buy margin consistency was answered from the FY2013–17 tables in the 2017 and
  2019 10-Ks.
- `00735` PepsiCo legal matters were answered for FY2021 only.
- `01911` MGM interest coverage retrieved the 2018/2020/2022 10-Ks but not the Q4 2022
  earnings release that holds adjusted EBIT.

This is the characteristic FinanceBench failure, and it is already visible with only a handful
of extra years in the index.

**Multi-input calculations (7).**
- `10130` Corning FY2020 DPO: the model found the payables but not FY2020 COGS, so it
  declined.
- Also: `00540` AES and `00711` J&J inventory turnover, `03620` PepsiCo EBITDA less capex,
  `04080` Nike inventory turnover, `04103` General Mills CCC, and `07966` Activision 3-year
  capex.

The refusals are the right behaviour. What's wrong is retrieving one chunk per statement
line. This is the clearest case for statement-level retrieval or XBRL facts (P2).

**Table split across chunks (3).**
- `04417` *Best Buy FY2019 inventories?* The top-ranked chunk is the balance sheet, starting at
  `assets | 606 | 374` and running through total assets and liabilities. The current-assets
  block with *Merchandise inventories 5,409* is in the previous chunk. The model: *"The current
  assets section … is cut off."*
- `00080` PayPal (the current-assets total is in the other half) and `06247` Walmart show the
  same split. Walmart: *"The balance sheet in Excerpt 6 is cut off after 'Accounts payable |
  46,092 |'."*
- These also show that recall overstates usable evidence: a chunk counts as a hit when it holds
  ≥25% of the passage, which a half-table does.

This is the case for the P1 `table-preserving` pipeline.

**Definition or judgment differs (8).**
- `00005` Corning working capital: $2,278M total against the gold's $831M "operating" working
  capital.
- `00807` 3M quick ratio, 0.85 vs 0.96: the gold subtracts only inventories.
- `10420` AES ROA: consolidated vs attributable net loss.
- Also: `00283` Pfizer remaining Upjohn costs, `00790` CVS capital intensity, `00460` Best Buy
  store count (branded vs total), `00651` J&J EPS growth, `01484` J&J US vs international
  growth (operational vs reported).
- Several of these are FinanceBench's own conventions, not errors. A prompt that names the
  definition would fix some, but that tunes to the benchmark, so it's out of scope for P0.

**Hedges (4).**
- `00822` Foot Locker board nominee: right name and vote counts, plus two **PepsiCo** nominees,
  because 8 of the 10 chunks were PepsiCo's proxy results.
- Also: `00299` JPM lowest-revenue segment, `01902` Best Buy category, `00839` Foot Locker CEO
  experience.

**Debatable (3).**
- `05718` American Water dividends: $0.389B is called incorrect against a gold of $0.40B.
- `03856` Adobe operating cash flow ratio: 0.82 vs 0.83.
- `02419` *As of Q2 2023, is Pfizer spinning off any large business segments?* Gold says
  "Upjohn". The model says Upjohn was spun off in 2020, which is defensible.

## Judge spot-check

- **Random sample:** 10 "correct" verdicts, all defensible. One (`00669` J&J gross-margin drivers)
  accepts a hedge that the judge penalised elsewhere, a mild inconsistency in `judge@1`.
- **Every miss:** all 50 were read and bucketed above. 3 look like judge or gold strictness, so
  accuracy might be about 2 points higher with a more lenient judge.
- **Judge is pinned:** it's fixed per run (`claude-cli:claude-opus-5-5:judge@1`),
  so later pipelines are graded the same way.

## Cost

- **Pricing:** list prices from `config/prices.toml`, whoever paid. This run went through the
  subscription.
- **As recorded:** `claude -p` writes each new prompt to the 1-hour cache at 2× input, so the
  recorded $5.75 is mostly cache writes.
- **Comparable views:** at plain API rates the same tokens cost $3.19, and $1.60 through the
  Batch API. These are the numbers to compare across runs and backends.
- **Per question:** about 9.1K prompt tokens and 313 output tokens. Query embedding is
  negligible ($0.0008 for 150 questions). Building the index cost 10.2M Voyage tokens ($1.22 at
  list, inside the free tier).

## What P1 should move

1. **Fiscal-period and company metadata as hard filters.** This targets the wrong period/company
   bucket and the cross-company contamination: 38 of 150 questions had another company's chunks
   in their top 10.
2. **`table-preserving` chunking**, so a statement stays whole. This targets the table-split and
   multi-input buckets.
3. **Hybrid BM25 + vector, then a reranker.** This targets "evidence not retrieved": recall@1 is
   0.26 even though the right filing dominates the top 10.
4. **Distractor years in the corpus**, so the next numbers are measured on a harder and more
   realistic index. Expect accuracy to drop before it rises.
