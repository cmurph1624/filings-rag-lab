# Run report: p1-00-naive-distractors vs p1-01-scope-rules vs p1-03-rules-rerank vs p1-04-tables-rules-rerank vs p1-05-structured-rules-rerank vs p1-05-structured-rules-rerank-cite

FinanceBench **open subset** (150 questions). These are not full-set FinanceBench results.

|  | run 136 | run 635 | run 636 | run 637 | run 638 | run 616 |
|---|---|---|---|---|---|---|
| config | p1-00-naive-distractors `81aa51a38da499c9` | p1-01-scope-rules `19ec53fb7c40d260` | p1-03-rules-rerank `8fb5fc7756539906` | p1-04-tables-rules-rerank `cae6dc1a4fd32666` | p1-05-structured-rules-rerank `920e4b0e0937f9bc` | p1-05-structured-rules-rerank-cite `5212d3c7595a99d9` |
| git | `98b14f0ab6` | `a97724ecae` | `a97724ecae` | `a97724ecae` | `a97724ecae` | `0d0449f2ec` |
| pipeline | naive-fixed@1 | naive-fixed@1 | naive-fixed@1 | table-preserving@1 | structured@1 | structured@1 |
| corpus | fb-distractors@1 | fb-distractors@1 | fb-distractors@1 | fb-distractors@1 | fb-distractors@1 | fb-distractors@1 |
| retriever | vector k=10 search=exact | vector k=10 search=exact | vector k=10 search=exact | vector k=10 search=exact | vector k=10 search=exact | vector k=10 search=exact |
| scope | none | rules lookahead=0 | rules lookahead=0 | rules lookahead=0 | rules lookahead=0 | rules lookahead=0 |
| rerank | none | none | rerank-3 over 50 candidates | rerank-3 over 50 candidates | rerank-3 over 50 candidates | rerank-3 over 50 candidates |
| generation | claude-cli claude-sonnet-5-5 answer@1 | claude-cli claude-sonnet-5-5 answer@1 | claude-cli claude-sonnet-5-5 answer@1 | claude-cli claude-sonnet-5-5 answer@1 | claude-cli claude-sonnet-5-5 answer@1 | claude-cli claude-sonnet-5-5 answer@2 |
| judge | claude-cli:claude-opus-5-5:judge@1 | claude-cli:claude-opus-5-5:judge@1 | claude-cli:claude-opus-5-5:judge@1 | claude-cli:claude-opus-5-5:judge@1 | claude-cli:claude-opus-5-5:judge@1 | claude-cli:claude-opus-5-5:judge@1 |
| answered | 150/150 (0 errors) | 150/150 (0 errors) | 150/150 (0 errors) | 150/150 (0 errors) | 150/150 (0 errors) | 150/150 (0 errors) |

## Answers

Accuracy counts every question in the set; unanswered or unjudged ones are not correct.

|  | run 136 | run 635 | run 636 | run 637 | run 638 | run 616 |
|---|---|---|---|---|---|---|
| accuracy | 60.7% (91/150) | 67.3% (101/150) | 78.0% (117/150) | 74.7% (112/150) | 80.7% (121/150) | 80.0% (120/150) |
| incorrect | 20 | 27 | 25 | 26 | 19 | 22 |
| refused | 39 | 22 | 8 | 12 | 10 | 8 |
| not judged | 0 | 0 | 0 | 0 | 0 | 0 |
| answers citing excerpts | 32.7% (49/150) | 18.0% (27/150) | 13.3% (20/150) | 17.3% (26/150) | 18.7% (28/150) | 100.0% (150/150) |
| citation precision | 38.9% (37/95) | 38.8% (26/67) | 31.5% (17/54) | 31.9% (23/72) | 31.4% (22/70) | 28.0% (135/482) |

*Citation precision*: the share of excerpts cited in answers (prompts that ask for citations, e.g. answer@2) that hold the question's labelled evidence, over questions with labels. Like recall it is a proxy: an excerpt that repeats the figure also counts as useful to the model but not here.

By question type:

| question type | run 136 | run 635 | run 636 | run 637 | run 638 | run 616 |
|---|---|---|---|---|---|---|
| domain-relevant | 54.0% (27/50) | 64.0% (32/50) | 78.0% (39/50) | 74.0% (37/50) | 76.0% (38/50) | 76.0% (38/50) |
| metrics-generated | 70.0% (35/50) | 80.0% (40/50) | 90.0% (45/50) | 84.0% (42/50) | 94.0% (47/50) | 92.0% (46/50) |
| novel-generated | 58.0% (29/50) | 58.0% (29/50) | 66.0% (33/50) | 66.0% (33/50) | 72.0% (36/50) | 72.0% (36/50) |

## Retrieval

Against FinanceBench evidence passages mapped to the pipeline's chunks. recall@k: share of a question's evidence passages found in the top k. hit@k: any passage found. These are a proxy: an answer can also come from a passage that repeats the same figure, and a partial overlap can count as a hit (see docs/results/p0-label-audit.md).

