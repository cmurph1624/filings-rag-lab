# P3 summary: evals as infrastructure

FinanceBench **open subset (150 questions)**. These are not full-set FinanceBench results. P3 adds no new
best accuracy: the headline stays run 846 (87.3%, 131/150, Sonnet 5.5). What P3 adds is a way to tell whether a
change helped, and a CI check that blocks a change that broke retrieval. This page sums up the seven pieces and
links the detailed write-ups. Every number traces to a run id in those pages.

## What P3 set out to show

The vault's "done when": **a deliberately bad chunking change is blocked by CI.** That happened (PR #31, below).
Along the way P3 also measured the noise, compared models, added two generation metrics, and built synthetic
question sets that stay out of the headline.

## 1. Noise sets the thresholds (`p3-noise.md`)

Run 846's config was run three times (846, 960, 980) and run 846's answers were judged three times.

- Accuracy: 87.3%, 86.7%, 86.7%. 145 of 150 verdicts are identical in all three runs. Any two runs differ on at
  most 4 questions, net 0 or -1.
- The judge agreed with itself on 150/150 when the answers were identical.
- The 5 unstable questions are rewordings of the same figures that sit on a debatable gold (Corning's working
  capital, Boeing's sign on a tax benefit, $0.389B against $0.40B).
- Retrieval was not deterministic: the top-k differed on 40/150 questions, because Voyage `rerank-3` returns scores
  at two precisions and near-ties reorder.

So an accuracy change of one or two questions is not evidence, and a gate cannot sit on answer accuracy. It has to
sit on something exact.

## 2. Caches make retrieval exact (`p3-cache.md`)

An embedding cache and a rerank cache (migration 012) make retrieval repeatable: three retrieval-only runs of the P2
best retriever (1057, 1058, 1059) are identical on 150/150 questions (recall@10 0.796, hit@10 0.852, MRR 0.636). A
new pipeline version re-embeds only chunks whose text changed. Runs made before the caches (846, 960, 980) are not in
them, so a new run of an old config does not reproduce their retrieval exactly.

## 3. The gate (`p3-gate.md`)

`frl gate` runs the gated configs retrieval-only on the 147-filing gate corpus `fb-gate@1` (the 84 question filings
plus each one's next filing), with no model calls, and compares with a pinned baseline in `config/gate.toml`.
Recall@10, hit@10 and MRR may each drop 0.01 (one question of 149 is 0.0067); judge-free fact checks may not drop.

| check | what it runs | baseline run |
|---|---|---|
| gate-best | P2 best retrieval (rules scope, rerank-3, rules router) on the open subset | 1079 |
| gate-vector | plain vector top-10, no scope or reranker, so a chunking regression can't hide behind them | 1080 |
| gate-synth-prose | P2 best retrieval on `synth-prose@1` (section 7) | 1525 |

Known limit: recall@k favours bigger chunks. `naive-fixed@1` *raised* gate-best recall@10 from 0.833 to 0.869, and
P1 showed it gives worse answers. The verdict flags a candidate that retrieves more than 1.25x the baseline's
context and says to run the answer eval.

## 4. The gate in CI, and the bad chunker it blocked (`p3-ci.md`)

A self-hosted runner on the Nucbox (`nucbox-frl`) with its own database (`frl_ci`) runs `eval-gate` on every PR
that touches retrieval code or config. It posts one verdict comment, edited on each push. An `eval:full` label runs
run 846's config at the PR's code, as an informational answer eval.

Draft PR #31 added `naive-fixed@2`, 60-word windows with no overlap. CI built it over the gate corpus (161,917
chunks, about $1.96 of embedding, 47 minutes) and **failed the check**:

| check | metric | structured@1 | naive-fixed@2 |
|---|---|---|---|
| gate-best | recall@10 | 0.833 | 0.584 |
| gate-best | hit@10 | 0.886 | 0.638 |
| gate-best | MRR | 0.651 | 0.395 |
| gate-vector | recall@10 | 0.521 | 0.312 |

41 questions (gate-best) lost their evidence from the top 10. The fact route did not move, since it reads XBRL and
not chunks. #31 was closed unmerged.

## 5. Generation metrics (`p3-gen-metrics.md`)

Back-filled on runs 846, 638 (P1 best) and 922 (decomposition):

- **Fiscal period stated correctly: about 99%** of answered questions in all three. A deterministic check, no model.
- **Claims supported: 97.2% to 97.9%** (`faithful@1`). In run 846, 12 of 18 wrong answers are fully supported by their
  context: errors come from missing or misread context, not invented figures.
- Hand checks: period 20/20 correct. Faithfulness 20 answers, 2 false alarms in 15 flags, no missed flags found.
  The checks were my own reading.
- Faithfulness costs about $12 per run, so it runs on demand and is not in the gate.

## 6. Model comparison (`p3-models.md`)

Run 846's pipeline with only the generator swapped, all judged by the same pinned Opus judge.

| model | run | accuracy | per correct (plain / Batch) | fixed / broken vs Sonnet | McNemar p |
|---|---|---|---|---|---|
| Sonnet 5.5 | 846 | 87.3% (131/150) | $0.0238 / $0.0125 | – | – |
| Haiku 5.5 | 1401 | 84.0% (126/150) | $0.0027 / $0.0020 | 1 / 6 | 0.125 |
| Opus 5.5 | 1443 | 84.0% (126/150) | $0.0512 / $0.0263 | 0 / 5 | 0.062 |
| Qwen3.6-27B, 4-bit, rented 4090 | 1442 | 82.7% (124/150) | $0.0034 (GPU-hour) | 1 / 8 | 0.039 |
| OpenAI `gpt-6.1-sol` | not run | – | – | – | – |

- The whole spread is 7 questions. The pipeline sets most of the result; the generator is a small lever once the
  evidence is in the prompt.
- Opus does not beat Sonnet at about twice the cost. Only Qwen's gap clears p < 0.05, and with 5 to 9 disagreements
  Haiku's and Opus's gaps from Sonnet are not distinguishable from noise.
- **Gaps:** no OpenAI row (no API key). The Opus row is self-judged. Qwen's 4-bit build is a community quantization,
  so model size and quantization are not separated. Qwen's GPU cost omits pod start-up and earlier failed loads.

## 7. Synthetic question sets (`p3-synth.md`)

Two sets, kept in their own `questions.benchmark` values and never folded into the open-subset numbers. `frl report`
and `frl gate` name the set a run used.

- **`synth-facts@1`**: 300 templated (company, year, metric) questions with gold computed from XBRL by `metrics@1`.
  Run 1529: the headline fact equals the gold on 300/300. That is a plumbing and regression check, not evidence
  about the metric definitions, since the same code makes the gold and the answer.
- **`synth-prose@1`**: 150 questions Haiku wrote from sampled 10-K chunks, with the chunk as evidence. Rules dropped
  53 of 230 generations. Hand check of 30: 30/30 supported, 28/30 well-formed. Retrieval (run 1525) is at ceiling
  (recall@10 1.000, MRR 0.952, against 0.796 and 0.643 on the open subset), so the two are not comparable. It works
  as a gate tripwire for broken prose chunking, not as a quality measure.

## What P3 did not show

- **No accuracy gain.** The best config is still P2's. Retrieval metrics and answer accuracy disagreed in P2, and
  the gate checks only the first.
- **The gate catches broken retrieval, not better answers.** It passes a chunker that merely makes chunks bigger.
- **One bad-chunker proof.** One chunker that was clearly bad was blocked; the gate's sensitivity to subtler
  regressions was not measured, apart from the threshold chosen from noise (0.01).
- **Single-sample comparisons.** Each model ran once, and the noise run shows that one run moves by about one
  question. A gap under about 3 questions says nothing.
- **Hand checks are mine.** The period, faithfulness and synth-prose checks were read by the model that wrote the
  tooling, not by a second person.
- **Open subset only.** Nothing here is a full-FinanceBench number.

## Reproduce

```
uv run frl noise 846 960 980 --replicates 3        # section 1
uv run frl gate                                    # sections 3 and 7, retrieval-only
uv run frl gate --pipeline naive-fixed@2 --fresh   # section 4, after adding the pipeline
uv run frl faithful 846 && uv run frl report 846 --vs 638 --vs 922                    # section 5
uv run frl report 846 --vs 1401 --vs 1443 --vs 1442                                   # section 6
uv run frl bench load-synth                        # section 7
```
