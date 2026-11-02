# P3 backends: the `openai-compatible` backend and the two new models

FinanceBench open subset (150 questions). This is plumbing for PR 7's multi-model comparison;
it has no accuracy numbers.

## The backend

`backends/openai_compat.py` speaks OpenAI chat completions, so one class serves OpenAI and an
open model on vLLM. Config (`[generation]`):

| key | meaning |
|---|---|
| `backend = "openai-compatible"`, `model` | the model name sent in the request |
| `base_url` | default `https://api.openai.com/v1`; for the pod, a tunnel to vLLM |
| `api_key_env` | env var holding the key (default `OPENAI_API_KEY`); unset means no key is sent |
| `effort` | sent as `reasoning_effort` |
| `max_tokens` | sent as `max_completion_tokens` (default 16,000) |
| `extra_body` | merged into the request, e.g. Qwen's `chat_template_kwargs.enable_thinking` |
| `gpu_usd_per_hour` | not sent anywhere; the report's GPU-hour cost view (below) |

Retries on 408/409/429/5xx and transport errors with exponential backoff or the server's
`Retry-After`; any other error fails the call. Cached prompt tokens are recorded as
`cache_read_tokens` and the rest of the prompt as `input_tokens`. A `refusal` or a
`content_filter` finish is recorded as a refusal. A 200 reply that isn't JSON (a proxy's error page) is retried like a 5xx. The
recorded model is the server's name when it is the configured model or a dated snapshot of it,
and the configured name otherwise, so a vLLM path or `--served-model-name` can't break pricing.

## Models and prices (checked 2026-11-02)

- **OpenAI: `gpt-6.1-sol`**, $2.00 input / $0.10 cached input / $10.00 output per million
  tokens (standard tier, prompts up to 272K; developers.openai.com/api/docs/pricing). The API
  answers with a dated snapshot name; `pricing.price_key` prices it as `gpt-6.1-sol`. The same
  page lists `gpt-6.1-terra` ($1 / $0.05 / $5) below it and `gpt-6-astra` ($10 / $1 / $50)
  above it. The page doesn't label tiers, so "mid-tier" is the plan's wording, not OpenAI's.
- **Open: `Qwen/Qwen3.6-27B`**, a 27B dense post-trained model, Apache-2.0, 262K context,
  thinking on by default (huggingface.co/Qwen/Qwen3.6-27B). The model card names no 4-bit
  build, so the quantised repo for the 4090 is picked at the smoke run. The card's vLLM
  example uses `--reasoning-parser qwen3`, and Qwen's thinking can be switched off with
  `extra_body`. Which of the two PR 7 compares is a method decision for PR 7.

## GPU-hour cost view

A self-hosted model has no token price, so `prices.toml` lists it at $0 and the token views
(as recorded, plain API, Batch API) read $0 for it. `frl report` adds *GPU-hour* rows when a
run's config sets `gpu_usd_per_hour`: wall time from the run's start to its finish times the
rate, plus the other models (embedding, reranker, scope) at plain API cost. A run resumed after
a pause includes the gap, so it assumes the pod stayed rented.

## Smoke run: Qwen on a rented 4090 (2026-11-02, run 1380)

`configs/p3-smoke-qwen.toml`, the first 5 questions (`frl run ... --limit 5`), then `frl judge`.
Plumbing check, not a result.

- **It works end to end.** 5 answered, 0 failed, generation 8–18 s per question (60.6 s in
  total at concurrency 2, 681 output tokens), judged and reported. Token views cost it at $0 as
  designed.
- **Answers match the Sonnet run (846) on 4 of 5.** The difference is 00005 (Corning working
  capital), the question `p3-noise.md` already found unstable because its gold is debatable.
- **What the pod cost:** one RTX 4090 at $0.89/hr (RunPod Secure Cloud; the lab README's $0.74
  is out of date) for about 40 minutes, roughly $0.60. Most of that was getting a server up,
  not answering.
- **GPU-hour rows read "–" for this run.** `runs.finished_at` is only set when a run covers the
  whole question set, and `--limit 5` doesn't. PR 7's full runs get the rows; a partial run
  can't.

### Getting Qwen3.6-27B onto a 24 GB card (for PR 7)

- **RunPod had no 4090 in EU-RO-1 at the first try**, and it was free on the second, ten
  minutes later. The volume pins the data center, so there is no other place to look.
- **Pick the build by its safetensors size, not its model card.** `88plug/Qwen3.6-27B-W4A16`
  says "14 GB" and "fits 24 GB"; it is 25.9 GB and ran out of memory while building the LM head
  (vLLM 0.33.0). The 4-bit builds of this model are 18.7–22 GB: `Alennnndy/Qwen3.6-27B-AWQ-4bit`
  (18.7), `Lorbus/Qwen3.6-27B-int4-AutoRound` (19.0, needs `--trust-remote-code`),
  `cyankiwi/Qwen3.6-27B-AWQ-INT4` (20.4), `QuantTrio/Qwen3.6-27B-AWQ` (21.9).
- **The one that served:** `Alennnndy/Qwen3.6-27B-AWQ-4bit`, with
  `VLLM_MAX_MODEL_LEN=24576 VLLM_DOWNLOAD_DIR=/root/hf VLLM_EXTRA_ARGS="--language-model-only
  --kv-cache-dtype fp8 --enforce-eager --max-num-seqs 2 --gpu-memory-utilization 0.95
  --enable-prefix-caching --reasoning-parser qwen3" lab up --serve vllm --model
  Alennnndy/Qwen3.6-27B-AWQ-4bit`. It was serving about 4 minutes after launch, download
  included. `--max-num-seqs 2` means PR 7's `concurrency` should be 2 for this model.
- **Weights go on the container disk** (`VLLM_DOWNLOAD_DIR=/root/hf`): the 50 GB network volume
  already holds 28 GB of Ollama models and 15 GB of Qwen2.5-7B, and the download died with
  `Disk quota exceeded`. The container disk is wiped on `lab down`, so each pod re-downloads
  18.7 GB (a few minutes).
- **The served name is the HF id's basename** (`Qwen3.6-27B-AWQ-4bit`), so the run config's
  `model` and a `prices.toml` entry use that name.
- **The server crashed silently into `/workspace/vllm.log`** each time; `lab up` reported
  success because the bootstrap only starts it. Read the log (`ssh ... tail /workspace/vllm.log`)
  rather than waiting.

### Still not done

The OpenAI smoke run (`configs/p3-smoke-openai.toml`): there is no `OPENAI_API_KEY` in `.env`,
and it was skipped on 2026-11-02 at the user's request.
