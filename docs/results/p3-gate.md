# P3 gate: `frl gate`, the retrieval gate

FinanceBench open subset (150 questions, 149 with labelled evidence), retrieval-only, no model
calls. Config `config/gate.toml`, code `src/filings_rag/eval/gate.py`.

## What it does

`frl gate [--pipeline name@v]`:

1. Defines the gate corpus `fb-gate@1` if needed.
2. With `--pipeline`, builds that pipeline over the gate corpus, re-embedding only chunks whose
   text changed (PR 2's cache), and labels its chunks with the evidence passages.
3. Runs each gated config retrieval-only at the current code (swapping in the pipeline).
4. Compares each run with its pinned baseline, prints a markdown verdict, and exits 1 on a
   breach.

## Gate corpus: `fb-gate@1`, 147 filings

The 84 question filings plus each one's next filing of the same form (FY+1): 63 more. The next
filing repeats the question year's figures as comparatives, so it is the hardest distractor for
scope and periods. That makes 147 filings, against 367 for `fb-distractors@1`. Manifest:
`corpus/gate_distractors.csv`.

## Gated configs and baselines

| check | config | baseline run | recall@10 | hit@10 | MRR | facts |
|---|---|---|---|---|---|---|
| gate-best | P2 best retrieval: rules scope + rerank-3 + rules router | 1079 | 0.833 | 0.886 | 0.651 | 48/50 |
| gate-vector | plain vector top-10, no scope or reranker | 1080 | 0.521 | 0.557 | 0.282 | – |

gate-vector is there because the scope and reranker can hide a chunking regression.

## Thresholds

From `p3-noise.md`. Retrieval is exactly repeatable with the caches, so an unchanged config
scores the same every time. A new pipeline shifts which chunks hold each evidence passage, so
recall@10, hit@10 and MRR each allow a drop of 0.01 (one question of 149 is 0.0067). The fact
route is deterministic, so no fact check may be lost.

An evidence passage the candidate's pipeline can't label (its chunks don't hold it any more), matched by its evidence index,
counts as not found, and the verdict says how many. Dropping it instead would let a broken chunker raise recall by shrinking
the pool.

## Checks run

- **Unchanged code and config** (runs 1081/1082, again 1123/1124): identical to the baselines on
  every metric, PASS. With the caches warm, a gate run takes about 30 s and costs nothing.
- **`--pipeline naive-fixed@1`** (the P0 chunker, runs 1083/1084): **PASS, and recall goes up**
  (gate-best recall@10 0.833 → 0.869, gate-vector 0.521 → 0.600). Its 350-word windows retrieve
  1.2–1.5× the text of structured@1, and more text per chunk holds evidence more often. P1
  showed structured@1 gives better answers anyway.

### gate-vector: pass

Baseline run 1080 (`structured@1`), candidate run 1084 (`naive-fixed@1`), over the baseline's 149 scored questions.

| metric | baseline | candidate | change | allowed drop | |
|---|---|---|---|---|---|
| recall_at_10 | 0.521 | 0.600 | +0.078 | 0.010 | ok |
| hit_at_10 | 0.557 | 0.658 | +0.101 | 0.010 | ok |
| mrr | 0.282 | 0.355 | +0.073 | 0.010 | ok |

Lost evidence from the top 10 (15): question (baseline rank → candidate rank): 00552 (6 → –), 00603 (6 → –), 00702 (1 → –), 00735 (2 → –), 00822 (1 → –), 00859 (1 → –), 01107 (8 → –), 01275 (8 → –), 01328 (7 → –), 01936 (1 → –), 01964 (4 → –), 02024 (2 → –), 02049 (1 → –), 04700 (1 → –), 08135 (9 → –)

## Limitation: recall@k favours bigger chunks

A chunker that only makes chunks bigger can pass the gate, or even improve on it, without
better answers. The verdict shows the words of context each run retrieves, and when a candidate
retrieves more than 1.25× the baseline it says to run the answer eval (`eval:full`, PR 4)
before trusting the gain. A chunker that splits evidence (small windows, tables cut mid-row)
loses recall and fails. PR 4's deliberately bad chunker tests that case in CI.
