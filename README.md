# Filings RAG Lab

Open-source research assistant over SEC EDGAR filings (10-K, 10-Q, 8-K), scored publicly against
[FinanceBench](https://github.com/patronus-ai/financebench) (open subset, 150 questions).

Retrieval is the focus: metadata-scoped vector retrieval in Postgres + pgvector with a reranker. An
experiment harness records every run (pipeline, retriever, model, judge) with tokens and
API-equivalent cost. CI runs lint, type checks and tests on every PR.

Not finished yet: a public deployment on AWS (Terraform-provisioned EC2, P4). Everything runs
locally for now.

## Status

**P0 done: baseline on the board.** `naive-fixed@1` with vector top-10 and Sonnet 5.5 scores
**66.7% (100/150)** on the FinanceBench **open subset** (not the full set), at $0.032 per correct
answer at plain API rates ($0.016 with the Batch API). Accuracy is 81% when retrieval finds the
evidence and 45% when it doesn't. See [`docs/results/p0-baseline.md`](docs/results/p0-baseline.md)
for the failure analysis.

**P1 done: retrieval.** On a harder corpus with 283 distractor filings, the naive baseline drops
to 60.7%. Rules-based company/period scoping plus a Voyage reranker lifts it to 78.0%, and the
`structured@1` pipeline (context headers on each chunk) reaches **80.7% (121/150)** on the open
subset, at $0.025 per correct answer at plain API rates ($0.013 Batch). Hybrid BM25 didn't help.
See [`docs/results/p1-retrieval.md`](docs/results/p1-retrieval.md).

**P2 done: routing.** XBRL facts (SEC `companyfacts`) and a metrics library answer fact
questions deterministically. A router sends them to SQL-computed facts and keeps prose on
retrieval. That lifts the open subset to **87.3% (131/150)**: fact questions 98.4%, refusals
10 → 1, at $0.024 per correct answer (plain API). Decomposing multi-period questions helped
retrieval but not accuracy. See [`docs/results/p2-routing.md`](docs/results/p2-routing.md).

**P3 done: evals as infrastructure.** No new best accuracy (the headline is still 87.3%); P3 makes
changes measurable. Re-running the best config moves accuracy by about one question, so the gate
checks retrieval instead, which caches make exact. A self-hosted CI job runs `frl gate` on every
retrieval PR, and it blocked a deliberately bad chunker (recall@10 0.833 → 0.584). Swapping the
answering model changes accuracy by at most 7 questions (Sonnet 87.3%, Haiku 84.0%, Opus 84.0%,
a 4-bit Qwen3.6-27B 82.7%; no OpenAI row). Fiscal-period and faithfulness metrics, and two
synthetic question sets kept apart from the headline, are also in. See
[`docs/results/p3-evals.md`](docs/results/p3-evals.md).

## Reproduce

Setup is in `CLAUDE.md`. Secrets in `.env` are 1Password `op://` references, so start the
database with `op run --env-file=.env -- docker compose up -d` and prefix commands that need
keys or the database the same way (`op run --env-file=.env -- uv run frl ...`). Then:

```
op run --env-file=.env -- uv run frl bench pull && op run --env-file=.env -- uv run frl corpus fetch  # FinanceBench open subset + EDGAR filings
op run --env-file=.env -- uv run frl ingest build naive-fixed@1 && op run --env-file=.env -- uv run frl ingest label naive-fixed@1
op run --env-file=.env -- uv run frl run configs/p0-naive.toml        # prints the run id
op run --env-file=.env -- uv run frl judge <run-id> && op run --env-file=.env -- uv run frl report <run-id>
```
