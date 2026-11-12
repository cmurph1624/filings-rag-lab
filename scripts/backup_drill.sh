#!/usr/bin/env bash
# Restore drill: prove the newest frl_app backup restores. Run on the Nucbox with an admin
# profile (the server can't read its own backups):
#
#   AWS_PROFILE=frl-admin scripts/backup_drill.sh [s3-key]
#
# Downloads the newest backup (or the given key under backups/), restores it into a throwaway
# Postgres container with the production image and no published port, prints the row count of
# each app table, then removes the container and the download. The dump holds question text and
# hashed IPs, so nothing is kept.
set -euo pipefail
bucket="${FRL_BUCKET:-frl-271200701471-data}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-2}"
image="pgvector/pgvector:pg17@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d"

key="${1:-$(aws s3api list-objects-v2 --bucket "$bucket" --prefix backups/frl_app/ \
  --query 'max_by(Contents, &LastModified).Key' --output text)}"
[[ "$key" == backups/frl_app/*.dump ]] || { echo "no backup found (got '$key')" >&2; exit 1; }

tmp="$(mktemp -d)"
name="frl-drill-$$"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$tmp"; }
trap cleanup EXIT

aws s3 cp --only-show-errors "s3://$bucket/$key" "$tmp/app.dump"
echo "restoring $key ($(stat -c%s "$tmp/app.dump") bytes)"
docker run -d --name "$name" -e POSTGRES_USER=frl -e POSTGRES_PASSWORD=drill \
  -e POSTGRES_DB=frl_app "$image" >/dev/null
for _ in $(seq 60); do
  docker exec "$name" pg_isready -h 127.0.0.1 -U frl -d frl_app >/dev/null 2>&1 && break
  sleep 1
done
docker exec -i "$name" pg_restore -U frl -d frl_app --no-owner --exit-on-error <"$tmp/app.dump"
docker exec "$name" psql -X -U frl -d frl_app -Atc "
  SELECT 'served_queries', count(*)::text FROM served_queries UNION ALL
  SELECT 'feedback', count(*)::text FROM feedback UNION ALL
  SELECT 'rate_events', count(*)::text FROM rate_events UNION ALL
  SELECT 'newest query', coalesce(max(created_at)::text, '-') FROM served_queries" | tr '|' '\t'
echo "drill ok"
