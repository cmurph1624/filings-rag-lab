# Run report: p1-05-structured-rules-rerank vs p2-05-scope-periods vs p2-04-router-rules-facts vs p2-07-decompose-facts

FinanceBench **open subset** (150 questions). These are not full-set FinanceBench results.

|  | run 638 | run 921 | run 846 | run 922 |
|---|---|---|---|---|
| config | p1-05-structured-rules-rerank `920e4b0e0937f9bc` | p2-05-scope-periods `7219809b9845de44` | p2-04-router-rules-facts `b4c2314c3cf43072` | p2-07-decompose-facts `2330af693aad9499` |
| git | `a97724ecae` | `1108393839` | `092d6252dd` | `46bb6b476a` |
| pipeline | structured@1 | structured@1 | structured@1 | structured@1 |
| corpus | fb-distractors@1 | fb-distractors@1 | fb-distractors@1 | fb-distractors@1 |
| retriever | vector k=10 search=exact | vector k=10 search=exact | vector k=10 search=exact | vector k=10 search=exact decompose=True decompose_merge=score |
| scope | rules lookahead=0 | rules lookahead=0 fiscal_labels=financebench forecast_lookback=1 | rules lookahead=0 fiscal_labels=financebench forecast_lookback=1 | rules lookahead=0 fiscal_labels=financebench forecast_lookback=1 |
| rerank | rerank-3 over 50 candidates | rerank-3 over 50 candidates | rerank-3 over 50 candidates | rerank-3 over 50 candidates |
| generation | claude-cli claude-sonnet-5-5 answer@1 | claude-cli claude-sonnet-5-5 answer@3 | claude-cli claude-sonnet-5-5 answer@4 | claude-cli claude-sonnet-5-5 answer@4 |
| judge | claude-cli:claude-opus-5-5:judge@1 | claude-cli:claude-opus-5-5:judge@1 | claude-cli:claude-opus-5-5:judge@1 | claude-cli:claude-opus-5-5:judge@1 |
| answered | 150/150 (0 errors) | 150/150 (0 errors) | 150/150 (0 errors) | 150/150 (0 errors) |

## Answers

Accuracy counts every question in the set; unanswered or unjudged ones are not correct.

|  | run 638 | run 921 | run 846 | run 922 |
|---|---|---|---|---|
| accuracy | 80.7% (121/150) | 80.7% (121/150) | 87.3% (131/150) | 86.0% (129/150) |
| incorrect | 19 | 21 | 18 | 20 |
| refused | 10 | 8 | 1 | 1 |
| not judged | 0 | 0 | 0 | 0 |
| answers citing excerpts | 18.7% (28/150) | 2.0% (3/150) | 58.7% (88/150) | 58.7% (88/150) |
| citation precision | 31.4% (22/70) | 25.0% (1/4) | 27.1% (62/229) | 27.5% (68/247) |

*Citation precision*: the share of excerpts cited in answers (prompts that ask for citations, e.g. answer@2) that hold the question's labelled evidence, over questions with labels. Like recall it is a proxy: an excerpt that repeats the figure also counts as useful to the model but not here.

By question type:

| question type | run 638 | run 921 | run 846 | run 922 |
|---|---|---|---|---|
| domain-relevant | 76.0% (38/50) | 76.0% (38/50) | 80.0% (40/50) | 78.0% (39/50) |
| metrics-generated | 94.0% (47/50) | 92.0% (46/50) | 100.0% (50/50) | 98.0% (49/50) |
| novel-generated | 72.0% (36/50) | 74.0% (37/50) | 82.0% (41/50) | 82.0% (41/50) |

By hand route label (`routes@1`, `corpus/route_labels.csv`):

| subset | run 638 | run 921 | run 846 | run 922 |
|---|---|---|---|---|
| route: fact (63) | 92.1% (58/63) | 88.9% (56/63) | 98.4% (62/63) | 96.8% (61/63) |
| route: mixed (17) | 52.9% (9/17) | 47.1% (8/17) | 58.8% (10/17) | 52.9% (9/17) |
| route: prose (70) | 77.1% (54/70) | 81.4% (57/70) | 84.3% (59/70) | 84.3% (59/70) |
| multi-period (50) | 80.0% (40/50) | 78.0% (39/50) | 88.0% (44/50) | 88.0% (44/50) |
| single period (100) | 81.0% (81/100) | 82.0% (82/100) | 87.0% (87/100) | 85.0% (85/100) |

## Retrieval

Against FinanceBench evidence passages mapped to the pipeline's chunks. recall@k: share of a question's evidence passages found in the top k. hit@k: any passage found. These are a proxy: an answer can also come from a passage that repeats the same figure, and a partial overlap can count as a hit (see docs/results/p0-label-audit.md).

