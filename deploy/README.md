# Production stack

`compose.prod.yaml` runs three containers on one host:

- **caddy** terminates TLS and holds the only published ports (80, 443).
- **api** is `frl serve` from the image built by the root `Dockerfile`.
- **db** is Postgres 17 + pgvector, pinned to the Nucbox's image digest. It holds two databases:
  - `frl_index` is restored whole from the Nucbox's dump and can always be rebuilt.
  - `frl_app` holds `served_queries`, `feedback` and `rate_events`. It is the only data that can't be rebuilt, and the only data backed up.

The deploy renders both env files from SSM Parameter Store (`render_env.sh`). Secrets never
go in the repo, the image or Terraform state. On the server they sit in `/run/frl` (tmpfs,
root-only) and, as for any container, in Docker's container config on the encrypted disk.

## On the server

```
/etc/frl/host.env          region, ECR repository, bucket, hostname (cloud-init)
/opt/frl/releases/<sha>/   this directory, one per deployed commit (the last five kept)
/opt/frl/current           → the live release
/opt/frl/previous          the SHA before it (rollback target)
/opt/frl/state.env         FRL_IMAGE and ACME_CA of the live release
/run/frl/env, stack.env    rendered from SSM on each deploy
/usr/local/bin/frl-compose docker compose for the live stack: frl-compose ps | logs -f api | …
```

There is no SSH. Get a shell with `aws ssm start-session --profile frl-admin --target <instance>`
(needs the Session Manager plugin), or run one command with
`AWS_PROFILE=frl-admin scripts/ssm_run.sh 60 note <<< 'frl-compose ps'`.

## Deploying

Both workflows run by hand from the Actions tab, in the `production` environment:

- **deploy** (`sha`, `acme_staging`): builds the arm64 image (skipped if ECR has it), stages
  this directory in S3, runs `deploy.sh <sha>` on the server through SSM Run Command, and checks
  that `https://<host>/version` reports the SHA. **Rollback:** run it with the previous SHA.
- **restore-index** (`dump`, an S3 key under `serving/`): runs `restore_index.sh` on the
  server, then starts the stack.

`deploy.sh` migrates `frl_app` and starts the api and Caddy only once an index has been restored.

### First deploy

1. `AWS_PROFILE=frl-admin scripts/serving_dump.sh structured@1 --upload s3://frl-271200701471-data`
   on the Nucbox (under `op run`, for `DATABASE_URL`).
2. **deploy** with the SHA and `acme_staging` on. The db starts and `frl_app` is migrated, but
   there's no index yet.
3. **restore-index** with the dump's key. The site comes up with a staging certificate.
4. `AWS_PROFILE=frl-admin scripts/smoke_prod.sh <sha> -k`
5. **deploy** again with `acme_staging` off, for the real certificate, then
   `scripts/smoke_prod.sh <sha>` without `-k`.

## Stack env (compose interpolation)

| Variable | Example | |
|---|---|---|
| `FRL_IMAGE` | `<ecr>/frl:<sha>` | the api image |
| `FRL_ENV_FILE` | `/run/frl/env` (default) | the api's env file, on tmpfs |
| `POSTGRES_PASSWORD` | | db superuser; also inside the api's URLs |
| `FRL_HOST` | `filings.stigmergylabs.net` | |
| `FRL_TLS` | an email, or `internal` | `internal` = Caddy's own CA (rehearsals) |
| `ACME_CA` | LE staging URL | until the first real certificate |
| `SERVE_TAG` | `prod` (default) | tags rows and traces; requests with the smoke token are tagged `smoke` |
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
LANGFUSE_HOST=https://cloud.langfuse.com    # the default
SMOKE_TOKEN=...                             # X-Smoke-Token: tags a query smoke
```

## Commands

The api reads `frl_index` at startup, so on a new host the index is restored before the api
first starts:

```sh
C="docker compose --env-file stack.env -f compose.prod.yaml"   # on the server: frl-compose
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
