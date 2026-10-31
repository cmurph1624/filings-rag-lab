# P1 retrieval: scoping, reranking and chunking on a corpus with distractors

**FinanceBench open subset, 150 questions. These are not full-set FinanceBench results.**
Every run below uses the corpus `fb-distractors@1`: the 84 filings the questions reference plus 283
others from the same companies (every 10-K for FY2015–2023, and 10-Qs for FY2021–2024 for the six
companies with quarterly questions). Generation is `claude-cli` Sonnet 5.5 with `answer@1`, and the
judge is Opus 5.5 with `judge@1`, unless a row says otherwise. Full report:
[`p1-report.md`](p1-report.md) (`frl report 136 --vs 635 --vs 636 --vs 637 --vs 638 --vs 616`).

## Headline

| | |
|---|---|
| Best config | `structured@1` + rules scope + Voyage `rerank-3` (run 638) |
| Accuracy | **80.7% (121/150)**, up from 60.7% for the naive baseline on the same corpus (run 136) |
| Retrieval | recall@10 0.776, MRR 0.623, recall@1 0.450 (baseline 0.527 / 0.318 / 0.195) |
| Cost per correct answer | **$0.025** plain API, **$0.013** Batch API (baseline $0.035 / $0.018) |

The P0 result on the easy, gold-only corpus was 66.7% (run 60). Accuracy first fell to 60.7% when
distractor years were added, and the retrieval changes then lifted it 20 points above that.

## The ladder (full runs)

Each step adds one change to the step before it, except row 4b, which swaps the pipeline in row 3.

| step | run | accuracy | incorrect | refused | recall@10 | MRR | recall@1 | plain API / correct | Batch / correct |
|---|---|---|---|---|---|---|---|---|---|
| 1. naive, vector top-10 | 136 | 60.7% (91) | 20 | 39 | 0.527 | 0.318 | 0.195 | $0.0351 | $0.0176 |
| 2. + rules scope | 635 | 67.3% (101) | 27 | 22 | 0.663 | 0.392 | 0.232 | $0.0317 | $0.0159 |
| 3. + rerank-3 over 50 | 636 | 78.0% (117) | 25 | 8 | 0.815 | 0.646 | 0.470 | $0.0295 | $0.0157 |
| 4a. pipeline → `table-preserving@1` | 637 | 74.7% (112) | 26 | 12 | 0.784 | 0.654 | 0.482 | $0.0285 | $0.0151 |
| 4b. pipeline → `structured@1` | 638 | **80.7% (121)** | 19 | 10 | 0.776 | 0.623 | 0.450 | $0.0251 | $0.0133 |
| 4b with `answer@2` (cites excerpts) | 616 | 80.0% (120) | 22 | 8 | 0.776 | 0.623 | 0.450 | $0.0266 | $0.0140 |

