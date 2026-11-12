#!/usr/bin/env bash
# Daily backup of frl_app, the only data on the server that can't be rebuilt (frl_index is
# restored from the Nucbox's dump and never backed up). Run as root by frl-backup.timer:
#
#   /opt/frl/current/backup.sh
#
# Dumps frl_app through the db container (custom format, compressed), checks the archive lists
# the app's tables, uploads it to s3://$FRL_BUCKET/backups/frl_app/ and keeps the newest 7
# locally in /var/backups/frl. The instance role may only *put* under backups/: it can't read,
# list or delete old backups, so a compromised server can't destroy them. S3 keeps them 90 days.
#
# Failures show in `systemctl status frl-backup` / `journalctl -u frl-backup`, and in the ops
# health check (scripts/prod_health.sh), which fails when the newest backup is over 30 hours old.
# Restore: see deploy/README.md ("Backups").
set -euo pipefail
export PATH="/snap/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
set -a; . /etc/frl/host.env; set +a

dir=/var/backups/frl
keep=7
name="frl_app-$(date -u +%Y-%m-%dT%H%M%SZ).dump"
install -d -m 0700 "$dir"
umask 077
part="$dir/$name.part"
trap 'rm -f "$part"' EXIT

frl-compose exec -T db pg_dump -U frl -d frl_app -Fc >"$part"
size="$(stat -c%s "$part")"
(( size > 1024 )) || { echo "dump too small ($size bytes); not uploading" >&2; exit 1; }
# A truncated archive fails to list; a wrong database lists without these tables.
toc="$(frl-compose exec -T db pg_restore --list <"$part")"
for table in served_queries feedback rate_events; do
  grep -q "TABLE DATA public $table " <<<"$toc" || { echo "dump has no data for $table" >&2; exit 1; }
done
mv "$part" "$dir/$name"

aws s3 cp --only-show-errors --checksum-algorithm SHA256 "$dir/$name" "s3://$FRL_BUCKET/backups/frl_app/$name"
ls -1t "$dir"/frl_app-*.dump | tail -n +$((keep + 1)) | xargs -r rm -f
echo "backed up $name ($size bytes) to s3://$FRL_BUCKET/backups/frl_app/"
