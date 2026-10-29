# Filings RAG Lab

Open-source research assistant over SEC EDGAR filings (10-K, 10-Q, 8-K), scored publicly against
[FinanceBench](https://github.com/patronus-ai/financebench) (open subset, 150 questions).

Retrieval is the focus: metadata-scoped hybrid retrieval in Postgres + pgvector, XBRL facts answered
with SQL, and a router between the two. An experiment harness records every run (pipeline, retriever,
model, judge) with tokens and API-equivalent cost, and a CI eval gate blocks regressions.

Batch work (ingest, embedding, evals) runs locally; the public deployment is on AWS (Terraform-provisioned EC2).

Status: P0, baseline and harness skeleton. Nothing here yet.
