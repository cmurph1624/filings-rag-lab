#!/usr/bin/env bash
# Render the stack's env files from SSM (/frl/prod/*) into /run/frl (tmpfs, root-only):
#   /run/frl/env        the api's env file (compose env_file): database URLs and API keys
#   /run/frl/stack.env  compose interpolation: POSTGRES_PASSWORD, FRL_HOST, FRL_TLS, plus
#                       /opt/frl/state.env (FRL_IMAGE, ACME_CA), which deploy.sh writes
# Stops if a required secret is missing or still the Terraform placeholder. Langfuse and the
# smoke token are optional (tracing is off without the Langfuse keys).
set -euo pipefail
export PATH="/snap/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
set -a; . /etc/frl/host.env; set +a

umask 077
mkdir -p /run/frl
aws ssm get-parameters-by-path --path /frl/prod --with-decryption \
  --query 'Parameters[].[Name,Value]' --output json |
  python3 -c '
import json, sys
from urllib.parse import quote

p = {name.rsplit("/", 1)[1]: value for name, value in json.load(sys.stdin)}
unset = lambda v: not v or v.startswith("placeholder")
required = ["anthropic_api_key", "voyage_api_key", "ip_hash_salt", "postgres_password",
            "reader_password", "acme_email"]
missing = [k for k in required if unset(p.get(k))]
if missing:
    sys.exit("not set in SSM (/frl/prod): " + ", ".join(missing))
optional = {"LANGFUSE_PUBLIC_KEY": "langfuse_public_key",
            "LANGFUSE_SECRET_KEY": "langfuse_secret_key", "SMOKE_TOKEN": "smoke_token"}

pw, rpw = quote(p["postgres_password"], safe=""), quote(p["reader_password"], safe="")
api = {
    "DATABASE_URL": f"postgresql://frl_reader:{rpw}@db:5432/frl_index",
    "APP_DATABASE_URL": f"postgresql://frl:{pw}@db:5432/frl_app",
    "ANTHROPIC_API_KEY": p["anthropic_api_key"],
    "VOYAGE_API_KEY": p["voyage_api_key"],
    "IP_HASH_SALT": p["ip_hash_salt"],
} | {k: p[n] for k, n in optional.items() if not unset(p.get(n))}
stack = {
    "POSTGRES_PASSWORD": p["postgres_password"],
    "FRL_READER_PASSWORD": p["reader_password"],  # restore_index.sh
    "FRL_TLS": p["acme_email"],
}
for path, values in (("/run/frl/env", api), ("/run/frl/stack.env", stack)):
    with open(path, "w") as fh:
        fh.writelines(f"{k}={v}\n" for k, v in values.items())
'
{
  echo "FRL_HOST=$FRL_HOST"
  [[ -f /opt/frl/state.env ]] && cat /opt/frl/state.env
} >>/run/frl/stack.env
