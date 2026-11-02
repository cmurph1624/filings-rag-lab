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

## Smoke runs: not done in this PR

`configs/p3-smoke-openai.toml` and `configs/p3-smoke-qwen.toml` are the 5-question smoke
configs (`frl run <config> --only <file>`). They need an `OPENAI_API_KEY` (not in `.env`) and a
rented pod (`lab up --serve vllm`, then `lab down`), which costs money, so they wait for an OK.