|  | run 638 | run 921 | run 846 | run 922 |
|---|---|---|---|---|
| recall@1 | 0.450 | 0.460 | 0.470 | 0.460 |
| recall@3 | 0.652 | 0.669 | 0.672 | 0.669 |
| recall@5 | 0.740 | 0.774 | 0.774 | 0.777 |
| recall@10 | 0.776 | 0.796 | 0.796 | 0.810 |
| hit@10 | 0.832 | 0.852 | 0.852 | 0.859 |
| MRR | 0.623 | 0.637 | 0.643 | 0.635 |
| questions scored | 149 (1 without labels) | 149 (1 without labels) | 149 (1 without labels) | 149 (1 without labels) |
| rerank cost (list) | $0.1682 | $0.1684 | $0.1684 | $0.2296 |

## Scoping

How each question's search was narrowed before retrieval. *Filings*: the company's filings for the periods the question names. *Latest year*: the question names no period, so the company's filings from its latest labelled year (`no_year = 'latest'`). *Company*: no filing matched the periods, so all of the company's filings (a fallback). *Whole corpus*: no company found. *Gold filing in scope*: the question's own filing was searchable (always true for the whole corpus). Oracle scope searches only the gold filing: an upper bound, not a result.

|  | run 638 | run 921 | run 846 | run 922 |
|---|---|---|---|---|
| narrowed to filings | 92.7% (139/150) | 92.7% (139/150) | 92.7% (139/150) | 92.7% (139/150) |
| no period named: latest year | 0.0% (0/150) | 0.0% (0/150) | 0.0% (0/150) | 0.0% (0/150) |
| fell back to company | 5.3% (8/150) | 5.3% (8/150) | 5.3% (8/150) | 5.3% (8/150) |
| whole corpus | 2.0% (3/150) | 2.0% (3/150) | 2.0% (3/150) | 2.0% (3/150) |
| oracle | 0.0% (0/150) | 0.0% (0/150) | 0.0% (0/150) | 0.0% (0/150) |
| gold filing in scope | 98.0% (147/150) | 99.3% (149/150) | 99.3% (149/150) | 99.3% (149/150) |
| filings in scope, median | 2 | 2 | 2 | 2 |
| scope model cost (plain API) | $0.0000 | $0.0000 | $0.0000 | $0.0000 |

## Cost

USD at list prices (`config/prices.toml`, as of 2026-10-29), whoever paid. *As recorded* prices the tokens the backend actually used: `claude -p` writes each new prompt to the 1-hour cache at 2× input and reads repeats from it, so this figure depends on what earlier runs left in the cache. *Plain API* is the same tokens as an uncached API call and *Batch API* is that at the batch discount; these two are comparable across runs and backends. Answering cost is generation, query embedding, any reranker and any scope model (counted on every run, even when its result came from the cache, so runs compare as deployed systems); the judge is eval overhead and is shown on its own.

|  | run 638 | run 921 | run 846 | run 922 |
|---|---|---|---|---|
| as recorded: answering, total | $5.43 | $5.52 | $5.65 | $5.76 |
| as recorded: per question | $0.0362 | $0.0368 | $0.0377 | $0.0384 |
| as recorded: per correct answer | $0.0449 | $0.0456 | $0.0431 | $0.0447 |
| as recorded: judge, total | $1.84 | $1.82 | $1.84 | $1.83 |
| plain API: answering, total | $3.04 | $3.05 | $3.11 | $3.20 |
| plain API: per question | $0.0203 | $0.0203 | $0.0207 | $0.0213 |
| plain API: per correct answer | $0.0251 | $0.0252 | $0.0238 | $0.0248 |
| plain API: judge, total | $1.11 | $1.10 | $1.12 | $1.11 |
| Batch API: answering, total | $1.60 | $1.61 | $1.64 | $1.71 |
| Batch API: per question | $0.0107 | $0.0107 | $0.0109 | $0.0114 |
| Batch API: per correct answer | $0.0133 | $0.0133 | $0.0125 | $0.0133 |
| Batch API: judge, total | $0.5573 | $0.5493 | $0.5610 | $0.5542 |

## Latency

|  | run 638 | run 921 | run 846 | run 922 |
|---|---|---|---|---|
| retrieval p50 | 0.6s | 0.6s | 0.6s | 0.7s |
| retrieval p95 | 0.8s | 0.8s | 0.8s | 1.2s |
| generation p50 | 3.4s | 4.2s | 3.4s | 3.5s |
| generation p95 | 5.8s | 8.0s | 5.9s | 5.8s |
