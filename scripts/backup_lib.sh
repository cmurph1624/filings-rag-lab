# Sourced by scripts/backup_drill.sh and scripts/feedback_pull.sh (not run on its own): restore
# an frl_app backup from S3 into a throwaway Postgres container, with the production image and
# no published port. Needs an AWS profile that can read the bucket (frl-admin).
#
#   restore_backup [s3-key]   the newest backup if no key; sets $backup_key; removes the
#                             container and the download on exit
#   app_sql <sql>             run a query against the restored frl_app (psql -At)
#
# The dump holds question text and hashed IPs, so nothing it restores is kept.
bucket="${FRL_BUCKET:-frl-271200701471-data}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-2}"
image="pgvector/pgvector:pg17@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d"
_tmp="$(mktemp -d)"
_name="frl-drill-$$"
_cleanup() { docker rm -f "$_name" >/dev/null 2>&1 || true; rm -rf "$_tmp"; }
trap _cleanup EXIT

restore_backup() {
  backup_key="${1:-$(aws s3api list-objects-v2 --bucket "$bucket" --prefix backups/frl_app/ \
    --query 'max_by(Contents, &LastModified).Key' --output text)}"
  [[ "$backup_key" == backups/frl_app/*.dump ]] || { echo "no backup found (got '$backup_key')" >&2; return 1; }
  aws s3 cp --only-show-errors "s3://$bucket/$backup_key" "$_tmp/app.dump"
  echo "restoring $backup_key ($(stat -c%s "$_tmp/app.dump") bytes)" >&2
  docker run -d --name "$_name" -e POSTGRES_USER=frl -e POSTGRES_PASSWORD=drill \
    -e POSTGRES_DB=frl_app "$image" >/dev/null
  local ready=0
  for _ in $(seq 60); do
    if docker exec "$_name" pg_isready -h 127.0.0.1 -U frl -d frl_app >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
  done
  (( ready )) || { echo "the throwaway Postgres didn't start" >&2; return 1; }
  docker exec -i "$_name" pg_restore -U frl -d frl_app --no-owner --exit-on-error <"$_tmp/app.dump"
}

app_sql() { docker exec "$_name" psql -X -U frl -d frl_app -v ON_ERROR_STOP=1 -Atc "$1"; }
