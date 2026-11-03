#!/usr/bin/env bash
#
# Homebrew tap token pre-flight.
#
# The cask bump is the last publishing step, so a dead PUBLIC_RELEASE_TOKEN used to surface only
# after the GitHub Release and the Sparkle appcast were already public: a half-published release
# whose remedy is a fresh version. This proves the token can push to the tap BEFORE anything is
# signed, so a dead token stops the run while stopping is still free.
#
# `git push --dry-run` is the proof. It fetches the receive-pack ref advertisement, which GitHub
# serves only to a credential with write access (403 otherwise), and then sends nothing: no branch
# is created. A clone alone proves nothing, because the tap is public.
#
# Inputs (environment):
#   PUBLIC_RELEASE_TOKEN  Optional. Unset keeps the documented behaviour: a warning here, and the
#                         cask step skips later.
#   TAP_REPO              owner/name of the tap. Default teddychan/homebrew-tap.
#
# The token reaches git only through GIT_CONFIG_* environment variables: never argv, never a
# remote URL, never a file on disk.
set -euo pipefail

TAP_REPO="${TAP_REPO:-teddychan/homebrew-tap}"
REMEDY="Rotate the PUBLIC_RELEASE_TOKEN secret: a fine-grained PAT with Contents and Pull requests read and write on ${TAP_REPO}, then set it again in every app repo that releases. Nothing has been signed or published yet."

if [ -z "${PUBLIC_RELEASE_TOKEN:-}" ]; then
  echo "::warning::PUBLIC_RELEASE_TOKEN not set. The release will publish, but the Homebrew cask bump will be skipped."
  exit 0
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

export GIT_TERMINAL_PROMPT=0
export GIT_CONFIG_COUNT=2
export GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=
export GIT_CONFIG_KEY_1=http.https://github.com/.extraheader
GIT_CONFIG_VALUE_1="Authorization: Basic $(printf 'x-access-token:%s' "$PUBLIC_RELEASE_TOKEN" | base64 | tr -d '\n')"
export GIT_CONFIG_VALUE_1

url="https://github.com/${TAP_REPO}.git"
if ! git clone -q --depth 1 "$url" tap 2>"$work/err"; then
  echo "::error::Could not clone ${TAP_REPO} with PUBLIC_RELEASE_TOKEN, which is how an invalid, expired or revoked token fails: $(tr '\n' ' ' <"$work/err"). ${REMEDY}" >&2
  exit 1
fi

probe="refs/heads/release-preflight-${GITHUB_RUN_ID:-local}-$$"
if ! git -C tap push -q --dry-run origin "HEAD:${probe}" 2>"$work/err"; then
  echo "::error::PUBLIC_RELEASE_TOKEN cannot push to ${TAP_REPO}: $(tr '\n' ' ' <"$work/err"). ${REMEDY}" >&2
  exit 1
fi

echo "PUBLIC_RELEASE_TOKEN can push to ${TAP_REPO}."