The cost is answering cost: generation, query embedding, reranking and scoping. Judging costs
about $1.1 per run at plain API rates and is reported separately. Plain API and Batch API are the
comparable views (see [report cost views](p1-report.md#cost)). The as-recorded `claude -p`
figures run about 1.8× plain because of cache writes.

### Questions fixed and broken by each step

| from → to | fixed | broken | net |
|---|---|---|---|
| 136 → 635 (+ scope) | 18 | 8 | +10 |
| 635 → 636 (+ rerank) | 21 | 5 | +16 |
| 636 → 637 (tables) | 6 | 11 | −5 |
| 636 → 638 (structured) | 9 | 5 | +4 |
| 638 → 616 (`answer@2`) | 2 | 3 | −1 |
| 136 → 638 (all of P1) | 33 | 3 | +30 |
| P0 run 60 → 638 | 27 | 6 | +21 |

What the steps did:

- **Scoping and reranking made most of the gain.** Together they add 26 net questions (60.7% →
  78.0%), and refusals drop from 39 to 8. Most baseline misses were refusals because the right
  passage wasn't retrieved, which matches the P0 finding that retrieval is the bottleneck. Rules
  scope narrows 139 of 150 questions to their filings (a median of 2 in scope) and keeps the gold
  filing in scope for 147.
- **Scoping raised incorrect answers from 20 to 27** while it cut refusals. Six questions went
  from refusal to a wrong answer, because the excerpts now came from the right filing
  but not the right passage. Reranking then fixed retrieval for most of them.
- **`structured@1` is the best pipeline on accuracy, but only within noise.** It is 4 net questions
  over naive (9 fixed, 5 broken), which is not significant on 150 questions; McNemar's exact
  p ≈ 0.42. Its retrieval proxies are slightly lower than naive (recall@10 0.776 vs 0.815). The
  likely reason for the higher accuracy is that the context header (`company · form · FY · Item ·
  heading`) tells the generator which filing and section each excerpt comes from, so it mixes up
  periods less. It is also the cheapest per correct answer: about 8.0K prompt tokens per question vs 9.1K for naive.
- **`table-preserving@1` lost 5 net questions** (6 fixed, 11 broken; refusals 8 → 12), with
  slightly lower recall@10 (0.784 vs 0.815). It is within noise too (p ≈ 0.33), so treat it as no
  gain.
- **`answer@2` citations cost nothing in accuracy** (−1 net, noise). Citation precision is 28.0%
  (135 of 482 cited excerpts hold labelled evidence). It is a strict proxy: an excerpt that repeats
  the figure counts as a miss (see [`p0-label-audit.md`](p0-label-audit.md)).

On `answer@1` runs the report also shows a "citation precision" row. That counts the excerpt
numbers `answer@1` answers mention unprompted, so it isn't comparable to run 616's figure.

## Retrieval-only grid

All runs are on `fb-distractors@1` and make no model calls apart from reranking and the `llm`
scope. This is the full set of retrieval-only runs from PRs 2–7, plus run 639 (oracle + rerank-3, added after PR 8).

| pipeline | retriever | scope | rerank | run | recall@1 | recall@10 | MRR |
|---|---|---|---|---|---|---|---|
| naive-fixed@1 | vector | none | – | 135 | 0.195 | 0.527 | 0.318 |
| naive-fixed@1 | vector | rules | – | 169 | 0.232 | 0.663 | 0.392 |
| naive-fixed@1 | vector | llm (Haiku) | – | 197 | 0.238 | 0.677 | 0.397 |
| naive-fixed@1 | vector | oracle (upper bound) | – | 170 | 0.272 | 0.774 | 0.460 |
| naive-fixed@1 | vector | none | rerank-3 | 389 | 0.403 | 0.714 | 0.555 |
| naive-fixed@1 | vector | rules | rerank-3 | 390 | 0.470 | **0.815** | 0.644 |
| naive-fixed@1 | vector | rules | rerank-3-lite | 391 | 0.440 | 0.809 | 0.615 |
| naive-fixed@1 | vector | oracle (upper bound) | rerank-3 | 639 | 0.534 | 0.903 | 0.730 |
| naive-fixed@1 | BM25 text | none | – | 316 | 0.074 | 0.117 | 0.086 |
| naive-fixed@1 | hybrid RRF | none | – | 317 | 0.107 | 0.369 | 0.187 |
| naive-fixed@1 | hybrid RRF | rules | – | 318 | 0.168 | 0.511 | 0.285 |
| table-preserving@1 | vector | none | – | 482 | 0.185 | 0.459 | 0.277 |
| table-preserving@1 | vector | rules | – | 483 | 0.201 | 0.585 | 0.361 |
| table-preserving@1 | vector | rules | rerank-3 | 484 | **0.482** | 0.784 | **0.654** |
| table-preserving@1 | hybrid RRF | rules | – | 485 | 0.104 | 0.415 | 0.211 |
| structured@1 | vector | none | – | 612 | 0.154 | 0.456 | 0.245 |
| structured@1 | vector | rules | – | 613 | 0.181 | 0.554 | 0.291 |
| structured@1 | vector | none | rerank-3 | 614 | 0.426 | 0.711 | 0.579 |
| structured@1 | vector | rules | rerank-3 | 615 | 0.450 | 0.776 | 0.624 |

What the grid shows:

- **Hybrid (BM25 + vector RRF) is below vector alone** for `naive-fixed@1` (with and without rules
  scope) and `table-preserving@1` (rules scope). It was dropped from the ladder after PR 4. It was
  not tested with `structured@1` or under a reranker.
- **`rerank-3-lite` is within 0.006 recall@10 of `rerank-3`** at 40% of the price ($0.09 vs
  $0.23 per 150 questions). Either is a rounding error next to generation.
- **The oracle scope (gold filing only) is an upper bound, not a result.** Without a reranker it
  reaches 0.774, below rules + rerank (0.815), so the reranker adds more than perfect filing
  selection does. With the reranker (run 639) it reaches 0.903. So rules scope still leaves about
  0.09 recall@10 to filing selection, mostly questions whose year doesn't map one-to-one to a
  filing (see [`p1-misses.md`](p1-misses.md)).
- **The `llm` scope is about the same as rules** (0.677 vs 0.663, gold in scope 149 vs 147) at
  $0.023 per 150 questions. Rules stayed on the ladder because they cost nothing and keep the
  ladder deterministic.

These retrieval proxies are scored against FinanceBench's evidence passages, so they don't rank
the pipelines the same way accuracy does. `structured@1` is lowest on recall@10 among the reranked
configs but highest on accuracy.

## What's left (run 638, 29 misses)

- **19 incorrect and 10 refused.** By type, novel-generated is the weakest (72%), then
  domain-relevant (76%), with metrics-generated highest (94%).
- **Evidence still not retrieved:** 16.8% of questions have no labelled evidence passage in the
  top 10 (hit@10 0.832).
- **Hand-assigned failure buckets** are in [`p1-misses.md`](p1-misses.md): 11 retrieval, 8
  scoping and 10 generation or eval misses.

## Notes on method

- **Same questions, same judge:** every row is the same 150 questions with the same judge and
  prompt, and 0 errors in each run.
- **P0 run 60 was not re-judged.** `frl judge` skips questions that already have a verdict, and
  the judge (`judge@1`, Opus 5.5) hasn't changed since run 60, so run 60's verdicts compare as
  they are.
- **Pacing:** the runs went through the usage governor (`backends/governor.py`). Runs 635–638
  and the last 140 questions of run 616 took about 16.5 hours unattended on 2026-10-30, with no
  pauses.
- **Code revisions:** run 136 was made at `98b14f0` and the first 10 questions of run 616 at
  `0d0449f` (PR 7). Everything else was made at this PR's `a97724e`. The `git` row in
  [`p1-report.md`](p1-report.md) records this.
