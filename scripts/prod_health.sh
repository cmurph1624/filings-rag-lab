#!/usr/bin/env bash
# Health of the production deployment, for the ops registry (~/dev/agent-ops/ops/deployments.yaml)
# and by hand. Exit 0 = healthy. Needs an AWS profile that can list the data bucket (frl-admin):
#
#   AWS_PROFILE=frl-admin scripts/prod_health.sh
#
# Checks that /healthz answers (both databases and the index reachable) and that the newest
# frl_app backup is under 30 hours old (frl-backup.timer runs daily at 09:00 UTC).
set -euo pipefail
host="${FRL_HOST:-filings.stigmergylabs.net}"
bucket="${FRL_BUCKET:-frl-271200701471-data}"
max_age_h="${MAX_BACKUP_AGE_H:-30}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-2}"

curl -fsS -m 10 -o /dev/null "https://$host/healthz" || { echo "DOWN: https://$host/healthz" >&2; exit 1; }

newest="$(aws s3api list-objects-v2 --bucket "$bucket" --prefix backups/frl_app/ \
  --query 'max_by(Contents, &LastModified).LastModified' --output text 2>/dev/null || true)"
[[ -n "$newest" && "$newest" != None ]] || { echo "NO BACKUP under s3://$bucket/backups/frl_app/" >&2; exit 1; }
age_h=$(( ($(date +%s) - $(date -d "$newest" +%s)) / 3600 ))
(( age_h < max_age_h )) || { echo "STALE BACKUP: newest is ${age_h}h old ($newest)" >&2; exit 1; }
echo "ok: $host healthy; newest backup ${age_h}h old"
