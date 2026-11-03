#!/usr/bin/env bash
#
# Tests for scripts/tap-token-preflight.sh.
#
# Offline: a stub `git` on PATH plays GitHub. It authenticates a request only through the
# github.com-scoped http.extraheader in GIT_CONFIG_*, serves the clone to any valid token, and
# answers a push only for the write token, the way GitHub's receive-pack advertisement does. It
# records every argv so the tests can prove the token never reaches a command line or the log.
#
# Run: ./scripts/test-tap-token-preflight.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PREFLIGHT="$HERE/tap-token-preflight.sh"
PASS=0; FAIL=0

WRITE_TOKEN="fake-token-with-write"
READ_TOKEN="fake-token-read-only"
DEAD_TOKEN="fake-token-revoked"

STUB_DIR="$(mktemp -d)"
trap 'rm -rf "$STUB_DIR"' EXIT
LOG="$STUB_DIR/argv.log"
cat >"$STUB_DIR/git" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_LOG"
header=""
for ((i = 0; i < ${GIT_CONFIG_COUNT:-0}; i++)); do
  key="GIT_CONFIG_KEY_$i"; val="GIT_CONFIG_VALUE_$i"
  [ "${!key}" = "http.https://github.com/.extraheader" ] && header="${!val}"
done
token="$(printf '%s' "${header#Authorization: Basic }" | base64 -d 2>/dev/null | sed 's/^x-access-token://')"
case " $* " in
  *" clone "*)
    if [ "$token" = "$STUB_WRITE_TOKEN" ] || [ "$token" = "$STUB_READ_TOKEN" ]; then
      mkdir -p "${@: -1}/.git"; exit 0
    fi
    echo "remote: Invalid username or token. Password authentication is not supported for Git operations." >&2
    exit 128 ;;
  *" push "*)
    case " $* " in *" --dry-run "*) ;; *) echo "REAL PUSH" >>"$STUB_LOG" ;; esac
    [ "$token" = "$STUB_WRITE_TOKEN" ] && exit 0
    echo "remote: Permission to teddychan/homebrew-tap.git denied." >&2
    echo "fatal: unable to access 'https://github.com/teddychan/homebrew-tap.git/': The requested URL returned error: 403" >&2
    exit 128 ;;
esac
echo "stub git: unexpected command: $*" >&2
exit 2
STUB
chmod +x "$STUB_DIR/git"

OUT=""; RC=0
run() {
  : >"$LOG"
  OUT="$(env PATH="$STUB_DIR:$PATH" STUB_LOG="$LOG" \
    STUB_WRITE_TOKEN="$WRITE_TOKEN" STUB_READ_TOKEN="$READ_TOKEN" "$@" bash "$PREFLIGHT" 2>&1)"
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

echo "tap-token-preflight.sh"

run env -u PUBLIC_RELEASE_TOKEN
expect "an unset token passes with a warning" "$([ $RC -eq 0 ] && has "$OUT" "::warning::PUBLIC_RELEASE_TOKEN not set" || echo 0)"
expect "an unset token never contacts the tap" "$([ ! -s "$LOG" ] && echo 1 || echo 0)"

run env PUBLIC_RELEASE_TOKEN=""
expect "an empty token is treated as unset" "$([ $RC -eq 0 ] && has "$OUT" "::warning::" || echo 0)"

run env PUBLIC_RELEASE_TOKEN="$WRITE_TOKEN"
expect "a token with write access passes" "$([ $RC -eq 0 ] && has "$OUT" "can push to teddychan/homebrew-tap" || echo 0)"
expect "the push is a dry run, so no branch is created" "$(has "$(cat "$LOG")" "--dry-run")"
expect "no real push was attempted" "$(lacks "$(cat "$LOG")" "REAL PUSH")"
expect "the probe targets a branch, never main" "$(grep -q 'HEAD:refs/heads/release-preflight-' "$LOG" && echo 1 || echo 0)"
expect "the write token never appears in git's argv" "$(lacks "$(cat "$LOG")" "$WRITE_TOKEN")"

run env PUBLIC_RELEASE_TOKEN="$READ_TOKEN"
expect "a read-only token is rejected" "$([ $RC -ne 0 ] && echo 1 || echo 0)"
expect "the rejection is an ::error:: naming the cause" "$(has "$OUT" "::error::PUBLIC_RELEASE_TOKEN cannot push to teddychan/homebrew-tap: remote: Permission")"
expect "the rejection names the remedy" "$(has "$OUT" "Rotate the PUBLIC_RELEASE_TOKEN secret")"
expect "the read-only token never appears in the log" "$(lacks "$OUT" "$READ_TOKEN")"
expect "the read-only token never appears in git's argv" "$(lacks "$(cat "$LOG")" "$READ_TOKEN")"

run env PUBLIC_RELEASE_TOKEN="$DEAD_TOKEN"
expect "an invalid token is rejected" "$([ $RC -ne 0 ] && echo 1 || echo 0)"
expect "the rejection quotes GitHub's reason" "$(has "$OUT" "Invalid username or token")"
expect "the invalid token never appears in the log" "$(lacks "$OUT" "$DEAD_TOKEN")"

run env PUBLIC_RELEASE_TOKEN="$WRITE_TOKEN" TAP_REPO=someone/other-tap
expect "TAP_REPO selects the repo probed" "$(grep -q 'github.com/someone/other-tap.git' "$LOG" && echo 1 || echo 0)"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
echo "All tap-token-preflight tests passed."
