#!/usr/bin/env bash
# Pull production thumbs-downs for review (P4 PR 10). Run on the Nucbox with an admin profile:
#
#   AWS_PROFILE=frl-admin scripts/feedback_pull.sh [--include-smoke] [--key <s3-key>]
#
# Restores the newest frl_app backup (daily, 09:00 UTC; see deploy/README.md) into a throwaway
# container and writes one JSON line per thumbs-down to data/feedback/<UTC date>.jsonl:
# the question, the served answer, the rating's comment, scope, route, retrieved chunks, config
# hash and git SHA. Smoke traffic is left out unless --include-smoke. No IP hash is copied.
# data/ is gitignored: these are strangers' questions. Next: frl bench from-feedback.
set -euo pipefail
cd "$(dirname "$0")/.."
smoke=false
key=""
while (($#)); do
  case "$1" in
    --include-smoke) smoke=true ;;
    --key) key="${2:?--key needs an S3 key}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
. scripts/backup_lib.sh
restore_backup "$key"

mkdir -p data/feedback
out="data/feedback/$(date -u +%F).jsonl"
app_sql "
  SELECT json_build_object(
    'query_id', q.id, 'asked_at', q.created_at, 'question', q.question,
    'status', q.status, 'refusal', q.refusal, 'answer', q.answer,
    'rating', f.rating, 'comment', f.comment, 'rated_at', f.created_at,
    'scope', q.scope, 'route', q.route, 'retrieved', q.retrieved,
    'model', q.model, 'config_hash', q.config_hash, 'git_sha', q.git_sha,
    'tags', q.tags, 'trace_id', q.trace_id, 'cost_usd', q.cost_usd,
    'backup', '$backup_key')
  FROM feedback f JOIN served_queries q ON q.id = f.query_id
  WHERE f.rating = 'down' AND ($smoke OR NOT 'smoke' = ANY (q.tags))
  ORDER BY f.created_at" >"$out"
echo "wrote $(wc -l <"$out") thumbs-down(s) to $out"
