#!/usr/bin/env bash
# Deploy one commit on the server. Run as root by SSM Run Command from the deploy workflow
# (.github/workflows/deploy.yml), from the release directory it unpacked:
#
#   ACME_STAGING=0|1 /opt/frl/releases/<sha>/deploy.sh <sha>
#
# Steps: pull <ecr>/frl:<sha>; point /opt/frl/current here and render the env from SSM; start
# the db and migrate frl_app; start the api and Caddy (only once an index has been restored:
# until then the api has nothing to read); record this release as good and the one before as
# previous. On failure /opt/frl/current goes back to the last good release. Also installs the
# daily backup timer (backup.sh).
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
# The last release that came up (only written on success), not whatever /opt/frl/current
# points at: a failed deploy may have left that on a broken release. Servers from before
# /opt/frl/good existed fall back to /opt/frl/current once.
if [[ -s /opt/frl/good ]]; then
  prev="$(cat /opt/frl/good)"
else
  prev="$(readlink /opt/frl/current 2>/dev/null | xargs -r basename || true)"
fi

say "pulling $image"
aws ecr get-login-password | docker login --username AWS --password-stdin "${FRL_ECR%%/*}" >/dev/null
docker pull -q "$image"

install -m 0755 "$HERE/frl-compose" /usr/local/bin/frl-compose
# The daily frl_app backup. Its service runs /opt/frl/current/backup.sh, i.e. the live release's.
install -m 0644 "$HERE"/systemd/frl-backup.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q --now frl-backup.timer
umask 077
# Compose always runs through /opt/frl/current, so its bind-mount paths (Caddyfile, initdb)
# stay the same across releases: a different path would make Compose recreate the db and
# Caddy on every deploy. So /opt/frl/current points at this release while it deploys; on
# failure it goes back to the last good release (/opt/frl/good) with that release's env.
# state.env, good and previous change only once the new release is up.
next=/opt/frl/state.env.next
printf 'FRL_IMAGE=%s\nACME_CA=%s\n' "$image" "$acme" >"$next"
point() { ln -sfn "$1" /opt/frl/current.new && mv -T /opt/frl/current.new /opt/frl/current; }
dc() { docker compose --env-file /run/frl/stack.env -f /opt/frl/current/compose.prod.yaml "$@"; }
back_to_good() {
  if [[ -n "$prev" && -d "/opt/frl/releases/$prev" ]]; then
    point "/opt/frl/releases/$prev" && /opt/frl/current/render_env.sh || true
    echo "deploy failed; /opt/frl/current is back on $prev" >&2
  else
    rm -f /opt/frl/current  # no release has come up yet
    echo "deploy failed; no release has come up yet, so /opt/frl/current is removed" >&2
  fi
}
trap back_to_good ERR
point "$HERE"
FRL_STATE="$next" "$HERE/render_env.sh"

say "starting the db; migrating frl_app"
db_before="$(dc ps -q db 2>/dev/null || true)"
dc up -d --wait db
# A recreated db drops the api's connections. The api would notice through its healthcheck
# and restart itself, but restart it now rather than serve errors until then.
if [[ -n "$db_before" && "$(dc ps -q db)" != "$db_before" && -n "$(dc ps -q api)" ]]; then
  say "the db was recreated; restarting the api"
  dc restart api
fi
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
echo "$sha" >/opt/frl/good
if [[ -n "$prev" && "$prev" != "$sha" ]]; then echo "$prev" >/opt/frl/previous; fi
echo "FRL_DEPLOY_STATUS=$status"
# Keep the last five releases' bundles.
ls -1t /opt/frl/releases | tail -n +6 | while read -r old; do
  [[ "$old" == "$sha" || "$old" == "$prev" ]] || rm -rf "/opt/frl/releases/$old"
done
say "deployed $sha (previous: ${prev:-none})"
