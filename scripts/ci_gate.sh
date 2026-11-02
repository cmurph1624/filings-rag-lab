#!/usr/bin/env bash
# The retrieval gate in CI (P3 PR 4), on the self-hosted runner against the frl_ci database.
#   scripts/ci_gate.sh BASE_SHA VERDICT_FILE
# Gates the current code with the configs' own pipelines, then every pipeline version this
# change adds to the registry (vs BASE_SHA), each rebuilt from scratch over the gate corpus.
# Exit 0: all pass. 1: a breach. 2: the gate couldn't run.
set -uo pipefail
base_sha=$1
verdict=$2
tmp=${RUNNER_TEMP:-$(mktemp -d)}

if [[ "${DATABASE_URL:-}" != */frl_ci ]]; then
  echo "refusing to run: DATABASE_URL must point at the frl_ci database" >&2
  exit 2
fi
uv sync --locked -q || exit 2
uv run frl db migrate || exit 2

# Each list says which source tree it imported, and the script checks it: if the base listing
# silently imported the PR's own registry, every new pipeline would skip the gate.
list='import sys, filings_rag
from filings_rag.ingest.registry import PIPELINES
print(filings_rag.__file__, file=sys.stderr)
print("\n".join(f"{n}@{v}" for n, vs in PIPELINES.items() for v in vs))'
rm -rf "$tmp/base-src" && mkdir -p "$tmp/base-src"
git archive "$base_sha" src | tar -x -C "$tmp/base-src" || exit 2
PYTHONPATH="$tmp/base-src/src" uv run python -c "$list" 2> "$tmp/origin-base.txt" \
  | sort > "$tmp/pipelines-base.txt" || exit 2
uv run python -c "$list" 2> "$tmp/origin-head.txt" | sort > "$tmp/pipelines-head.txt" || exit 2
if [[ $(cat "$tmp/origin-base.txt") != "$tmp/base-src/src/"* ]]; then
  echo "base registry imported from $(cat "$tmp/origin-base.txt"), not the base commit" >&2
  exit 2
fi
mapfile -t added < <(comm -13 "$tmp/pipelines-base.txt" "$tmp/pipelines-head.txt")

status=0
run_gate() {  # label, then frl gate args
  local label=$1 part="$tmp/gate-$RANDOM.md" rc
  shift
  echo "== $label"
  uv run frl gate --out "$part" "$@"
  rc=$?
  if [[ $rc -ne 0 && ! -s $part ]]; then
    printf '## Retrieval gate: ERROR (%s)\n\n`frl gate %s` exited %s; see the job log.\n' \
      "$label" "$*" "$rc" > "$part"
  fi
  { echo; echo "<!-- $label -->"; cat "$part"; } >> "$verdict"
  if (( rc == 1 )); then status=$(( status > 1 ? status : 1 )); fi
  if (( rc > 1 )); then status=2; fi
}

: > "$verdict"
run_gate "current code"
for p in "${added[@]}"; do
  run_gate "new pipeline $p" --pipeline "$p" --fresh
done
if (( ${#added[@]} == 0 )); then
  printf '\nNo new pipeline versions in this change.\n' >> "$verdict"
fi
exit $status
