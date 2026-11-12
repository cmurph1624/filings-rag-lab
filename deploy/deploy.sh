#!/usr/bin/env bash
# Deploy one commit on the server. Run as root by SSM Run Command from the deploy workflow
# (.github/workflows/deploy.yml), from the release directory it unpacked:
#
#   ACME_STAGING=0|1 /opt/frl/releases/<sha>/deploy.sh <sha>
#
# Steps: render the env from SSM; pull <ecr>/frl:<sha>; point /opt/frl/current at this
# release; start the db and migrate frl_app; start the api and Caddy (only once an index has
# been restored: until then the api has nothing to read); record the previous release.
#
# Rollback: run the workflow again with the previous SHA (cat /opt/frl/previous).
set -euo pipefail
export PATH="/snap/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
sha="${1:?usage: deploy.sh <sha>}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "expected a full commit SHA" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
set -a; . /etc/frl/host.env; set +a
say() { echo "[$(date -u +%H:%M:%S)] $*"; }

image="$FRL_ECR:$sha"
acme="https://acme-v02.api.letsencrypt.org/directory"
[[ "${ACME_STAGING:-0}" == 1 ]] && acme="https://acme-staging-v02.api.letsencrypt.org/directory"
prev="$(readlink /opt/frl/current 2>/dev/null | xargs -r basename || true)"

say "pulling $image"
aws ecr get-login-password | docker login --username AWS --password-stdin "${FRL_ECR%%/*}" >/dev/null
docker pull -q "$image"

install -m 0755 "$HERE/frl-compose" /usr/local/bin/frl-compose
umask 077
printf 'FRL_IMAGE=%s\nACME_CA=%s\n' "$image" "$acme" >/opt/frl/state.env.new
mv /opt/frl/state.env.new /opt/frl/state.env
ln -sfn "$HERE" /opt/frl/current.new && mv -T /opt/frl/current.new /opt/frl/current
"$HERE/render_env.sh"

say "starting the db; migrating frl_app"
frl-compose up -d --wait db
app_url="$(grep '^APP_DATABASE_URL=' /run/frl/env | cut -d= -f2-)"
frl-compose run --rm --no-deps -e DATABASE_URL="$app_url" api frl db migrate

indexed="$(frl-compose exec -T db psql -X -U frl -d frl_index -Atc \
  "SELECT to_regclass('public.pipelines') IS NOT NULL")"
if [[ "$indexed" != t ]]; then
  say "no index restored yet: the api and Caddy stay down. Run the restore-index workflow."
  echo "FRL_DEPLOY_STATUS=no-index"
else
  say "starting the api and Caddy"
  frl-compose up -d --wait --remove-orphans
  echo "FRL_DEPLOY_STATUS=live"
fi
if [[ -n "$prev" && "$prev" != "$sha" ]]; then echo "$prev" >/opt/frl/previous; fi
# Keep the last five releases' bundles.
ls -1t /opt/frl/releases | tail -n +6 | while read -r old; do
  [[ "$old" == "$sha" || "$old" == "$prev" ]] || rm -rf "/opt/frl/releases/$old"
done
say "deployed $sha (previous: ${prev:-none})"
