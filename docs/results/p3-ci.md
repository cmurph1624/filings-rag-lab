# P3 CI: the retrieval gate on every retrieval PR

FinanceBench open subset (150 questions). Workflows `.github/workflows/eval-gate.yml` and
`eval-full.yml`, scripts `scripts/ci_gate.sh` and `scripts/ci_eval_full.sh`.

## Runner and database

- **Runner:** self-hosted on the Nucbox, `nucbox-frl` (labels `self-hosted, nucbox, frl`),
  in `~/actions-runner/filings-rag-lab`, run as the user systemd service `gh-runner-frl`.
  Its `.env` holds `DATABASE_URL` (pointing at `frl_ci`), `VOYAGE_API_KEY` and
  `SEC_USER_AGENT`, so no secrets live in GitHub. `ANTHROPIC_API_KEY` is deliberately absent:
  answer evals use `claude-cli` under the usage governor.
- **Database:** `frl_ci` on the same Postgres (127.0.0.1:5434), copied from `frl` on
  2026-11-01 without the unused `naive-fixed@1` and `table-preserving@1` builds (3.0 GB, against
  8.5 GB). It holds `structured@1`, the corpora, XBRL facts, the gate baselines 1079/1080, run
  846 and the embedding and rerank caches. Both scripts refuse to run unless `DATABASE_URL`
  ends in `/frl_ci`, so CI never writes to the working database.

To refresh it: `createdb frl_ci2`, then `pg_dump frl --exclude-table='chunks__naive_fixed__v1*'
--exclude-table='chunks__table_preserving__v1*' | psql frl_ci2`, drop those two pipelines'
registry rows (`build.drop`), and swap the names.

## Trust

PR code runs on the Nucbox. Both jobs run only for same-repo PRs, never forks, and the gate also
requires an author with write access (OWNER, MEMBER or COLLABORATOR). The `eval:full` label can
only be added by someone with write access. The repo is private with one collaborator.

## `eval-gate`: every PR that touches retrieval

Triggered by PRs touching ingest, retrieve, embed, rerank, route, facts, harness, extract, the
caches, corpora, the gate, `config/`, `configs/`, `corpus/` or migrations.

1. `frl gate` at the PR's code: both gated configs, with their own pipelines.
2. For each pipeline version the PR adds to the registry (compared with the base commit):
   `frl gate --pipeline name@v --fresh`. That rebuilds it over the 147-filing gate corpus from
   scratch on every push, re-embedding only changed chunk text.
3. The verdict is posted as one PR comment, found by its `<!-- frl-eval-gate -->` marker and
   edited on each push, and the check fails on any breach. eval-full reports are separate
   comments that the gate never touches.

A pipeline edited in place, without a version bump, fails pytest instead: each built
pipeline's output is pinned by a test.

## `eval-full`: on demand

Add the `eval:full` label to a PR. It runs run 846's config at the PR's code (Sonnet, judged by
Opus, about an hour of governor-paced `claude-cli`) and posts `frl report RUN --vs 846`. It's
informational, since answer accuracy moves by about one question between runs
(`p3-noise.md`). If the governor pauses it, the comment says how to resume.

Smoke test: `EVAL_LIMIT=2 scripts/ci_eval_full.sh` ran, judged and reported run 1186.

## Proof

*(filled in below once the deliberately bad chunker PR has run)*
