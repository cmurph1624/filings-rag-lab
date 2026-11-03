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

**A harmless retrieval PR passes.** This PR (#30) touches gated paths. Its `eval-gate` run
gated the current code, PASSED with every metric identical to the baselines, and posted the
verdict comment.

**A deliberately bad chunker is blocked.** Draft PR #31 added `naive-fixed@2`: 60-word windows
with no overlap, so tables split mid-row. CI detected the new pipeline version, built it over the
147-filing gate corpus (161,917 chunks, 16.3M tokens embedded, about $1.96 at list, 47 minutes
for the whole job), labelled it, and **failed the check**
([run](https://github.com/cmurph1624/filings-rag-lab/actions/runs/41380019974)):

| check | metric | baseline (structured@1) | naive-fixed@2 | allowed drop |
|---|---|---|---|---|
| gate-best | recall@10 | 0.833 | 0.584 | 0.010 |
| gate-best | hit@10 | 0.886 | 0.638 | 0.010 |
| gate-best | MRR | 0.651 | 0.395 | 0.010 |
| gate-best | fact checks | 48 | 48 | 0 |
| gate-vector | recall@10 | 0.521 | 0.312 | 0.010 |
| gate-vector | hit@10 | 0.557 | 0.349 | 0.010 |
| gate-vector | MRR | 0.282 | 0.200 | 0.010 |

41 questions (gate-best) and 43 (gate-vector) lost their evidence from the top 10. The fact route
didn't move: it reads XBRL, not chunks. #31 was closed unmerged, and its build dropped from
`frl_ci`.

The plan estimated 3–4M tokens for a gate rebuild. Small windows make many more chunks, so a
chunker change costs up to about $2 the first time. Later pushes to the same PR re-embed only
chunk text that changed.

## Update (P3 PR 8): a third gate check on synthetic prose questions

`config/gate.toml` gained `gate-synth-prose` (baseline run 1525): the P2 best retrieval on the 150
`synth-prose@1` questions, retrieval-only, on the same gate corpus. It is a separate question
set and the verdict says so; it is never mixed into the open-subset checks. `scripts/ci_gate.sh`
now runs `frl bench load-synth` and `frl ingest label structured@1` before gating, because the
questions are committed files (`corpus/synth-*.jsonl`) and their evidence labels are per
pipeline. Run 1525 was copied into `frl_ci` by hand with its rows from `runs` and
`run_questions`; a refresh from `frl` after this date already contains it. The set is close to
its ceiling (recall@10 1.000), so it catches a chunker that breaks prose but cannot show an
improvement: see `p3-synth.md`.
