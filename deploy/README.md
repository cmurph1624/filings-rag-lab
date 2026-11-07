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
DATABASE_URL=postgresql://frl:<pw>@db:5432/frl_index
APP_DATABASE_URL=postgresql://frl:<pw>@db:5432/frl_app
ANTHROPIC_API_KEY=...
VOYAGE_API_KEY=...
IP_HASH_SALT=...
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=https://cloud.langfuse.com
```

## Commands

```sh
C="docker compose --env-file stack.env -f compose.prod.yaml"
$C up -d db --wait
# Migrate the app database (the index database comes migrated in its dump).
$C run --rm --no-deps -e DATABASE_URL="$APP_DATABASE_URL" api frl db migrate
$C up -d --wait
```

Never run `down -v` here. It deletes `frl_app` and Caddy's certificates.

## Privacy

Neither the API nor Caddy logs client addresses:
- Caddy's access log and its own log (errors, certificates) drop them, and its TLS-handshake-error lines are excluded.
- uvicorn's access log is off.
- The rate limit stores a salted hash.

To check: stop the api, request the site, then search the logs for the client's address. It shouldn't appear:
`docker compose logs caddy api | grep <client ip>`.
