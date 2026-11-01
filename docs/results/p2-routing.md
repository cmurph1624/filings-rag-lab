# P2 routing: XBRL facts for fact questions

**FinanceBench open subset, 150 questions. These are not full-set FinanceBench results.**
Every run is on `fb-distractors@1` (the 84 question filings plus 283 distractors), answered by
`claude-cli` Sonnet 5.5 and judged by Opus 5.5 `judge@1`, with no errors. Full report:
[`p2-report.md`](p2-report.md) (`frl report 638 --vs 921 --vs 846 --vs 922`).

## Headline

| | |
|---|---|
| Best config | run 846: `structured@1` + rules scope (forecast lookback, FinanceBench labels) + `rerank-3` + rules router + `answer@4` (XBRL Facts section) |
| Accuracy | **87.3% (131/150)**, up from 80.7% (121/150) for P1's best (run 638) |
| Fact questions (`routes@1`, 63) | **98.4% (62/63)**, up from 92.1% |
| Refusals | 10 → 1 |
| Cost per correct answer | **$0.0238** plain API, $0.0125 Batch API (P1: $0.0251 / $0.0133) |
| Determinism | facts byte-identical across runs 844, 846 and 922 for all 78 questions that got them |

Across the phases: P0 scored 66.7% on the 84 gold filings only. P1 was 60.7% on the distractor
corpus before its changes and 80.7% after them. P2 reaches 87.3%.

## The ladder

Each step adds to the one before.

| step | run | accuracy | incorrect | refused | recall@10 | MRR | plain API / correct | vs previous: fixed / broken |
|---|---|---|---|---|---|---|---|---|
| 1. P1 best | 638 | 80.7% (121) | 19 | 10 | 0.776 | 0.623 | $0.0251 | |
| 2. + scope period options, `answer@3` | 921 | 80.7% (121) | 21 | 8 | 0.796 | 0.637 | $0.0252 | 5 / 5 |
| 3. **+ rules router, `answer@4` facts** | **846** | **87.3% (131)** | 18 | 1 | 0.796 | 0.643 | **$0.0238** | **11 / 1** |
| 4. + decomposition (score merge) | 922 | 86.0% (129) | 20 | 1 | 0.810 | 0.635 | $0.0248 | 0 / 2 |

By route label (`routes@1`) and period:

| subset | 638 | 921 | 846 | 922 |
|---|---|---|---|---|
| fact (63) | 92.1% | 88.9% | **98.4%** | 96.8% |
| mixed (17) | 52.9% | 47.1% | **58.8%** | 52.9% |
| prose (70) | 77.1% | 81.4% | **84.3%** | 84.3% |
| multi-period (50) | 80.0% | 78.0% | **88.0%** | 88.0% |
| single period (100) | 81.0% | 82.0% | **87.0%** | 85.0% |

What each step did:

- **Step 2 (scope options, period headers) is noise on accuracy: 5 fixed, 5 broken.**
  - Retrieval improved (recall@10 0.776 → 0.796, as in PR 2), and prose questions gained 3.
  - Fact and mixed questions lost 3: refusals turned into wrong answers and back, as answers
    changed run to run.
  - The scope options stay: steps 3 and 4 depend on them for the forecast and Ulta questions.
- **Step 3 (router + facts) is the P2 gain: 11 fixed, 1 broken** (McNemar exact p ≈ 0.006).
  - **Retrieval misses fixed:** five of P1's 11 retrieval misses
    ([`p1-misses.md`](p1-misses.md)) are now right from the Facts section: 00302 Pfizer PP&E,
    00678 Boeing gross margin, 04481 PepsiCo EBITDA margin, 06655 Amazon DPO, 10130 Corning DPO.
  - **Scope misses fixed:** step 3 fixes four of P1's 8 scope misses, using step 2's options.
    They are the Boeing forecast (00494) and all three Ulta questions (00603, 00605, 00606).
  - **Cost:** cost per correct answer fell, even though the prompt is about 7% longer.
- **Step 4 (decomposition) is noise: 0 fixed, 2 broken** (p = 0.5).
  - Retrieval improved (recall@10 0.796 → 0.810, as in PR 6), but no answer changed for the
    better.
  - Both "broken" are judge calls on debatable gold. 00005 is Corning's working capital, a
    definition mismatch. 05718 is American Water's dividends: $0.389B against a gold "$0.40B",
    which the step 3 judge accepted.
  - So decomposition is **not** in the best config. It stays available as config.

## Fact route

- **Router accuracy against `routes@1`:** rules 146/150, llm (Haiku) 142/150. The rules
  regexes were refined against the frozen labels, so 146 flatters them; the llm prompt was not
  tuned ([`p2-router.md`](p2-router.md)).
- **Judge-free accuracy:** on the 50 fact questions whose gold is a bare number, the headline
  computed fact equals the gold at the gold's precision for **48/50** (runs 844, 846 and 922
  alike). Both misses are two-step answers, and both of their inputs are given.
- **Coverage:** 79 of the 80 fact and mixed questions have every input in their gold filing's
  XBRL ([`p2-xbrl-facts.md`](p2-xbrl-facts.md)).
- **Spot checks:** `metrics@1` matches 15/15 FinanceBench gold values
  ([`p2-metrics.md`](p2-metrics.md)).
- **Determinism:** the Facts section is identical in every run for every question, because it
  is computed from stored facts, not sampled.

## What's left (run 846, 19 misses)

Each miss was read again and given its cause in run 846. The P1 bucket is in
[`p1-misses.md`](p1-misses.md).

| cause in 846 | n | questions |
|---|---|---|
| Metric definition differs from the gold | 5 | 00080 PayPal and 00799 Amcor (gold: working capital without cash, quick ratio as current assets − inventory), 00540 AES (gold: ending, not average, inventory), 00807 3M quick ratio, 02119 JPM tangible vs book value |
| Gold or judge debatable | 4 | 00283, 00585, 01328, 02419 (unchanged from P1) |
| Scope: no year or company, or an outlook in a release | 4 | 00601, 02416, 00685, 00651 |
| Retrieval: prose or non-GAAP that XBRL doesn't carry | 3 | 00669 J&J margin drivers, 01911 MGM adjusted EBIT, 01079 Amcor's third acquisition |
| Judgment: right numbers, opposite conclusion | 2 | 00790 CVS capital intensity, 00216 Verizon liquidity (new; right in 638) |
| Hedge | 1 | 01902 Best Buy category |

- **Three of P1's retrieval misses moved to definition misses.** For 00080, 00540 and 00799 the
  Facts section now gives the right inputs and a textbook formula, but the gold uses a different
  convention. Standard definitions keep them as misses (P2 decision 4).
- **Retrieval now accounts for 3 misses, down from 11 in P1.** All 3 need prose or non-GAAP
  figures.
- **Most of the rest is definitions, debatable gold and scope edge cases.**

## Method

- **Git revisions:** the runs were made at different commits, recorded on each run: 638 at
  `a97724e` (P1), 921 at `1108393`, 846 at `092d625`, and 922 at `46bb6b4`.
- **Smoke run first:** run 846 began as PR 5's 63-question smoke run. It was resumed to the full
  150 under the same config hash.
- **Parallel shards:** to use the week's remaining subscription usage before the reset, steps 2
  and 4 were judged, and step 4 answered, in three parallel shards (`frl run --only`, the new
  `frl judge --only`). Each shard was paced by the usage governor on its own. Results don't
  depend on the order or concurrency of the calls.
- **Judge:** all judging is Opus 5.5 `judge@1`, the same judge as P0 and P1.
