#!/usr/bin/env bash
#
# Public Tag Release Gate.
#
# Canonical spec: dragon-kit/docs/MAC-APP-RELEASE-LIFECYCLE.md, "What's New is part of the
# release". This runs BEFORE signing, notarization and publication, because the remedy for a
# failed gate is a fresh version — and you must never delete and re-push a release tag to retry
# (GitHub turns the published Release into a draft whose asset 404s).
#
# The gate is unconditional and lives here rather than in each app, so no caller can opt out of
# it or soften it. It is deliberately noisy: every rejection prints what was expected, what was
# found, and the remedy. A gate that skips a check silently is worse than no gate — it reports
# success over an unshippable build.
#
# Inputs (environment):
#   REF_NAME        GITHUB_REF_NAME of the triggering ref.
#   REF_TYPE        GITHUB_REF_TYPE — "tag" or "branch".
#   RELEASE_TAG     Optional. When the trigger was a manual dispatch (a branch ref), this must
#                   name an ALREADY EXISTING exact public tag. A branch name can never become a
#                   release version.
#   WHATS_NEW_PATH  Repo-relative path to the app's What's New source. Required.
#   VERIFY_ONLY     Optional. Exactly "true" enables verification-only mode: the run may not
#                   sign, notarize, upload, publish, touch the appcast, bump Homebrew or
#                   dispatch the site, so a run with no tag at all is permitted. The spec's
#                   "a manual workflow dispatch must name an existing exact tag OR run as
#                   verification-only" is this branch. Nothing else relaxes.
#   REPO_DIR        Optional. Defaults to the working directory.
#
# Output (stdout, for eval into $GITHUB_ENV by the caller):
#   TAG=vX.Y.Z
#   VERSION=X.Y.Z
#   PREVIOUS_TAG=vX.Y.Z   (empty for a first release)
#   GATE_MODE=release | verify-only-no-tag
#
# In verify-only-no-tag mode TAG/VERSION/PREVIOUS_TAG are deliberately EMPTY: there is no tag,
# so there is no version, and inventing one is the exact hazard this gate exists to stop. Any
# consumer of $VERSION must treat empty as "not a release" and refuse to publish — see the
# workflow's version-assertion step, which hard-fails on an empty VERSION unless VERIFY_ONLY.
set -euo pipefail

PUBLIC_TAG_RE='^v[0-9]+\.[0-9]+\.[0-9]+$'
REPO_DIR="${REPO_DIR:-$PWD}"
cd "$REPO_DIR"

fail() { echo "::error::tag gate: $*" >&2; exit 1; }
note() { echo "  $*" >&2; }

# Only the exact string "true" counts. A caller that forwards a workflow_dispatch input renders
# the boolean as a string, and any other value (an empty input, "yes", "1", "False") must leave
# the gate at full strength rather than half-relax it.
VERIFY_ONLY_MODE=false
[ "${VERIFY_ONLY:-}" = "true" ] && VERIFY_ONLY_MODE=true

if [ "$VERIFY_ONLY_MODE" = true ]; then
  echo "== Public tag release gate (VERIFICATION ONLY — nothing may be published) ==" >&2
else
  echo "== Public tag release gate ==" >&2
fi

# ---------------------------------------------------------------- 1. resolve the tag
# On workflow_call, GITHUB_REF_NAME is the CALLER's ref. A caller triggered by
# workflow_dispatch therefore arrives here with a BRANCH name — and the old
# `VERSION=${TAG##*v}` turned "main" into the version string "main", because that parameter
# expansion leaves a value containing no "v" untouched. That is the hazard the spec names as
# "a branch name can never become a release version".
if [ "${REF_TYPE:-}" = "tag" ]; then
  TAG="${REF_NAME:-}"
  [ -n "$TAG" ] || fail "REF_TYPE=tag but REF_NAME is empty."
elif [ -n "${RELEASE_TAG:-}" ]; then
  TAG="$RELEASE_TAG"
  # A dispatch may only re-run an EXISTING tag. Accepting a new name here would let a manual
  # run mint a release version that no commit was ever tagged with.
  git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null 2>&1 \
    || fail "RELEASE_TAG '${TAG}' does not exist. A manual dispatch may only name an existing exact public tag."
  note "manual dispatch re-running existing tag ${TAG}"
elif [ "$VERIFY_ONLY_MODE" = true ]; then
  # The one sanctioned way to run without a tag, and it is safe only because verification-only
  # cannot sign, notarize, upload, publish, touch the appcast, bump Homebrew or dispatch the
  # site. If that ever stops being true, this branch becomes a hole straight through the gate.
  TAG=""
  note "no tag: verification-only run from '${REF_NAME:-?}' (${REF_TYPE:-?})"
else
  fail "triggered from '${REF_NAME:-?}' (${REF_TYPE:-?}), which is not a tag.
    A manual dispatch must set RELEASE_TAG to an existing exact public tag, or run
    verification-only. A branch name can never become a release version."
