#!/usr/bin/env bash
# Restore a serving dump (scripts/serving_dump.sh) as frl_index, on the host running the stack.
#
#   FRL_READER_PASSWORD=... deploy/restore_index.sh <x.dump | s3://bucket/serving/.../x.dump>
#
# The dump's .manifest.json must sit next to it. Steps:
#   1. fetch (s3) and check the sha256 against the manifest
#   2. restore into a fresh frl_index_new, leaving out the HNSW and full-text indexes (serving
#      scans scoped documents exactly, so they'd only cost disk and restore time)
#   3. verify: row counts and migration level match the manifest, the pipeline is registered,
#      and a chunk's own embedding finds that chunk first
#   4. ANALYZE; grant the read-only role (frl_reader) the api connects as
#   5. stop the api, rename frl_index → frl_index_old and frl_index_new → frl_index (one
#      transaction), start the api, check /healthz. The previous index stays as frl_index_old
#      until the next restore (rollback: deploy/README.md).
#
# On failure: before the swap, the live index is untouched (frl_index_new is dropped); during
# the swap, the old name is put back and the api restarted; if the api is unhealthy on the new
# index, it is rolled back to frl_index_old (the new one kept as frl_index_bad).
#
# Environment: COMPOSE (default: docker compose with deploy/compose.prod.yaml; the stack's
# env must be set as for `up`), WORK (default /var/tmp/frl-restore), FRL_READER_PASSWORD.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="${COMPOSE:-docker compose -f $HERE/compose.prod.yaml}"
WORK="${WORK:-/var/tmp/frl-restore}"
: "${FRL_READER_PASSWORD:?set FRL_READER_PASSWORD (frl_reader, the api user on frl_index)}"
src="${1:?usage: restore_index.sh <x.dump | s3://.../x.dump>}"
[[ "$src" == *.dump ]] || { echo "expected a .dump" >&2; exit 2; }

say() { echo "[$(date -u +%H:%M:%S)] $*"; }
psql_() { $COMPOSE exec -T db psql -X -q -v ON_ERROR_STOP=1 -U frl "$@"; }
val() { psql_ -d "$1" -Atc "$2"; }
has_db() { [[ "$(val postgres "SELECT count(*) FROM pg_database WHERE datname = '$1'")" == 1 ]]; }
start=$(date +%s)

mkdir -p "$WORK"
if [[ "$src" == s3://* ]]; then
  say "fetching $src"
  aws s3 cp --only-show-errors "$src" "$WORK/index.dump"
  aws s3 cp --only-show-errors "${src%.dump}.manifest.json" "$WORK/manifest.json"
  dump="$WORK/index.dump" manifest="$WORK/manifest.json"
else
  dump="$src" manifest="${src%.dump}.manifest.json"
fi
m() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$manifest" "$1"; }

say "checking sha256"
[[ "$(sha256sum "$dump" | cut -d' ' -f1)" == "$(m sha256)" ]] || { echo "sha256 mismatch" >&2; exit 1; }
table="$(m chunk_table)"
server="$(val postgres 'SHOW server_version')"
[[ "$server" == "$(m server_version)" ]] || say "warning: server $server, dump from $(m server_version)"

phase=restore

rollback() {  # the api is unhealthy on the new index: back to the previous one
  has_db frl_index_old || { echo "no frl_index_old to roll back to" >&2; return 1; }
  $COMPOSE stop api
  psql_ -d postgres -c "DROP DATABASE IF EXISTS frl_index_bad"
  psql_ -1 -d postgres -c "ALTER DATABASE frl_index RENAME TO frl_index_bad" \
    -c "ALTER DATABASE frl_index_old RENAME TO frl_index"
  $COMPOSE up -d --wait api
  echo "rolled back to the previous index; the new one is frl_index_bad" >&2
}

on_exit() {
  local rc=$?
  trap - EXIT
  $COMPOSE exec -T db rm -f /tmp/index.dump /tmp/index.toc >/dev/null 2>&1 || true
  [[ "$src" == s3://* ]] && rm -f "$WORK/index.dump" "$WORK/manifest.json"
  (( rc == 0 )) && return
  case "$phase" in
    restore)
      psql_ -d postgres -c "DROP DATABASE IF EXISTS frl_index_new" >/dev/null 2>&1 || true
      echo "restore failed before the swap; the live index is untouched" >&2 ;;
    swap)
      if ! has_db frl_index && has_db frl_index_old; then
        psql_ -d postgres -c "ALTER DATABASE frl_index_old RENAME TO frl_index" || true
      fi
      $COMPOSE up -d api || true
      echo "swap failed; restarted the api on frl_index. Check: docker compose ps; healthz" >&2 ;;
    check)
      rollback || echo "roll back by hand: deploy/README.md (Rollback)" >&2 ;;
  esac
  exit "$rc"
}
trap on_exit EXIT

say "restoring $(m dump) ($(( $(m bytes) / 1048576 )) MB) into frl_index_new"
$COMPOSE cp "$dump" db:/tmp/index.dump
$COMPOSE exec -T db sh -c "pg_restore -l /tmp/index.dump | grep -vE ' INDEX public [a-z0-9_]+_(hnsw|tsv) ' > /tmp/index.toc"
psql_ -d postgres -c "DROP DATABASE IF EXISTS frl_index_new" -c "CREATE DATABASE frl_index_new"
psql_ -d frl_index_new -c "CREATE EXTENSION vector"
$COMPOSE exec -T db pg_restore -U frl -d frl_index_new -j 2 --no-owner --no-privileges \
  --exit-on-error -L /tmp/index.toc /tmp/index.dump
$COMPOSE exec -T db rm -f /tmp/index.dump /tmp/index.toc

say "verifying"
python3 -c 'import json,sys; [print(t, n) for t, n in json.load(open(sys.argv[1]))["rows"].items()]' "$manifest" |
  while read -r t n; do
    got="$(val frl_index_new "SELECT count(*) FROM public.\"$t\"")"
    [[ "$got" == "$n" ]] || { echo "$t: $got rows, manifest says $n" >&2; exit 1; }
  done