|  | run 136 | run 635 | run 636 | run 637 | run 638 | run 616 |
|---|---|---|---|---|---|---|
| recall@1 | 0.195 | 0.232 | 0.470 | 0.482 | 0.450 | 0.450 |
| recall@3 | 0.312 | 0.379 | 0.704 | 0.653 | 0.652 | 0.652 |
| recall@5 | 0.396 | 0.532 | 0.773 | 0.737 | 0.740 | 0.740 |
| recall@10 | 0.527 | 0.663 | 0.815 | 0.784 | 0.776 | 0.776 |
| hit@10 | 0.584 | 0.705 | 0.839 | 0.832 | 0.832 | 0.832 |
| MRR | 0.318 | 0.392 | 0.646 | 0.654 | 0.623 | 0.623 |
| questions scored | 149 (1 without labels) | 149 (1 without labels) | 149 (1 without labels) | 149 (1 without labels) | 149 (1 without labels) | 149 (1 without labels) |
| rerank cost (list) | – | – | $0.2288 | $0.1920 | $0.1682 | $0.1682 |

## Scoping

How each question's search was narrowed before retrieval. *Filings*: the company's filings for the periods the question names. *Company*: no filing matched the periods, so all of the company's filings (a fallback). *Whole corpus*: no company found. *Gold filing in scope*: the question's own filing was searchable (always true for the whole corpus). Oracle scope searches only the gold filing: an upper bound, not a result.

|  | run 136 | run 635 | run 636 | run 637 | run 638 | run 616 |
|---|---|---|---|---|---|---|
| narrowed to filings | – | 92.7% (139/150) | 92.7% (139/150) | 92.7% (139/150) | 92.7% (139/150) | 92.7% (139/150) |
| fell back to company | – | 5.3% (8/150) | 5.3% (8/150) | 5.3% (8/150) | 5.3% (8/150) | 5.3% (8/150) |
| whole corpus | – | 2.0% (3/150) | 2.0% (3/150) | 2.0% (3/150) | 2.0% (3/150) | 2.0% (3/150) |
| oracle | – | 0.0% (0/150) | 0.0% (0/150) | 0.0% (0/150) | 0.0% (0/150) | 0.0% (0/150) |
| gold filing in scope | – | 98.0% (147/150) | 98.0% (147/150) | 98.0% (147/150) | 98.0% (147/150) | 98.0% (147/150) |
| filings in scope, median | – | 2 | 2 | 2 | 2 | 2 |
| scope model cost (plain API) | – | $0.0000 | $0.0000 | $0.0000 | $0.0000 | $0.0000 |

## Cost

USD at list prices (`config/prices.toml`, as of 2026-10-29), whoever paid. *As recorded* prices the tokens the backend actually used: `claude -p` writes each new prompt to the 1-hour cache at 2× input and reads repeats from it, so this figure depends on what earlier runs left in the cache. *Plain API* is the same tokens as an uncached API call and *Batch API* is that at the batch discount; these two are comparable across runs and backends. Answering cost is generation, query embedding, any reranker and any scope model (counted on every run, even when its result came from the cache, so runs compare as deployed systems); the judge is eval overhead and is shown on its own.

|  | run 136 | run 635 | run 636 | run 637 | run 638 | run 616 |
|---|---|---|---|---|---|---|
| as recorded: answering, total | $5.90 | $5.92 | $6.19 | $5.72 | $5.43 | $5.70 |
| as recorded: per question | $0.0393 | $0.0394 | $0.0412 | $0.0381 | $0.0362 | $0.0380 |
| as recorded: per correct answer | $0.0648 | $0.0586 | $0.0529 | $0.0510 | $0.0449 | $0.0475 |
| as recorded: judge, total | $1.85 | $1.90 | $1.88 | $1.85 | $1.84 | $1.86 |
| plain API: answering, total | $3.19 | $3.20 | $3.45 | $3.20 | $3.04 | $3.19 |
| plain API: per question | $0.0213 | $0.0214 | $0.0230 | $0.0213 | $0.0203 | $0.0213 |
| plain API: per correct answer | $0.0351 | $0.0317 | $0.0295 | $0.0285 | $0.0251 | $0.0266 |
| plain API: judge, total | $1.13 | $1.18 | $1.16 | $1.12 | $1.11 | $1.13 |
| Batch API: answering, total | $1.60 | $1.60 | $1.84 | $1.69 | $1.60 | $1.68 |
| Batch API: per question | $0.0107 | $0.0107 | $0.0123 | $0.0113 | $0.0107 | $0.0112 |
| Batch API: per correct answer | $0.0176 | $0.0159 | $0.0157 | $0.0151 | $0.0133 | $0.0140 |
| Batch API: judge, total | $0.5641 | $0.5905 | $0.5800 | $0.5616 | $0.5573 | $0.5673 |

## Latency

|  | run 136 | run 635 | run 636 | run 637 | run 638 | run 616 |
|---|---|---|---|---|---|---|
| retrieval p50 | 0.6s | 0.3s | 0.7s | 0.6s | 0.6s | 0.6s |
| retrieval p95 | 0.7s | 0.4s | 1.0s | 0.9s | 0.8s | 0.8s |
| generation p50 | 4.7s | 5.0s | 4.6s | 3.6s | 3.4s | 3.4s |
| generation p95 | 9.4s | 10.1s | 9.2s | 6.1s | 5.8s | 6.5s |
