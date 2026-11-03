#!/usr/bin/env bash
#
# Ask www.dragonapp.com to refresh its changelog, and prove a site run actually started.
#
# This used to end in a `::warning::` on any non-204, which kept the step green. Spectacle 2
# v2.7.0 and v2.8.0 both got HTTP 403 "Resource not accessible by personal access token" here and
# nobody noticed for two releases. A failed dispatch now fails the step: `::error::`, a line in the
# run summary, exit 1. It is the last publishing step, so the release, the appcast and the cask are
# already out when it runs, and a red step here cannot half-ship anything.
#
# A 204 only means GitHub accepted the event. The site's workflow can still ignore it (wrong
# event_type, workflow disabled), so wait for a repository_dispatch run created after the dispatch,
# through the site repo's public Actions API, which needs no token.
#
# Inputs (environment):
#   PUBLIC_RELEASE_TOKEN  Optional. Unset skips the refresh with a warning.
#   APP_SLUG              Required. The app's slug in the site's i18n/apps/_index.json.
#   SITE_REPO             owner/name of the site. Default teddychan/www.dragonapp.com.
#   SITE_RUN_WAIT         Seconds to wait for the site run. Default 60.
#   SITE_RUN_POLL         Seconds between checks. Default 10.
set -euo pipefail

SITE_REPO="${SITE_REPO:-teddychan/www.dragonapp.com}"
API="https://api.github.com/repos/${SITE_REPO}"
WAIT="${SITE_RUN_WAIT:-60}"
POLL="${SITE_RUN_POLL:-10}"

if [ -z "${PUBLIC_RELEASE_TOKEN:-}" ]; then
  echo "::warning::PUBLIC_RELEASE_TOKEN not set — skipping site changelog refresh."; exit 0
fi
IDX="$(curl -sS -H 'Accept: application/vnd.github.raw' \
  -H "Authorization: Bearer ${PUBLIC_RELEASE_TOKEN}" \
  -H 'X-GitHub-Api-Version: 2022-11-28' \
  "${API}/contents/i18n/apps/_index.json" \
  || true)"
case "$IDX" in
  *"\"${APP_SLUG}\""*) : ;;
  *)
    echo "${APP_SLUG} has no page on www.dragonapp.com — nothing to refresh."
    exit 0 ;;
esac

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fail() {
  echo "::error::$1" >&2
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '### :x: www.dragonapp.com was not refreshed\n\n%s\n' "$1" >>"$GITHUB_STEP_SUMMARY"
  fi
  exit 1
}
AFTERMATH="The GitHub Release, the appcast and the Homebrew cask are already published; only the site still lists the previous version. Run the site's Refresh changelogs workflow by hand once this is fixed."

CODE="$(curl -sS -D "$work/headers" -o "$work/dispatch.out" -w '%{http_code}' -X POST \
  -H 'Accept: application/vnd.github+json' \
  -H "Authorization: Bearer ${PUBLIC_RELEASE_TOKEN}" \
  -H 'X-GitHub-Api-Version: 2022-11-28' \
  "${API}/dispatches" \
  -d '{"event_type":"app-released"}')" || CODE=000
if [ "$CODE" != "204" ]; then
  fail "The site changelog dispatch to ${SITE_REPO} returned HTTP ${CODE}: $(tr '\n' ' ' 2>/dev/null <"$work/dispatch.out"). PUBLIC_RELEASE_TOKEN needs Contents read and write on ${SITE_REPO}. ${AFTERMATH}"
fi

# GitHub's own clock, from the dispatch response, so runner clock skew cannot hide the new run.
# Five seconds of slack covers a run stamped in the second before the response was.
SINCE="$(python3 - "$work/headers" <<'PY'
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

when = datetime.now(timezone.utc)
for line in open(sys.argv[1], encoding="latin-1"):
    name, _, value = line.partition(":")
    if name.strip().lower() == "date":
        when = parsedate_to_datetime(value.strip())
print((when - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))
PY
)"
echo "Dispatched app-released to ${SITE_REPO} at ${SINCE} (less 5 s); waiting up to ${WAIT} s for its run."

LAST=""
deadline=$((SECONDS + WAIT))
while :; do
  sleep "$POLL"
  RUNS_CODE="$(curl -sS -o "$work/runs.json" -w '%{http_code}' \
    -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28' \
    "${API}/actions/runs?event=repository_dispatch&per_page=20")" || RUNS_CODE=000
  if [ "$RUNS_CODE" = "200" ]; then
    URL="$(python3 - "$work/runs.json" "$SINCE" <<'PY'
import json, sys
runs = json.load(open(sys.argv[1], encoding="utf-8")).get("workflow_runs", [])
print(next((r["html_url"] for r in runs if r.get("created_at", "") >= sys.argv[2]), ""))
PY
)"
    if [ -n "$URL" ]; then
      echo "Asked www.dragonapp.com to refresh its changelog: ${URL}"
      exit 0
    fi
    LAST="no repository_dispatch run created since ${SINCE}"
  else
    LAST="the Actions API answered HTTP ${RUNS_CODE}"
  fi
  [ "$SECONDS" -lt "$deadline" ] || break
done
fail "The site changelog dispatch to ${SITE_REPO} returned 204, but no site workflow run started within ${WAIT} s (${LAST}). Check that the site's workflow is enabled and listens for repository_dispatch type app-released. ${AFTERMATH}"
