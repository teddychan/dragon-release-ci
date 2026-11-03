#!/usr/bin/env bash
#
# Tests for scripts/site-dispatch.sh.
#
# Offline: a stub `curl` on PATH plays the GitHub API. It serves the site's app index, answers the
# dispatch POST with STUB_DISPATCH_CODE and a fixed Date header, and lists repository_dispatch runs
# created at STUB_RUN_CREATED (from the STUB_RUN_FROM_CALL'th listing on). It records every argv.
#
# Run: ./scripts/test-site-dispatch.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DISPATCH="$HERE/site-dispatch.sh"
PASS=0; FAIL=0

STUB_DIR="$(mktemp -d)"
trap 'rm -rf "$STUB_DIR"' EXIT
LOG="$STUB_DIR/argv.log"
SUMMARY="$STUB_DIR/summary.md"
cat >"$STUB_DIR/curl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_LOG"
out=/dev/stdout; headers=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift ;;
    -D) headers="$2"; shift ;;
    -H|-w|-X|-d) shift ;;
    https://*) url="$1" ;;
  esac
  shift
done
case "$url" in
  */contents/i18n/apps/_index.json)
    printf '%s' "$STUB_INDEX" ;;
  */dispatches)
    [ "$STUB_DISPATCH_CODE" = "000" ] && { echo "curl: (7) Failed to connect" >&2; exit 7; }
    printf 'HTTP/2 %s\r\ndate: Tue, 03 Nov 2026 11:00:00 GMT\r\n\r\n' "$STUB_DISPATCH_CODE" >"$headers"
    if [ "$STUB_DISPATCH_CODE" = "204" ]; then : >"$out"
    else printf '{"message":"Resource not accessible by personal access token","status":"403"}' >"$out"; fi
    printf '%s' "$STUB_DISPATCH_CODE" ;;
  */actions/runs\?event=repository_dispatch*)
    n=$(( $(cat "$STUB_CALLS" 2>/dev/null || echo 0) + 1 )); echo "$n" >"$STUB_CALLS"
    old='{"created_at":"2026-11-03T10:50:00Z","html_url":"https://github.com/site/runs/1"}'
    if [ "$n" -ge "${STUB_RUN_FROM_CALL:-1}" ] && [ -n "${STUB_RUN_CREATED:-}" ]; then
      new="{\"created_at\":\"$STUB_RUN_CREATED\",\"html_url\":\"https://github.com/site/runs/2\"},"
    else new=""; fi
    printf '{"workflow_runs":[%s%s]}' "$new" "$old" >"$out"
    printf '%s' "${STUB_RUNS_CODE:-200}" ;;
  *) echo "stub curl: unexpected URL: $url" >&2; exit 2 ;;
esac
STUB
chmod +x "$STUB_DIR/curl"

INDEX='{"apps":["spectacle-2","ice-2"]}'
OUT=""; RC=0
run() {
  : >"$LOG"; : >"$SUMMARY"; rm -f "$STUB_DIR/calls"
  OUT="$(env PATH="$STUB_DIR:$PATH" STUB_LOG="$LOG" STUB_CALLS="$STUB_DIR/calls" \
    STUB_INDEX="$INDEX" GITHUB_STEP_SUMMARY="$SUMMARY" SITE_RUN_POLL=0 SITE_RUN_WAIT=0 \
    PUBLIC_RELEASE_TOKEN=fake-site-token APP_SLUG=spectacle-2 "$@" bash "$DISPATCH" 2>&1)"
  RC=$?
}

expect() {
  local name="$1" ok="$2"
  if [ "$ok" = 1 ]; then
    PASS=$((PASS+1)); echo "  ok    $name"
  else
    FAIL=$((FAIL+1)); echo "  FAIL  $name"; echo "$OUT" | sed 's/^/        /'
  fi
}
has() { grep -qF -- "$2" <<<"$1" && echo 1 || echo 0; }
lacks() { grep -qF -- "$2" <<<"$1" && echo 0 || echo 1; }

echo "site-dispatch.sh"

run env -u PUBLIC_RELEASE_TOKEN
expect "an unset token skips with a warning" "$([ $RC -eq 0 ] && has "$OUT" "::warning::PUBLIC_RELEASE_TOKEN not set" || echo 0)"
expect "an unset token never calls the API" "$([ ! -s "$LOG" ] && echo 1 || echo 0)"

run env APP_SLUG=dragon-sample-app STUB_DISPATCH_CODE=204
expect "an app with no page on the site exits 0" "$([ $RC -eq 0 ] && has "$OUT" "has no page on www.dragonapp.com" || echo 0)"
expect "...without dispatching" "$(lacks "$(cat "$LOG")" "/dispatches")"

run env STUB_DISPATCH_CODE=403
expect "a 403 dispatch fails the step" "$([ $RC -eq 1 ] && echo 1 || echo 0)"
expect "...with an ::error:: carrying the code and GitHub's message" "$([ "$(has "$OUT" "::error::")$(has "$OUT" "HTTP 403")$(has "$OUT" "Resource not accessible by personal access token")" = 111 ] && echo 1 || echo 0)"
expect "...written to the run summary" "$(has "$(cat "$SUMMARY")" "HTTP 403")"
expect "...and no runs are polled" "$(lacks "$(cat "$LOG")" "actions/runs")"

run env STUB_DISPATCH_CODE=000
expect "an unreachable API fails the step as HTTP 000" "$([ $RC -eq 1 ] && has "$OUT" "HTTP 000" || echo 0)"

run env STUB_DISPATCH_CODE=204 STUB_RUN_CREATED=2026-11-03T11:00:03Z
expect "a 204 followed by a new site run passes" "$([ $RC -eq 0 ] && has "$OUT" "https://github.com/site/runs/2" || echo 0)"
expect "...and nothing goes to the run summary" "$([ ! -s "$SUMMARY" ] && echo 1 || echo 0)"
expect "...and the runs listing is read without the token" "$(grep "actions/runs" "$LOG" | grep -qF "Authorization" && echo 0 || echo 1)"

run env STUB_DISPATCH_CODE=204 STUB_RUN_CREATED=2026-11-03T10:59:57Z
expect "a run stamped within 5 s before GitHub's Date header counts" "$([ $RC -eq 0 ] && echo 1 || echo 0)"

run env STUB_DISPATCH_CODE=204
expect "a 204 with only an older site run fails the step" "$([ $RC -eq 1 ] && has "$OUT" "::error::" || echo 0)"
expect "...saying no run started" "$(has "$OUT" "no site workflow run started")"
expect "...in the run summary too" "$(has "$(cat "$SUMMARY")" "no site workflow run started")"
expect "...and does not mistake the older run for this one" "$(lacks "$OUT" "https://github.com/site/runs/1")"

run env STUB_DISPATCH_CODE=204 STUB_RUN_CREATED=2026-11-03T11:00:20Z STUB_RUNS_CODE=403
expect "a runs listing the API refuses fails the step, naming its code" "$([ $RC -eq 1 ] && has "$OUT" "Actions API answered HTTP 403" || echo 0)"

run env STUB_DISPATCH_CODE=204 STUB_RUN_CREATED=2026-11-03T11:00:20Z STUB_RUN_FROM_CALL=3 SITE_RUN_WAIT=30
expect "a run that appears on a later poll passes" "$([ $RC -eq 0 ] && [ "$(cat "$STUB_DIR/calls")" = 3 ] && echo 1 || echo 0)"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
