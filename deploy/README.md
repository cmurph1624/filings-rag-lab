# Production stack

`compose.prod.yaml` runs three containers on one host:

- **caddy** terminates TLS and holds the only published ports (80, 443).
- **api** is `frl serve` from the image built by the root `Dockerfile`.
- **db** is Postgres 17 + pgvector, pinned to the Nucbox's image digest. It holds two databases:
  - `frl_index` is restored whole from the Nucbox's dump and can always be rebuilt.
  - `frl_app` holds `served_queries`, `feedback` and `rate_events`. It is the only data that can't be rebuilt, and the only data backed up.

The deploy (P4 PR 8) renders both env files from SSM Parameter Store. Secrets never go in
the repo, the image or Terraform state.

## Stack env (compose interpolation)

| Variable | Example | |
|---|---|---|
| `FRL_IMAGE` | `<ecr>/frl:<sha>` | the api image |
| `FRL_ENV_FILE` | `/run/frl/env` (default) | the api's env file, on tmpfs |
| `POSTGRES_PASSWORD` | | db superuser; also inside the api's URLs |
| `FRL_HOST` | `filings.stigmergylabs.net` | |
| `FRL_TLS` | an email, or `internal` | `internal` = Caddy's own CA (rehearsals) |
| `ACME_CA` | LE staging URL | until the first real certificate |
| `SERVE_TAG` | `prod` (default), `smoke` | tags rows and traces |
| `FRL_BIND`, `FRL_HTTP_PORT`, `FRL_HTTPS_PORT` | `0.0.0.0`, 80, 443 | |

## api env file

```
DATABASE_URL=postgresql://frl_reader:<reader pw>@db:5432/frl_index   # read-only; made by restore_index.sh
APP_DATABASE_URL=postgresql://frl:<pw>@db:5432/frl_app
ANTHROPIC_API_KEY=...
VOYAGE_API_KEY=...
IP_HASH_SALT=...
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=https://cloud.langfuse.com
```

## Commands

The api reads `frl_index` at startup, so on a new host the index is restored before the api
first starts:

```sh
C="docker compose --env-file stack.env -f compose.prod.yaml"
$C up -d db --wait
# Migrate the app database (the index database comes migrated in its dump).
$C run --rm --no-deps -e DATABASE_URL="$APP_DATABASE_URL" api frl db migrate
COMPOSE="$C" FRL_READER_PASSWORD=... ./restore_index.sh s3://<bucket>/serving/structured@1/<x>.dump
$C up -d --wait
```

## The index

`scripts/serving_dump.sh structured@1 [--upload s3://<bucket>]` runs on the Nucbox. It dumps
only the tables serving reads:
- the pipeline's chunks
- `documents`, `corpora` and `corpus_documents`
- `benchmark_docs` and `xbrl_facts`
- `pipelines` and `schema_migrations`

It writes about 600 MB with a manifest of row counts, sha256, migration level and versions.

`restore_index.sh` restores into `frl_index_new`, without the HNSW and full-text indexes,
because serving scans a scope's chunks exactly. It checks the manifest, grants `frl_reader`,
then swaps the database names during a brief api stop. The previous index stays as
`frl_index_old` until the next restore.

Rehearsed on the Nucbox (2026-11-07):
- **Speed:** a 572 MB dump restored in 38–40 s, with about 8 s of api downtime for the swap. `frl_index` is 1.2 GB.
- **Retrieval:** identical to the source database on all 150 open-subset questions, reading as `frl_reader`. Scope, route and facts were identical too.

On failure, `restore_index.sh` cleans up after itself:
- **Before the swap:** it drops `frl_index_new`. The live index is untouched.
- **During the swap:** it puts the old name back and restarts the api.
- **Unhealthy after the swap:** it rolls back by itself.

### Rollback

The previous index is kept as `frl_index_old`, so rolling back by hand is a rename:

```sh
$C stop api
$C exec -T db psql -U frl -d postgres -c "DROP DATABASE IF EXISTS frl_index_bad"
$C exec -T db psql -U frl -d postgres -1 \
  -c "ALTER DATABASE frl_index RENAME TO frl_index_bad" \
  -c "ALTER DATABASE frl_index_old RENAME TO frl_index"
$C up -d --wait api
```

The next restore drops `frl_index_bad`.

Never run `down -v` here. It deletes `frl_app` and Caddy's certificates.

## Privacy

Neither the API nor Caddy logs client addresses:
- Caddy's access log and its own log (errors, certificates) drop them, and its TLS-handshake-error lines are excluded.
- uvicorn's access log is off.
- The rate limit stores a salted hash.

To check: stop the api, request the site, then search the logs for the client's address. It shouldn't appear:
`docker compose logs caddy api | grep <client ip>`.
