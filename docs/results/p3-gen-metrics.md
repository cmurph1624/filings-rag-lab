# P3 generation metrics: fiscal period and faithfulness

FinanceBench open subset (150 questions), distractor corpus. Two new per-answer metrics,
back-filled on three runs:

- run 846: `p2-04-router-rules-facts`, answer@4, the P2 best;
- run 638: `p1-05-structured-rules-rerank`, answer@1, the P1 best;
- run 922: `p2-07-decompose-facts`, answer@4 with query decomposition.

All three use Sonnet 5.5 (`claude-cli`) to answer and Opus 5.5 to judge. Reproduce the table
with `frl faithful <run>` for each run, then `frl report 846 --vs 638 --vs 922`.

## What the metrics are

- **Fiscal period** (`eval/periods.py`, deterministic, no model calls) checks whether the
  answer states the period the question is about. That period is the latest of the question's
  hand-labelled `routes@1` periods, and any of its forms counts:
  - the FinanceBench label year;
  - the company's own name for the year (Ulta's `label_offset`);
  - a date inside the period or within a few days of its end;
  - for a quarter, that quarter or its fiscal year.

  *Wrong* means the answer states periods, but not that one. *None* means it states no period.
  Answers judged refused are left out.
- **Faithfulness** (`faithful@1`, using the run's judge model) lists each checkable claim in
  an answer. It labels each claim *supported*, *unsupported* or *contradicted* against the
  context the generator was given. That context is the Facts block and the retrieved excerpts,
  rebuilt from the run record exactly as the generator saw them. The judge never sees the gold
  answer, so this metric is independent of `judge@1` correctness. It is stored per answer in
  the `faithfulness` table (migration 013).

## Results

|  | run 846 | run 638 | run 922 |
|---|---|---|---|
| accuracy (judge@1) | 87.3% (131/150) | 80.7% (121/150) | 86.0% (129/150) |
| period: right | 98.7% (147/149) | 98.6% (138/140) | 98.7% (147/149) |
| period: wrong | 0.7% (1/149) | 1.4% (2/140) | 1.3% (2/149) |
| period: none stated | 0.7% (1/149) | 0.0% (0/140) | 0.0% (0/149) |
| claims supported | 97.5% (1298/1331) | 97.9% (1237/1263) | 97.2% (1309/1347) |
| claims unsupported | 27 | 18 | 29 |
| claims contradicted | 6 | 8 | 9 |
| answers with a contradicted claim | 4.0% (6/150) | 5.3% (8/150) | 6.0% (9/150) |
| answers fully supported | 83.3% (125/150) | 87.3% (131/150) | 80.0% (120/150) |

Faithfulness against judge@1 verdicts:

| answer's worst claim | run 846 correct / incorrect | run 638 | run 922 |
|---|---|---|---|
| all supported | 112 / 12 | 107 / 15 | 110 / 10 |
| some unsupported | 14 / 5 | 6 / 4 | 11 / 9 |
| some contradicted | 5 / 1 | 8 / 0 | 8 / 1 |

Refusals are left out: 1, 10 and 1 answers.

## Findings

- **Period is almost never the problem.** The answer names the right period in about 99% of
  answered questions in every run. The misses are:
  - 01148: Amcor, answered from the FY2021 10-K;
  - 02416: Pfizer, answered with the wrong years;
  - one 846 answer that names no period.

  Scope and the Facts block already pin the period. A model-based period check isn't needed.
- **Wrong answers are mostly faithful.** In run 846, 12 of 18 incorrect answers are fully
  supported by their context, and only 1 contradicts it. The generator reads what it was
  given. Errors come from the context (a missing or wrong passage) or from reasoning over it,
  not from figures invented against it. Improving retrieval and context matters more than
  grounding prompts.
- **Most unfaithful claims are on correct answers.** Unsupported claims are mostly outside
  knowledge: background facts the excerpts don't state. Contradicted claims are a mix of rounding
  (1.43% for 1.42%) and side figures read from the wrong column or year. They are usually not
  the headline figure, so the correctness judge passes most of these answers.
- **answer@4 adds claims, not hallucination.** answer@1 (run 638) refuses 10 questions and
  writes shorter answers, so it has fewer unsupported claims (18 against 27). Decomposition
  (run 922) retrieves more and has slightly more unsupported and contradicted claims. The
  differences are a handful of claims, which is within what one would expect from noise at
  this size (see `p3-noise.md`).

## Hand checks

**Period: 20 verdicts, all correct.** The sample was every *wrong* or *none* verdict plus a
spread of *right* ones across fact, prose, quarter and Ulta-offset questions. Building the
check fixed four false alarms:
- 8-K event dates inside the year;
- quarter questions answered with the fiscal year;
- refusals;
- the prior quarter's end date counting as inside the period.

**Faithfulness: 20 answers from run 846.**

| label | checked | agree | false alarm |
|---|---|---|---|
| contradicted | 6 claims (6 answers) | 5 (one borderline: 1.915% vs 1.914%) | 1 |
| unsupported | 9 claims (7 answers) | 8 | 1 |
| all supported | 7 answers | 7 | 0 |

The contradicted false alarm is 00494: the excerpt does state 737 production at 31 per month.
The unsupported false alarm is 00070: the excerpts do mention commercial paper and revolver
financing. The confirmed contradictions are:
- 00394: the answer contradicts itself;
- 00585: the $587M audit settlement is in the 2020 column;
- 00591: the change in deferred revenue was smaller in 2022;
- 03473: 1.43% vs 1.42%.

No fully supported answer hid an unsupported figure. The judge errs slightly toward strict:
about 1 in 8 flags is a false alarm, and no missed flags were found.

## Limits

- Period: an answer that names the right year next to a figure from another year still counts
  as right. For Ulta-style companies, a bare year is ambiguous between two labels.
- Faithfulness uses one judge sample per answer, and the judge is the same model family as the
  generator. The hand check covers 20 answers, all from one run.
- Cost: about $12 per run API-equivalent (Opus 5.5 over the full context), roughly 10× the
  judge@1 cost, because each call carries all the excerpts. It runs on demand
  (`frl faithful`), not in the CI gate.
