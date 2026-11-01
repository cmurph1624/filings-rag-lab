# Filings RAG Lab

Open-source research assistant over SEC EDGAR filings (10-K, 10-Q, 8-K), scored publicly against
[FinanceBench](https://github.com/patronus-ai/financebench) (open subset, 150 questions).

Retrieval is the focus: metadata-scoped vector retrieval in Postgres + pgvector with a reranker. An
experiment harness records every run (pipeline, retriever, model, judge) with tokens and
API-equivalent cost. CI runs lint, type checks and tests on every PR.

Not finished yet: a router that answers fact questions from XBRL data with SQL (P2, in progress),
a CI eval gate that blocks regressions (P3), and a public deployment on AWS (Terraform-provisioned
EC2, P4). Everything runs locally for now.

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

**P2 in progress: routing.** XBRL company facts are loaded into Postgres with a metrics library on
top ([`docs/results/p2-xbrl-facts.md`](docs/results/p2-xbrl-facts.md)); the router that sends fact
questions to SQL is not merged yet.

**P2 done: routing.** XBRL facts (SEC `companyfacts`) and a metrics library answer fact
questions deterministically. A router sends them to SQL-computed facts and keeps prose on
retrieval. That lifts the open subset to **87.3% (131/150)**: fact questions 98.4%, refusals
10 → 1, at $0.024 per correct answer (plain API). Decomposing multi-period questions helped
retrieval but not accuracy. See [`docs/results/p2-routing.md`](docs/results/p2-routing.md).

## Reproduce

Setup is in `CLAUDE.md`. Then:

```
uv run frl bench pull && uv run frl corpus fetch  # FinanceBench open subset + EDGAR filings
uv run frl ingest build naive-fixed@1 && uv run frl ingest label naive-fixed@1
uv run frl run configs/p0-naive.toml        # prints the run id
uv run frl judge <run-id> && uv run frl report <run-id>
```
