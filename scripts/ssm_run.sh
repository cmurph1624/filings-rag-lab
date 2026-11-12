#!/usr/bin/env bash
# Run a bash script (stdin) on the server through SSM Run Command and wait for it:
#
#   scripts/ssm_run.sh <timeout-seconds> <comment> < script.sh
#
# The server is the one running instance tagged app=frl. Prints the script's output (SSM keeps
# the last 24,000 characters) and exits non-zero unless it succeeded. Used by the deploy and
# restore-index workflows; works from a laptop too with an admin profile (AWS_PROFILE).
set -euo pipefail
timeout="${1:?usage: ssm_run.sh <timeout-seconds> <comment> < script}"
comment="${2:?usage: ssm_run.sh <timeout-seconds> <comment> < script}"

iid="$(aws ec2 describe-instances \
  --filters Name=tag:app,Values=frl Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
[[ "$iid" =~ ^i-[0-9a-f]+$ ]] || { echo "expected one running instance tagged app=frl, got: '$iid'" >&2; exit 1; }

# AWS-RunShellScript runs its commands with sh (dash on Ubuntu), so the script goes to bash
# whole, base64-encoded: no quoting or shebang to get wrong.
script="$(base64 -w0)"
params="$(jq -n --arg s "$script" --arg t "$timeout" \
  '{commands: ["echo \($s) | base64 -d | bash"], executionTimeout: [$t]}')"
cid="$(aws ssm send-command --instance-ids "$iid" --document-name AWS-RunShellScript \
  --comment "${comment:0:100}" --parameters "$params" --query Command.CommandId --output text)"
echo "command $cid on $iid"

deadline=$((SECONDS + timeout + 120))
while :; do
  sleep 10
  status="$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
    --query Status --output text 2>/dev/null || echo Pending)"
  case "$status" in
    Pending | InProgress | Delayed)
      (( SECONDS < deadline )) || { echo "gave up waiting (status $status)" >&2; exit 1; } ;;
    *) break ;;
  esac
done
aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
  --query StandardOutputContent --output text
err="$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
  --query StandardErrorContent --output text)"
[[ -z "$err" || "$err" == None ]] || echo "$err" >&2
echo "status: $status"
[[ "$status" == Success ]]
