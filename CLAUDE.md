# Filings RAG Lab — conventions

Design, phases and decisions live in the vault: `~/TheBrainOfChris/10 Projects/Filings RAG Lab/Context.md`.
Read it before changing architecture. Retrieval is the focus; the harness makes it measurable.

## Setup
- `uv sync`, `cp .env.example .env`, `docker compose up -d`, `uv run frl db migrate`.
- Postgres (pgvector, pg17) listens on `127.0.0.1:5434`. Other projects own 5432/5433/55432.

## Rules
- Python 3.12 via uv. `uv run ruff check`, `uv run ruff format`, `uv run mypy` and `uv run pytest` must pass.
- Schema changes are new files in `migrations/NNN_name.sql`. Never edit an applied migration.
- Plain SQL with psycopg 3; no ORM.
- Anything that varies between experiments (pipeline, retriever, model, judge) is config, not a code path.
- Every model or embedding call records tokens. Cost is API-equivalent from `prices.yaml`,
  even for `claude-cli` (subscription) runs.
- Ingest pipelines are versioned; each version writes to its own namespace. Bump the version
  instead of changing a pipeline's output in place.
- Downloaded filings and run outputs live under `data/` (gitignored). Small manifests that make
  runs reproducible (e.g. `corpus/manifest.csv`) are committed.
- EDGAR: send `SEC_USER_AGENT` on every request and stay under 10 req/s.
- Results are FinanceBench **open subset (150 questions)**. Never imply full-set numbers.
- The public deployment uses the Anthropic API only; `claude-cli` is for local benchmark runs.

## Workflow
- Branch + PR per change; the automated reviewer watches this repo (run the pr-review-loop skill).
