#!/usr/bin/env bash
# The answer-level eval in CI (P3 PR 4), for PRs labelled `eval:full`: run 846's config at the
# current code on the frl_ci database (claude-cli under the usage governor), judge it, and
# report it against run 846.   scripts/ci_eval_full.sh REPORT_FILE
# EVAL_LIMIT=N runs only the first N questions (a smoke test). The report is informational:
# answer accuracy moves by about one question run to run (docs/results/p3-noise.md).
set -uo pipefail
report=$1
config=configs/p2-04-router-rules-facts.toml
baseline=846
limit=()
[[ -n "${EVAL_LIMIT:-}" ]] && limit=(--limit "$EVAL_LIMIT")

if [[ "${DATABASE_URL:-}" != */frl_ci ]]; then
  echo "refusing to run: DATABASE_URL must point at the frl_ci database" >&2
  exit 2
fi
uv sync --locked -q || exit 2
uv run frl db migrate || exit 2

log=$(mktemp)
uv run frl run "$config" "${limit[@]}" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
run_id=$(grep -m1 -oE '^run [0-9]+' "$log" | cut -d' ' -f2)
if [[ -z "$run_id" ]]; then
  echo "## Answer eval: ERROR — the run didn't start; see the job log." > "$report"
  exit 2
fi
if (( rc == 3 )); then
  printf '## Answer eval: PAUSED\n\nThe usage governor stopped run %s (exit 3). Resume it on the runner with `frl run %s --resume %s`, or re-add the label later.\n' \
    "$run_id" "$config" "$run_id" > "$report"
  exit 3
fi
(( rc == 0 )) || { echo "## Answer eval: ERROR — run $run_id failed (exit $rc)." > "$report"; exit 2; }
uv run frl judge "$run_id"
rc=$?
if (( rc == 3 )); then
  printf '## Answer eval: PAUSED\n\nThe usage governor stopped judging run %s. Resume with `frl judge %s`.\n' \
    "$run_id" "$run_id" > "$report"
  exit 3
fi
(( rc == 0 )) || { echo "## Answer eval: ERROR — judging run $run_id failed (exit $rc)." > "$report"; exit 2; }
{
  printf '## Answer eval: run %s vs baseline %s\n\n' "$run_id" "$baseline"
  printf 'FinanceBench open subset. Informational: accuracy moves by about one question between runs of the same config (docs/results/p3-noise.md).\n\n'
  uv run frl report "$run_id" --vs "$baseline"
} > "$report"
