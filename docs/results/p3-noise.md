# P3 noise: how much results move when nothing changes

FinanceBench open subset (150 questions), distractor corpus. Config
`configs/p2-04-router-rules-facts.toml` (the P2 best), run three times: runs 846, 960 and 980.
Run 846's answers were also judged three times by the same pinned judge
(`frl judge 846 --replicate 2|3`). Reproduce the tables with
`frl noise 846 960 980 --replicates 3`.

## Findings

- **Accuracy moves by one question.** 87.3%, 86.7%, 86.7%. 145 of 150 verdicts are identical
  in all three runs. Any two runs differ on at most 4 questions, with a net change of 0 or −1.
- **The judge agrees with itself on identical text.** Three judgings of run 846's answers gave
  the same verdict on 150/150.
- **It doesn't on near-identical text.** Of the 5 unstable questions, 3 (00005, 00585, 05718)
  have the same numbers and reasoning in every run, just reworded. The judge decides
  differently because they sit on a debatable gold: Corning's "working capital" definition,
  Boeing's sign convention for a tax benefit on a loss, and $0.389B against a gold of $0.40B.
  The other 2 (00216 Verizon's quick-ratio interpretation, 00460 which Best Buy store count to
  lead with) are the model framing the same facts differently.
- **The fact route is deterministic.** Its output is identical across runs for all 78
  questions that got facts.
- **Retrieval is not deterministic, and the cause is the reranker.** The top-k differs on
  40/150 questions: 29 only in order, 11 in which chunks came back. Voyage `rerank-3` returns
  the same scores at two precisions (e.g. 0.89755 in one call, 0.89844 in another, which is a
  multiple of 1/256), so near-ties reorder. Of the 5 unstable verdicts, only 00005 also had a
  retrieval change. Caching rerank results by (query, candidate ids, model) alongside the
  embedding cache in PR 2 makes retrieval repeatable.

## Gate thresholds (proposal for PR 3)

The gate compares a candidate run with the baseline question by question, with the same
judge.

1. **Accuracy.** Fail if the net loss (broken − fixed) is **3 or more questions** (2 points).
   Warn at 2. Noise gave at most −1 net, and at most 2 broken in any pair.
2. **Known-noisy questions.** List the 5 unstable questions in `corpus/noisy.csv`. Report
   their flips but don't count them toward the net. On the other 145, no verdict changed in
   three runs, so a single break there is worth reading.
3. **Facts.** Exact. Judge-free fact accuracy must not drop below 48/50, and any change in
   fact-route output on a question with unchanged inputs fails.
4. **Retrieval.** Exact once PR 2 caches the reranker: gold-page recall and MRR must equal
   the baseline's when retrieval config is unchanged, and must not drop when it changed.
   Until then, no retrieval gate (11 questions change their chunk set on their own).
5. **No significance test as the gate.** With only 4 discordant questions per pair at noise
   level, McNemar can't flag a 2-point drop. Report its p-value next to the net count, as in
   P1 and P2, but fail on the net threshold.

`frl noise` refuses runs with different config hashes, duplicate run ids, and any run or
replicate that is only partly judged. Errored rows are left out of the determinism checks
(none of the three runs had any). The judge backend caches prompt prefixes, not responses, so
each replicate is a fresh judgment.

Judge replicates are cheap evidence the judge is stable on identical inputs. They don't
remove the debatable-gold flips, which is why the noisy list exists.

# Generated tables

## Generation (same config, separate runs, pinned judge)

| run | accuracy |
|---|---|
| run 846 | 87.3% (131/150) |
| run 960 | 86.7% (130/150) |
| run 980 | 86.7% (130/150) |

Spread: 1 questions. Verdict agreement across all runs (150 questions judged in every run): 96.7% (145/150); unstable questions: 5 ({'correct/incorrect': 5}).

| b vs a | fixed | broken | net |
|---|---|---|---|
| run 960 vs run 846 | 1 | 2 | -1 |
| run 980 vs run 846 | 1 | 2 | -1 |
| run 980 vs run 960 | 2 | 2 | +0 |

## Judge (same answers, judged again)

| judging | accuracy |
|---|---|
| judge@1 | 87.3% (131/150) |
| judge@1#r2 | 87.3% (131/150) |
| judge@1#r3 | 87.3% (131/150) |

Verdict agreement (150 questions judged in every replicate): 100.0% (150/150); unstable: 0 ({}).

## Determinism

- Retrieved top-k (chunk ids, in order) identical across runs: 73.3% (110/150); differ: 00005, 00206, 00215, 00283, 00382, 00407, 00499, 00517, 00521, 00606, 00651, 00669, 00790, 00859, 01091, 01328, 01346, 01474, 01487, 01488
- Retrieved top-k as a set (order ignored) identical across runs: 92.7% (139/150); differ: 00005, 00215, 00382, 00521, 00669, 01487, 02981, 03069, 04171, 04784, 04980
- Fact route output identical across runs (questions that got facts): 100.0% (78/78)

## Unstable questions

| question | generation verdicts | judge verdicts |
|---|---|---|
| 00005 | correct / incorrect / incorrect | stable |
| 00216 | incorrect / incorrect / correct | stable |
| 00460 | correct / correct / incorrect | stable |
| 00585 | incorrect / correct / incorrect | stable |
| 05718 | correct / incorrect / correct | stable |
