#!/usr/bin/env bash
# Deploy one commit on the server. Run as root by SSM Run Command from the deploy workflow
# (.github/workflows/deploy.yml), from the release directory it unpacked:
#
#   ACME_STAGING=0|1 /opt/frl/releases/<sha>/deploy.sh <sha>
#
# Steps: pull <ecr>/frl:<sha>; render the env from SSM; start the db and migrate frl_app;
# start the api and Caddy (only once an index has been restored: until then the api has
# nothing to read); then point /opt/frl/current at this release and record the previous one.
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
# The new release takes over (current, state.env, previous) only once it's up. Until then
# compose runs from this directory with the new state; on failure the env files are rendered
# again for the live release, so frl-compose keeps matching it.
next=/opt/frl/state.env.next
printf 'FRL_IMAGE=%s\nACME_CA=%s\n' "$image" "$acme" >"$next"
dc() { docker compose --env-file /run/frl/stack.env -f "$HERE/compose.prod.yaml" "$@"; }
restore_env() {
  if [[ -n "$prev" ]]; then /opt/frl/current/render_env.sh || true; fi
  echo "deploy failed; /opt/frl/current is still ${prev:-unset}" >&2
}
trap restore_env ERR
FRL_STATE="$next" "$HERE/render_env.sh"

say "starting the db; migrating frl_app"
dc up -d --wait db
app_url="$(grep '^APP_DATABASE_URL=' /run/frl/env | cut -d= -f2-)"
dc run --rm -T --no-deps -e DATABASE_URL="$app_url" api frl db migrate

indexed="$(dc exec -T db psql -X -U frl -d frl_index -Atc \
  "SELECT to_regclass('public.pipelines') IS NOT NULL")"
if [[ "$indexed" != t ]]; then
  say "no index restored yet: the api and Caddy stay down. Run the restore-index workflow."
  status=no-index
else
  say "starting the api and Caddy"
  dc up -d --wait --remove-orphans
  status=live
fi
trap - ERR

mv "$next" /opt/frl/state.env
if [[ -n "$prev" && "$prev" != "$sha" ]]; then echo "$prev" >/opt/frl/previous; fi
ln -sfn "$HERE" /opt/frl/current.new && mv -T /opt/frl/current.new /opt/frl/current
echo "FRL_DEPLOY_STATUS=$status"
# Keep the last five releases' bundles.
ls -1t /opt/frl/releases | tail -n +6 | while read -r old; do
  [[ "$old" == "$sha" || "$old" == "$prev" ]] || rm -rf "/opt/frl/releases/$old"
done
say "deployed $sha (previous: ${prev:-none})"