[[ "$(val frl_index_new 'SELECT max(name) FROM schema_migrations')" == "$(m migration)" ]] ||
  { echo "migration level differs from the manifest" >&2; exit 1; }
[[ "$(val frl_index_new "SELECT count(*) FROM pipelines WHERE chunk_table = '$table'")" == 1 ]] ||
  { echo "$table is not in the pipeline registry" >&2; exit 1; }
self="$(val frl_index_new "SET enable_indexscan = off; WITH c AS (SELECT id, embedding FROM \"$table\" ORDER BY id LIMIT 1)
  SELECT (SELECT t.id FROM \"$table\" t ORDER BY t.embedding <=> c.embedding, t.id LIMIT 1) = c.id FROM c")"
[[ "$self" == t ]] || { echo "a chunk's embedding didn't find itself" >&2; exit 1; }

say "analyzing; granting frl_reader"
psql_ -d frl_index_new -c "ANALYZE"
# The password goes in through the environment, not the command line (ps); the server stores
# a SCRAM hash, and log_statement is off, so it isn't logged.
$COMPOSE exec -T -e FRL_READER_PASSWORD db psql -X -q -v ON_ERROR_STOP=1 -U frl -d postgres <<'SQL'
SELECT 'CREATE ROLE frl_reader LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'frl_reader')
\gexec
\getenv pw FRL_READER_PASSWORD
ALTER ROLE frl_reader PASSWORD :'pw';
SQL
psql_ -d postgres <<'SQL'
ALTER ROLE frl_reader SET default_transaction_read_only = on;
REVOKE CONNECT ON DATABASE frl_app FROM PUBLIC;
REVOKE CONNECT ON DATABASE frl_index_new FROM PUBLIC;
GRANT CONNECT ON DATABASE frl_index_new TO frl_reader;
SQL
psql_ -d frl_index_new -c "GRANT USAGE ON SCHEMA public TO frl_reader" \
  -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO frl_reader"
# Make room before the downtime starts: the index before last, and any rolled-back one.
psql_ -d postgres -c "DROP DATABASE IF EXISTS frl_index_old" -c "DROP DATABASE IF EXISTS frl_index_bad"

say "swapping (the api stops briefly)"
phase=swap
$COMPOSE stop api
psql_ -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'frl_index'" >/dev/null
if has_db frl_index; then
  psql_ -1 -d postgres -c "ALTER DATABASE frl_index RENAME TO frl_index_old" \
    -c "ALTER DATABASE frl_index_new RENAME TO frl_index"
else
  psql_ -d postgres -c "ALTER DATABASE frl_index_new RENAME TO frl_index"
fi
phase=check
$COMPOSE up -d --wait api
health="$($COMPOSE exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=10).read().decode())")"
say "healthz: $health"
[[ "$health" == *'"ok":true'* ]] || { echo "unhealthy after the swap" >&2; exit 1; }
phase=done
say "restored $(m pipeline) ($(m dump)) in $(( $(date +%s) - start )) s;" \
  "frl_index is $(val postgres "SELECT pg_size_pretty(pg_database_size('frl_index'))")"
