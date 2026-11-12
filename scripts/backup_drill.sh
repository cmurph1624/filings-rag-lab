#!/usr/bin/env bash
# Restore drill: prove the newest frl_app backup restores. Run on the Nucbox with an admin
# profile (the server can't read its own backups):
#
#   AWS_PROFILE=frl-admin scripts/backup_drill.sh [s3-key]
#
# Restores the newest backup (or the given key under backups/) into a throwaway container
# (scripts/backup_lib.sh), prints the row count of each app table, then removes everything.
set -euo pipefail
. "$(dirname "$0")/backup_lib.sh"
restore_backup "${1:-}"
app_sql "
  SELECT 'served_queries', count(*)::text FROM served_queries UNION ALL
  SELECT 'feedback', count(*)::text FROM feedback UNION ALL
  SELECT 'rate_events', count(*)::text FROM rate_events UNION ALL
  SELECT 'newest query', coalesce(max(created_at)::text, '-') FROM served_queries" | tr '|' '\t'
echo "drill ok"
