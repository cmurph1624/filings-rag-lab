# P3 PR 7: the same pipeline with a different answering model

FinanceBench **open subset (150 questions)**. These are not full-set FinanceBench results.

Every run uses run 846's configuration (structured@1 corpus, vector k=10, rules scope, rerank-3 over 50, hand-tuned
router, `answer@4`) and swaps only `[generation]`. Every run is judged by the same pinned judge
(`claude-cli:claude-opus-5-5:judge@1`). Report: `frl report 846 --vs 1401 --vs 1443 --vs 1442`.
Configs: `configs/p3-models-{haiku,opus,openai,qwen}.toml`.

| model | run | config |
|---|---|---|
| Claude Sonnet 5.5 (baseline) | 846 | `p2-04-router-rules-facts` |
| Claude Haiku 5.5 | 1401 | `p3-models-haiku` |
| Claude Opus 5.5 | 1443 | `p3-models-opus` |
| Qwen3.6-27B, 4-bit AWQ, self-hosted | 1442 | `p3-models-qwen` |
| `gpt-6.1-sol` (OpenAI) | **not run** | `p3-models-openai` |

**Gap: OpenAI.** No `OPENAI_API_KEY` was available, and the decision was to run without it. The config and backend
exist and are priced (`config/prices.toml`), so the row can be filled by `frl run configs/p3-models-openai.toml`
and `frl judge`, but there is no number for it here.

## Accuracy

| | Sonnet 846 | Haiku 1401 | Opus 1443 | Qwen 1442 |
|---|---|---|---|---|
| accuracy | 87.3% (131/150) | 84.0% (126/150) | 84.0% (126/150) | 82.7% (124/150) |
| route: fact (63) | 98.4% | 96.8% | 95.2% | 95.2% |
| route: mixed (17) | 58.8% | 52.9% | 47.1% | 52.9% |
| route: prose (70) | 84.3% | 80.0% | 82.9% | 78.6% |
| refused | 1 | 1 | 1 | 4 |
| fixed vs Sonnet | – | 1 | 0 | 1 |
| broken vs Sonnet | – | 6 | 5 | 8 |
| McNemar p (exact, two-sided) | – | 0.125 | 0.062 | 0.039 |

Routes are the hand labels (`routes@1`). "Fixed" means Sonnet was not correct and the model was; "broken" is the
reverse; unjudged, unanswered and refused count as not correct.

- Sonnet 5.5 is the best of the four on this pipeline, and the larger Opus does not beat it. Only Qwen's gap
  clears p < 0.05 (8 broken against 1 fixed); with 150 questions and 5–9 disagreements, Haiku and Opus differences
  from Sonnet are not distinguishable from noise.
- The whole spread is 7 questions (87.3% to 82.7%). The pipeline (retrieval, routing, facts) sets most of the result;
  the generator is a small lever once the evidence is in the prompt.
- Mixed-route questions are the weak spot for every model (47–59%).
- Fiscal period stated correctly: 98.7% (Sonnet), 98.0% (Haiku), 98.7% (Opus), 95.2% (Qwen, 139/146 non-refused).
  Qwen gave no period in 4 answers.
- Faithfulness (`frl faithful`) was run only for 846 (PR 5); it was not run for the other models.
- Not verified: why the share of answers citing excerpts differs (Sonnet 58.7%, Haiku 88%, Opus 100%, Qwen 81%) under
  the same prompt.

## Cost

USD at list prices (`config/prices.toml`). Answering only; the judge is eval overhead (about $1.08–1.12 plain API per
run). Plain and Batch views are comparable across runs; "as recorded" depends on the `claude -p` cache and is in the
report only.

| | Sonnet 846 | Haiku 1401 | Opus 1443 | Qwen 1442 |
|---|---|---|---|---|
| plain API: total | $3.11 | $0.344 | $6.45 | $0.169 (non-generation only) |
| plain API: per correct | $0.0238 | $0.0027 | $0.0512 | – |
| Batch API: total | $1.64 | $0.257 | $3.31 | $0.169 (non-generation only) |
| Batch API: per correct | $0.0125 | $0.0020 | $0.0263 | – |
| GPU-hour: total | – | – | – | $0.416 |
| GPU-hour: per correct | – | – | – | $0.0034 |

Qwen has no token price: its token cost is $0, so the $0.169 is retrieval (embedding and rerank) only, the same
figure every run pays. Its real cost is the GPU view: $0.89/h × 0.28 h wall = $0.42 including those retrieval costs,
or $0.0034 per correct answer. That is the cost of the run with the pod already up and busy; it omits pod start-up, the model download and the
earlier failed loads and smoke run. Per correct answer, Haiku is the
cheapest API option (about 9× cheaper than Sonnet, 3 points less accurate), and Opus costs about twice Sonnet for
3 points less accuracy.

## Latency (generation, per answer)

| | Sonnet 846 | Haiku 1401 | Opus 1443 | Qwen 1442 |
|---|---|---|---|---|
| p50 | 3.4 s | 4.1 s | 5.6 s | 11.9 s |
| p95 | 5.9 s | 7.4 s | 8.9 s | 23.8 s |

Claude runs go through `claude -p` and the usage governor (one call at a time). Qwen ran at concurrency 2 on one
24 GB GPU with `--enforce-eager` and fp8 KV cache, so its latency reflects that card, not the model's best case.

## How Qwen was served

Served from Open Model Lab for the length of the run, then torn down (`lab down`).

- Model: `Alennnndy/Qwen3.6-27B-AWQ-4bit` (18.7 GB), served name `Qwen3.6-27B-AWQ-4bit`, 4-bit AWQ.
- GPU: one RTX 4090 (24 GB) on RunPod, $0.89/h.
- vLLM 0.33.0: `--max-model-len 24576 --language-model-only --kv-cache-dtype fp8 --enforce-eager --max-num-seqs 2
  --gpu-memory-utilization 0.95 --enable-prefix-caching --reasoning-parser qwen3`.
- Request: `enable_thinking = false` via `chat_template_kwargs`, `max_tokens = 4000`, client concurrency 2.
- This is a community 4-bit build, not an official release, downloaded to the container disk. A 4-bit quantization may cost accuracy relative to the full-precision model; this run does not separate that from
  model size.

Details and the failed builds are in `p3-backends.md`.

## Reading

On this pipeline the answering model matters less than expected: Sonnet 5.5 leads, Opus 5.5 and Haiku 5.5 tie
at 84.0%, and a 4-bit open model on a rented consumer GPU is 4.6 points behind at about 27% of Sonnet's batch cost
per correct answer ($0.0034 against $0.0125), with a significant McNemar result against it. The OpenAI row is missing.
