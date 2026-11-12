#!/usr/bin/env bash
# Smoke-test the live site from the Nucbox, after a deploy or an index restore:
#
#   AWS_PROFILE=frl-admin scripts/smoke_prod.sh [expected-sha] [-k]
#
# Checks /healthz, /version (against expected-sha, if given), one answered open-subset question
# with the gold figure and a citation (excerpt or fact), one out-of-coverage refusal at $0, and a feedback round trip. The
# questions carry the smoke token (from SSM), so they're tagged "smoke" and kept out of the
# stats. Costs about $0.02. -k accepts a Let's Encrypt staging certificate.
set -euo pipefail
host="${FRL_HOST:-filings.stigmergylabs.net}"
want="" k=()
for a in "$@"; do
  case "$a" in
    -k) k=(-k) ;;
    *) want="$a" ;;
  esac
done
base="https://$host"
token="$(aws ssm get-parameter --region us-east-2 --with-decryption --name /frl/prod/smoke_token \
  --query Parameter.Value --output text)"
[[ -n "$token" && "$token" != placeholder* ]] || { echo "smoke_token isn't set in SSM" >&2; exit 1; }

fail() { echo "FAIL: $*" >&2; exit 1; }
get() { curl -fsS "${k[@]}" --max-time 15 "$base$1"; }
# The token goes in through a file descriptor, not the command line.
ask() {
  curl -sS "${k[@]}" --max-time 120 -H 'Content-Type: application/json' \
    -H @<(printf 'X-Smoke-Token: %s\n' "$token") \
    --data "$(jq -n --arg q "$1" '{question: $q}')" "$base/ask"
}

get /healthz | jq -e '.ok == true' >/dev/null || fail "healthz"
echo "ok   healthz"
v="$(get /version)"
[[ -z "$want" || "$(jq -r .sha <<<"$v")" == "$want" ]] || fail "version is $(jq -r .sha <<<"$v"), want $want"
echo "ok   version $(jq -r '.sha[0:12] + " " + .pipeline + " " + .model' <<<"$v")"

a="$(ask "What is the FY2018 capital expenditure amount (in USD millions) for 3M?")"
# Gold: $1,577M (FinanceBench). The router sends it to the XBRL facts, so the citation may be
# a fact [F1] rather than an excerpt [n]: either counts.
jq -e '.status == "answered" and (.answer | test("1,?577"))
       and (any(.excerpts[]; .cited) or (.facts | length) > 0)' \
  <<<"$a" >/dev/null || fail "answer: $(jq -c '{status, refusal, error, answer: .answer[0:200]}' <<<"$a")"
echo "ok   answered in $(jq -r .latency_s <<<"$a") s for \$$(jq -r .cost_usd <<<"$a"): $(jq -r '.answer[0:100]' <<<"$a")…"
qid="$(jq -r .query_id <<<"$a")"

r="$(ask "What was Tesla's revenue in 2022?")"
jq -e '.status == "refused" and .refusal == "out_of_coverage" and .cost_usd == 0' <<<"$r" >/dev/null ||
  fail "refusal: $(jq -c '{status, refusal, cost_usd}' <<<"$r")"
echo "ok   out-of-coverage refused at \$0"

code="$(curl -sS "${k[@]}" -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  --data "$(jq -n --arg id "$qid" '{query_id: $id, rating: "down", comment: "smoke test"}')" \
  "$base/feedback")"
[[ "$code" == 204 ]] || fail "feedback returned $code"
echo "ok   feedback recorded"
echo "smoke passed ($qid)"
