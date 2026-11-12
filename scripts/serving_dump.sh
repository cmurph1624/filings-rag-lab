#!/usr/bin/env bash
# Dump what the serving app reads for one pipeline, with a manifest, for deploy/restore_index.sh.
#
#   scripts/serving_dump.sh structured@1 [--upload s3://<bucket>]
#
# Writes data/serving/<name>@<v>/<stamp>-<sha>.dump and .manifest.json. With --upload, copies
# both to s3://<bucket>/serving/<name>@<v>/ (the deploy role reads them from there).
#
# The serving set: the pipeline's chunk table, documents, corpora, corpus_documents,
# benchmark_docs, xbrl_facts, pipelines and schema_migrations. Nothing else: no runs,
# questions, caches or other pipelines. The served config retrieves by exact scan over a
# scope, so the restore leaves out the HNSW and full-text indexes (they're in the dump).
#
# pg_dump runs in the same pinned image as the databases (PG_IMAGE), so its version matches.
# Run it while nothing writes to these tables (no ingest or fact load): the row counts are
# taken before and after the dump, and the script stops if they differ.
set -euo pipefail

PG_IMAGE="${PG_IMAGE:-pgvector/pgvector:pg17@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ref="${1:?usage: serving_dump.sh <name@version> [--upload s3://bucket]}"
shift
bucket=""
if [[ "${1:-}" == "--upload" ]]; then
  bucket="${2:?--upload needs s3://bucket}"
  bucket="${bucket%/}"
fi
name="${ref%@*}"
version="${ref#*@}"
[[ "$name" != "$ref" && "$version" =~ ^[0-9]+$ ]] || { echo "pipeline must be name@version" >&2; exit 2; }

if [[ -z "${DATABASE_URL:-}" && -f "$ROOT/.env" ]]; then
  DATABASE_URL="$(grep -E '^DATABASE_URL=' "$ROOT/.env" | head -1 | cut -d= -f2-)"
fi
: "${DATABASE_URL:?set DATABASE_URL}"
[[ "$DATABASE_URL" != op://* ]] || { echo "DATABASE_URL is a 1Password reference: run under op run" >&2; exit 2; }

pg() {  # a Postgres client tool from PG_IMAGE, reaching the host's loopback-bound database
  docker run --rm -i --network host --user "$(id -u):$(id -g)" -v "$out:/out" "$PG_IMAGE" "$@"
}
sqlval() { pg psql -X "$DATABASE_URL" -Atqc "$1"; }

out="$ROOT/data/serving/$ref"
mkdir -p "$out"

table="$(sqlval "SELECT chunk_table FROM pipelines WHERE name = '$name' AND version = $version")"
[[ -n "$table" ]] || { echo "no pipeline $ref in the registry" >&2; exit 1; }
tables=("$table" documents corpora corpus_documents benchmark_docs xbrl_facts pipelines schema_migrations)

sha="$(git -C "$ROOT" rev-parse HEAD)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="$stamp-${sha:0:12}"
args=()
for t in "${tables[@]}"; do args+=(-t "public.$t"); done

# Row counts, so the restore can check it got everything.
row_counts() {
  local c="{"
  for t in "${tables[@]}"; do c+="\"$t\": $(sqlval "SELECT count(*) FROM public.\"$t\""),"; done
  echo "${c%,}}"
}
counts="$(row_counts)"

echo "dumping ${tables[*]} → $out/$base.dump"
start=$(date +%s)
pg pg_dump "$DATABASE_URL" -Fc -Z 6 --no-owner --no-privileges "${args[@]}" -f "/out/$base.dump"
echo "dumped in $(( $(date +%s) - start )) s"
if [[ "$(row_counts)" != "$counts" ]]; then
  rm -f "$out/$base.dump"
  echo "rows changed during the dump (a write is running?); dump discarded, try again" >&2
  exit 1
fi

python3 - "$out/$base" "$ref" "$table" "$sha" "$stamp" "$counts" \
  "$(sqlval "SELECT max(name) FROM schema_migrations")" \
  "$(sqlval "SELECT extversion FROM pg_extension WHERE extname = 'vector'")" \
  "$(sqlval "SHOW server_version")" "$PG_IMAGE" <<'EOF'
import hashlib, json, os, sys
base, ref, table, sha, stamp, counts, migration, pgvector, server, image = sys.argv[1:]
h = hashlib.sha256()
with open(base + ".dump", "rb") as fh:
    for block in iter(lambda: fh.read(1 << 20), b""):
        h.update(block)
manifest = {
    "pipeline": ref,
    "chunk_table": table,
    "dump": os.path.basename(base) + ".dump",
    "sha256": h.hexdigest(),
    "bytes": os.path.getsize(base + ".dump"),
    "rows": json.loads(counts),
    "migration": migration,
    "pgvector": pgvector,
    "server_version": server,
    "image": image,
    "git_sha": sha,
    "created": stamp,
}
with open(base + ".manifest.json", "w") as fh:
    json.dump(manifest, fh, indent=2)
    fh.write("\n")
print(json.dumps({k: manifest[k] for k in ("dump", "bytes", "migration", "pgvector")}))
EOF

if [[ -n "$bucket" ]]; then
  for f in "$base.dump" "$base.manifest.json"; do
    aws s3 cp "$out/$f" "$bucket/serving/$ref/$f"
  done
  echo "uploaded to $bucket/serving/$ref/$base.*"
fi
