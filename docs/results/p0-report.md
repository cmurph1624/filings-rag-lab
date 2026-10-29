# Run report: p0-naive

FinanceBench **open subset** (150 questions). These are not full-set FinanceBench results.

|  | run 60 |
|---|---|
| config | p0-naive `6cc9a6708ce5ab47` |
| git | `62a3cdc7d8` |
| pipeline | naive-fixed@1 |
| retriever | vector k=10 search=exact |
| generation | claude-cli claude-sonnet-5-5 answer@1 |
| judge | claude-cli:claude-opus-5-5:judge@1 |
| answered | 150/150 (0 errors) |

## Answers

Accuracy counts every question in the set; unanswered or unjudged ones are not correct.

|  | run 60 |
|---|---|
| accuracy | 66.7% (100/150) |
| incorrect | 27 |
| refused | 23 |
| not judged | 0 |

By question type:

| question type | run 60 |
|---|---|
| domain-relevant | 64.0% (32/50) |
| metrics-generated | 76.0% (38/50) |
| novel-generated | 60.0% (30/50) |

## Retrieval

Against FinanceBench evidence passages mapped to the pipeline's chunks. recall@k: share of a question's evidence passages found in the top k. hit@k: any passage found.

|  | run 60 |
|---|---|
| recall@1 | 0.262 |
| recall@3 | 0.403 |
| recall@5 | 0.490 |
| recall@10 | 0.670 |
| hit@10 | 0.718 |
| MRR | 0.412 |
| questions scored | 149 (1 without labels) |

## Cost

USD at list prices (`config/prices.toml`, as of 2026-10-29), whoever paid. *As recorded* prices the tokens the backend actually used: `claude -p` writes each new prompt to the 1-hour cache at 2× input and reads repeats from it, so this figure depends on what earlier runs left in the cache. *Plain API* is the same tokens as an uncached API call and *Batch API* is that at the batch discount; these two are comparable across runs and backends. Answering cost is generation plus query embedding; the judge is eval overhead and is shown on its own.

|  | run 60 |
|---|---|
| as recorded: answering, total | $5.75 |
| as recorded: per question | $0.0383 |
| as recorded: per correct answer | $0.0575 |
| as recorded: judge, total | $1.86 |
| plain API: answering, total | $3.19 |
| plain API: per question | $0.0213 |
| plain API: per correct answer | $0.0319 |
| plain API: judge, total | $1.13 |
| Batch API: answering, total | $1.60 |
| Batch API: per question | $0.0106 |
| Batch API: per correct answer | $0.0160 |
| Batch API: judge, total | $0.5675 |

## Latency

|  | run 60 |
|---|---|
| retrieval p50 | 0.2s |
| retrieval p95 | 0.4s |
| generation p50 | 4.4s |
| generation p95 | 12.2s |
