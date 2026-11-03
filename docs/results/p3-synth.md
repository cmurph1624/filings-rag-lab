# P3 PR 8: synthetic question sets

Two question sets beyond the FinanceBench **open subset (150 questions)**. They are their own
sets (`questions.benchmark`), are never folded into the open-subset numbers, and `frl report` and
the gate name the set a run used. Both live in committed files (`corpus/synth-facts@1.jsonl`,
`corpus/synth-prose@1.jsonl`); `frl bench load-synth` puts them into a database. Migration 014
drops the foreign key from `questions.doc_name` to the benchmark documents, since these
questions are about any fetched 10-K.

## `synth-facts@1`: 300 deterministic fact questions

`frl bench synth-facts`. One question per (company, fiscal year, metric) for the 10-Ks of the
`fb-distractors@1` corpus (32 companies, FY2015–2023), worded like the open subset ("What is the
FY2018 return on assets for 3M, as a percentage rounded to one decimal place?"), with the gold
computed in code by `metrics@1` from XBRL facts. A pair is kept only if every input is as filed
in that year's 10-K. Of 7,143 computable (10-K, metric) pairs, a seeded sample of 300 was taken
company by company in turn (so every company is covered: 8–10 each) with at most 14 per metric
(29 metrics). No model call and no judge.

| run | what | result |
|---|---|---|
| 1529 | `configs/p3-synth-facts.toml`: run 846's scope, rerank-3 and rules router, retrieval-only | headline fact equals the gold number on **300/300**; the gold filing was in scope for 300/300 |

Read this as a plumbing check and a regression test, not as evidence about the metrics. The same
`metrics@1` code computes the gold and the answer, so 300/300 shows that the question wording,
scope (company and year to the right 10-K), anchor filing and fact route fit together on every
company and year. It does not show that the metric definitions are right; that is what the
open-subset fact checks (48/50 in run 846, against FinanceBench's own gold) are for. Every
template is also unit-tested to route to its metric with the rules router, so this set measures
scope, facts and answering, not the router's vocabulary. No answer-model run was made on it.

## `synth-prose@1`: 150 questions written from filing chunks

`frl bench synth-prose`. 230 chunks were sampled (seeded; at most 3 per filing) from the
`structured@1` chunks of the 10-Ks in the gate corpus `fb-gate@1`, only from Items 1, 1A, 1C, 7
and 7A, 120–450 words, under 10% digits (so not tables). Claude Haiku 5.5 (`claude-cli`, under
the usage governor, $0.12 at list) wrote a question and a one-to-three-sentence answer for each
with the pinned prompt `synth-prose@1`. The chunk is the question's evidence, so recall@k works
as for the open subset. Rules dropped 53 of 230 generations (the loop stops at 150 kept):
40 did not name exactly the filing's fiscal year, 10 copied eight or more words from the chunk,
2 were skipped by the model, 1 had an over-long answer. The 150 kept come from 78 filings of 27
companies: Item 1A 60, Item 7 49, Item 1 38, Item 7A 2, Item 1C 1.

### Hand check of 30

I read 30 of the 150 (seeded sample, seed 1102) against their source chunk. This was my own
reading, not Chris's, and the questions came from a model, so treat it as a spot check.

- **Answer supported by the chunk: 30/30.**
- **Well-formed, standalone, one clear answer: 28/30.** Two are weak: the CVS question asking
  about "FY2022" describes an early redemption made in December 2021 (a comparative in the FY2022
  filing, so the period is ambiguous), and an MGM question about rising energy prices is a one-line
  risk-factor bullet that any year's filing repeats.
- Several questions are compound ("what share…, and how does…"). That is by design of the
  prompt, but the answer is then usually spread over two sentences of one chunk.
- Risk-factor questions about generic topics (for example "what regulatory changes could lower
  AES's margins") could be answered by a neighbouring chunk. The labels only count the source
  chunk (and chunks that overlap it), so such a question can look like a miss when retrieval found
  an equally good passage. Recall here is a lower bound for those.

### Retrieval

Run 1525 is `configs/gate-synth-prose.toml`: the P2 best retrieval (vector top-10 over 50
reranked candidates, rules scope), retrieval-only, corpus `fb-gate@1`, `structured@1`.

| | synth-prose@1 (run 1525) | open subset (run 846) |
|---|---|---|
| recall@1 | 0.913 | 0.470 |
| recall@3 | 0.993 | 0.672 |
| recall@5 | 1.000 | 0.774 |
| recall@10 | 1.000 | 0.796 |
| hit@10 | 1.000 | 0.852 |
| MRR | 0.952 | 0.643 |
| questions scored | 150 | 149 |
| gold filing in scope | 100% | 99.3% |

**The synthetic set is much easier, and the two columns are not comparable.** Each question was
written from the chunk it is scored against, so it shares its vocabulary, and the scope narrows
the search to a median of one filing. The open subset's questions are written independently of
the filings and often need figures spread over statements. So this set cannot say how good the
retrieval is; it saturates at recall@10 = 1.000, which leaves no room to show an improvement.

### Use in the gate

It is still useful as a tripwire. A chunker that splits or mangles prose paragraphs loses
recall here even when the table-heavy open subset hides it. `config/gate.toml` gained
`gate-synth-prose` with baseline run 1525 and the same allowed drop (0.01, about 1.5 questions
of 150) as the other checks. The gate passes at the current code in both `frl` and `frl_ci`
(identical metrics; the candidate runs are retrieval-only, about $0.17 of rerank each).
`scripts/ci_gate.sh` loads the committed question files and labels the pipeline before gating.
`synth-facts@1` is not gated: its corpus (`fb-distractors@1`) is not the gate corpus, and the
fact route already has `fact_correct` on the open subset.

## Changes in the code

- `frl bench synth-facts | synth-prose | load-synth`; `benchmark/synth.py` (selection, rules, gold
  text, loading) and `synth_gen.py` (model calls, kept in `data/synth/` so a pause resumes).
- `route.evaluate.fact_route_accuracy` treats every `synth-facts` question as fact-route;
  `frl report` shows a "fact route: headline equals gold" row for runs with a router.
- `frl report` and `frl gate` print the question set, so a synthetic run is never labelled
  "open subset".
- Reloading a set deletes questions no longer in its file (and their cached labels, scopes and
  routes) unless a run used them.

## Limits

- `synth-prose@1` was written by one model with one prompt and kept by simple rules. Its
  questions skew to risk-factor and business-description text (Items 1A and 1).
- Not measured: an answer model on either set, or the noise of repeating the generation.
- Sizes are the plan's (300 and 150). No power calculation was done.
