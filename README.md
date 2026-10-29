# Filings RAG Lab

Open-source research assistant over SEC EDGAR filings (10-K, 10-Q, 8-K), scored publicly against
[FinanceBench](https://github.com/patronus-ai/financebench) (open subset, 150 questions).

Retrieval is the focus: metadata-scoped hybrid retrieval in Postgres + pgvector, XBRL facts answered
with SQL, and a router between the two. An experiment harness records every run (pipeline, retriever,
model, judge) with tokens and API-equivalent cost, and a CI eval gate blocks regressions.

Batch work (ingest, embedding, evals) runs locally; the public deployment is on AWS (Terraform-provisioned EC2).

## Status

**P0 done: baseline on the board.** `naive-fixed@1` with vector top-10 and Sonnet 5.5 scores
**66.7% (100/150)** on the FinanceBench **open subset** (not the full set), at $0.032 per correct
answer at plain API rates ($0.016 with the Batch API). Accuracy is 81% when retrieval finds the
evidence and 45% when it doesn't. See [`docs/results/p0-baseline.md`](docs/results/p0-baseline.md)
for the failure analysis.

Next, P1: metadata filters, table-preserving chunks, hybrid retrieval and a reranker, measured
on a corpus with distractor fiscal years.

## Reproduce

Setup is in `CLAUDE.md`. Then:

```
uv run frl bench pull && uv run frl corpus fetch  # FinanceBench open subset + EDGAR filings
uv run frl ingest build naive-fixed@1 && uv run frl ingest label naive-fixed@1
uv run frl run configs/p0-naive.toml        # prints the run id
uv run frl judge <run-id> && uv run frl report <run-id>
```