fi

if [ -n "$TAG" ]; then
  # -------------------------------------------------------------- 2. exact public tag only
  # No sample-v, mas-v, app-v or release-v. One repository owns at most one public vX.Y.Z
  # series, and every distribution channel for an app consumes that same exact tag.
  if ! printf '%s' "$TAG" | grep -Eq "$PUBLIC_TAG_RE"; then
    fail "'${TAG}' is not an exact public release tag (expected ^v[0-9]+\.[0-9]+\.[0-9]+\$).
    Prefixed families such as sample-v*, mas-v* or app-v* are not permitted; a channel-specific
    workflow must reference the app's existing exact vX.Y.Z tag instead of inventing its own."
  fi
  VERSION="${TAG#v}"
  note "tag ${TAG} -> version ${VERSION}"

  # -------------------------------------------------------------- 3. preceding public tag
  # Only exact public tags count, so a historical sample-v*/mas-v* never becomes the baseline
  # the What's New diff is measured against. `sort -V` on the numeric part orders 2.9.11 before
  # 2.10.0, which a lexical sort gets wrong.
  PREVIOUS_TAG="$(
    git tag --list 'v*' \
      | grep -E "$PUBLIC_TAG_RE" \
      | sed 's/^v//' \
      | sort -V \
      | awk -v cur="$VERSION" '$0 == cur { exit } { last = $0 } END { if (last != "") print "v" last }'
  )"
  if [ -z "$PREVIOUS_TAG" ]; then
    note "no preceding public tag — treating as the first public release"
  else
    note "preceding public tag: ${PREVIOUS_TAG}"
  fi
else
  # Say out loud which checks did not run. A gate that quietly drops four of its six checks is
  # the "reports success over an unshippable build" failure this file opens by warning about.
  VERSION=""
  PREVIOUS_TAG=""
  note "SKIPPED (no tag): exact-tag format, tag->version, preceding-tag lookup, What's New diff"
  note "STILL ENFORCED: What's New exists, no hardcoded version, notes say something"
fi

# ---------------------------------------------------------------- 4. What's New is current
[ -n "${WHATS_NEW_PATH:-}" ] \
  || fail "WHATS_NEW_PATH is not set. The gate cannot confirm the release notes are current, and
    a check that cannot fail is worse than no check. Pass whats_new_path from the caller."
[ -f "$WHATS_NEW_PATH" ] \
  || fail "WHATS_NEW_PATH '${WHATS_NEW_PATH}' does not exist in the checkout."

if [ -n "$PREVIOUS_TAG" ]; then
  if git diff --quiet "${PREVIOUS_TAG}" "${TAG}" -- "$WHATS_NEW_PATH" 2>/dev/null; then
    fail "${WHATS_NEW_PATH} has not changed since ${PREVIOUS_TAG}.
    Every public release updates What's New, including a maintenance-only one — otherwise the
    pane relabels the previous release's notes with this version's number. Update it and tag a
    fresh version; do not move this tag."
  fi
  note "What's New changed since ${PREVIOUS_TAG}"
fi

# ---------------------------------------------------------------- 5. no hardcoded version
# The heading derives from CFBundleShortVersionString, which is the same string this gate
# asserts the tag against. An explicit `version:` argument re-introduces a second, hand-typed
# source of truth that silently disagrees with the bundle on the next release.
if grep -nE '^[^/]*\bWhatsNewContent\(' -A6 "$WHATS_NEW_PATH" | grep -qE '^\s*[0-9]*[-:]?\s*version:'; then
  grep -nE -A6 '\bWhatsNewContent\(' "$WHATS_NEW_PATH" | grep -nE 'version:' >&2 || true
  fail "${WHATS_NEW_PATH} passes an explicit 'version:' to WhatsNewContent.
    The heading must derive from CFBundleShortVersionString. Remove the argument."
fi

# ---------------------------------------------------------------- 6. notes say something
# Either real entries, or an explicit statement that the release is maintenance-only. A release
# with neither has notes that describe nothing.
if grep -q 'ChangeSection(' "$WHATS_NEW_PATH"; then
  note "notes contain change sections"
elif grep -qiE 'maintenance|no user-facing|noUserFacingChanges' "$WHATS_NEW_PATH"; then
  note "notes declare a maintenance-only release"
else
  fail "${WHATS_NEW_PATH} has neither a ChangeSection nor an explicit maintenance-only
    statement. Say what changed, or say plainly that nothing user-facing did."
fi

if [ -n "$TAG" ]; then
  echo "== gate passed: ${TAG} ==" >&2
  GATE_MODE=release
else
  echo "== gate passed: verification only, no release ==" >&2
  GATE_MODE=verify-only-no-tag
fi
echo "TAG=${TAG}"
echo "VERSION=${VERSION}"
echo "PREVIOUS_TAG=${PREVIOUS_TAG}"
echo "GATE_MODE=${GATE_MODE}"
