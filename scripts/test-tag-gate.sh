#!/usr/bin/env bash
#
# Tests for scripts/tag-gate.sh.
#
# The gate runs in the critical release path and every branch of it is a rejection, so the
# failure mode that matters is a check that silently passes. Each case below asserts the gate
# REJECTS something it must reject; the happy paths assert it does not over-reject.
#
# Run: ./scripts/test-tag-gate.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
GATE="$HERE/tag-gate.sh"
PASS=0; FAIL=0

# Throwaway fixture repo. `core.hooksPath` is pointed at an empty directory because a global
# pre-commit hook enforces the real committer identity — these fixtures are disposable and must
# not depend on, or impersonate, whoever runs the tests.
init_repo() {
  git init -q .
  mkdir -p .nohooks
  git config core.hooksPath "$PWD/.nohooks"
  git config user.email fixture@example.invalid
  git config user.name fixture
}

# Build a throwaway repo with a What's New file and the given public tags.
make_repo() {
  local dir; dir="$(mktemp -d)"
  (
    cd "$dir"
    init_repo
    mkdir -p app
    printf 'WhatsNewContent(\n  date: "2026-01-01",\n  sections: [ChangeSection(kind: .fixed, entries: ["a"])]\n)\n' > app/WhatsNewConfig.swift
    git add -A; git commit -qm init
    for t in "$@"; do
      printf '// %s\n' "$t" >> app/WhatsNewConfig.swift
      git add -A; git commit -qm "$t"
      git tag "$t"
    done
  )
  echo "$dir"
}

run_gate() { REPO_DIR="$1" "$GATE" 2>&1; }

expect_reject() {
  local name="$1"; shift
  local out; out="$("$@" 2>&1)"; local rc=$?
  if [ $rc -ne 0 ]; then
    PASS=$((PASS+1)); echo "  ok    rejects $name"
  else
    FAIL=$((FAIL+1)); echo "  FAIL  ACCEPTED $name"; echo "$out" | sed 's/^/        /'
  fi
}

expect_accept() {
  local name="$1"; shift
  local out; out="$("$@" 2>&1)"; local rc=$?
  if [ $rc -eq 0 ]; then
    PASS=$((PASS+1)); echo "  ok    accepts $name"
  else
    FAIL=$((FAIL+1)); echo "  FAIL  REJECTED $name"; echo "$out" | sed 's/^/        /'
  fi
}

echo "tag-gate.sh"

REPO="$(make_repo v2.9.11 v2.10.0 v2.11.0)"
BASE=(env REPO_DIR="$REPO" WHATS_NEW_PATH=app/WhatsNewConfig.swift)

# --- the branch-name hazard: the old `${TAG##*v}` turned "main" into VERSION=main ---
expect_reject "a branch ref with no RELEASE_TAG" \
  "${BASE[@]}" REF_TYPE=branch REF_NAME=main "$GATE"

expect_reject "a dispatch naming a tag that does not exist" \
  "${BASE[@]}" REF_TYPE=branch REF_NAME=main RELEASE_TAG=v9.9.9 "$GATE"

expect_accept "a dispatch naming an existing exact tag" \
  "${BASE[@]}" REF_TYPE=branch REF_NAME=main RELEASE_TAG=v2.11.0 "$GATE"

# --- prefixed tag families are gone ---
for bad in sample-v1.2.0 mas-v2.20.1 app-v1.0.0 release-v1.0.0 v2.11 v2.11.0-beta1 vmain main; do
  expect_reject "prefixed/malformed tag '$bad'" \
    "${BASE[@]}" REF_TYPE=tag REF_NAME="$bad" "$GATE"
done

expect_accept "an exact public tag" \
  "${BASE[@]}" REF_TYPE=tag REF_NAME=v2.11.0 "$GATE"

# --- preceding-tag selection must be version-ordered, not lexical ---
# 2.9.11 sorts AFTER 2.10.0 lexically; the baseline for 2.11.0 must be 2.10.0.
PREV="$("${BASE[@]}" REF_TYPE=tag REF_NAME=v2.11.0 "$GATE" 2>/dev/null | sed -n 's/^PREVIOUS_TAG=//p')"
if [ "$PREV" = "v2.10.0" ]; then
  PASS=$((PASS+1)); echo "  ok    picks v2.10.0 (not v2.9.11) as the preceding tag"
else
  FAIL=$((FAIL+1)); echo "  FAIL  preceding tag was '$PREV', expected v2.10.0"
fi

# --- a historical prefixed tag must never become the baseline ---
REPO2="$(make_repo v2.10.0 v2.11.0)"
( cd "$REPO2" && git tag sample-v9.9.9 && git tag mas-v9.9.9 )
PREV2="$(env REPO_DIR="$REPO2" WHATS_NEW_PATH=app/WhatsNewConfig.swift REF_TYPE=tag REF_NAME=v2.11.0 "$GATE" 2>/dev/null | sed -n 's/^PREVIOUS_TAG=//p')"
if [ "$PREV2" = "v2.10.0" ]; then
  PASS=$((PASS+1)); echo "  ok    ignores historical sample-v/mas-v when picking the baseline"
else
  FAIL=$((FAIL+1)); echo "  FAIL  baseline was '$PREV2', expected v2.10.0"
fi

# --- What's New must have changed since the preceding tag ---
REPO3="$(mktemp -d)"
(
  cd "$REPO3"; init_repo
  mkdir -p app
  printf 'WhatsNewContent(\n  date: "2026-01-01",\n  sections: [ChangeSection(kind: .fixed, entries: ["a"])]\n)\n' > app/WhatsNewConfig.swift
  git add -A; git commit -qm init; git tag v2.10.0
  printf 'let unrelated = 1\n' > other.swift          # a release that touched everything BUT the notes
  git add -A; git commit -qm bump; git tag v2.10.1
)
expect_reject "a release whose What's New did not change" \
  env REPO_DIR="$REPO3" WHATS_NEW_PATH=app/WhatsNewConfig.swift REF_TYPE=tag REF_NAME=v2.10.1 "$GATE"

# --- missing / unusable config must fail, never skip ---
expect_reject "an unset WHATS_NEW_PATH" \
  env REPO_DIR="$REPO" REF_TYPE=tag REF_NAME=v2.11.0 "$GATE"
expect_reject "a WHATS_NEW_PATH that does not exist" \
  env REPO_DIR="$REPO" WHATS_NEW_PATH=app/Nope.swift REF_TYPE=tag REF_NAME=v2.11.0 "$GATE"

# --- an explicit version: argument re-introduces a hand-typed source of truth ---
REPO4="$(make_repo v2.10.0)"
(
  cd "$REPO4"
  printf 'WhatsNewContent(\n  version: "2.10.0",\n  date: "2026-01-01",\n  sections: [ChangeSection(kind: .fixed, entries: ["a"])]\n)\n' > app/WhatsNewConfig.swift
  git add -A; git commit -qm pin; git tag v2.10.1
)
expect_reject "an explicit version: argument in WhatsNewContent" \
  env REPO_DIR="$REPO4" WHATS_NEW_PATH=app/WhatsNewConfig.swift REF_TYPE=tag REF_NAME=v2.10.1 "$GATE"

# --- notes must say something ---
REPO5="$(make_repo v2.10.0)"
(
  cd "$REPO5"
  printf 'WhatsNewContent(\n  date: "2026-01-01",\n  sections: []\n)\n' > app/WhatsNewConfig.swift
  git add -A; git commit -qm empty; git tag v2.10.1
)
expect_reject "notes with no sections and no maintenance statement" \
  env REPO_DIR="$REPO5" WHATS_NEW_PATH=app/WhatsNewConfig.swift REF_TYPE=tag REF_NAME=v2.10.1 "$GATE"

REPO6="$(make_repo v2.10.0)"
(
  cd "$REPO6"
  printf 'WhatsNewContent(\n  date: "2026-01-01",\n  summary: "Maintenance only: nothing user-facing changed.",\n  sections: []\n)\n' > app/WhatsNewConfig.swift
  git add -A; git commit -qm maint; git tag v2.10.1
)
expect_accept "an explicit maintenance-only release" \
  env REPO_DIR="$REPO6" WHATS_NEW_PATH=app/WhatsNewConfig.swift REF_TYPE=tag REF_NAME=v2.10.1 "$GATE"

# --- verification-only mode -------------------------------------------------
# The spec's "a manual workflow dispatch must name an existing exact tag OR run as
# verification-only". This is the gate's ONLY relaxation, and it is safe purely because a
# verification-only run cannot sign, notarize, upload, publish, touch the appcast, bump Homebrew
# or dispatch the site. So the tests below are really two claims: the branch exists, and it
# relaxes nothing beyond the tag itself.
expect_accept "verify-only from a branch with no tag" \
  "${BASE[@]}" REF_TYPE=branch REF_NAME=main VERIFY_ONLY=true "$GATE"

# Only the exact string "true". A caller that forwards a dispatch input can send anything, and
# every other value must leave the gate at full strength rather than half-relax it.
for notTrue in false 1 yes True TRUE '' ; do
  expect_reject "verify-only spelled '${notTrue}' (not exactly 'true')" \
    "${BASE[@]}" REF_TYPE=branch REF_NAME=main VERIFY_ONLY="$notTrue" "$GATE"
done

# A tag that IS present is still held to the format, and a named tag must still exist: verify-only
# removes the requirement to HAVE a tag, never the rules about one.
expect_reject "verify-only with a prefixed tag ref" \
  "${BASE[@]}" REF_TYPE=tag REF_NAME=sample-v1.2.0 VERIFY_ONLY=true "$GATE"
expect_reject "verify-only naming a tag that does not exist" \
  "${BASE[@]}" REF_TYPE=branch REF_NAME=main RELEASE_TAG=v9.9.9 VERIFY_ONLY=true "$GATE"

# The three checks that do not need a tag stay enforced with no tag.
expect_reject "verify-only with an unset WHATS_NEW_PATH" \
  env REPO_DIR="$REPO" REF_TYPE=branch REF_NAME=main VERIFY_ONLY=true "$GATE"
expect_reject "verify-only with a WHATS_NEW_PATH that does not exist" \
  env REPO_DIR="$REPO" WHATS_NEW_PATH=app/Nope.swift REF_TYPE=branch REF_NAME=main VERIFY_ONLY=true "$GATE"
expect_reject "verify-only with an explicit version: argument" \
  env REPO_DIR="$REPO4" WHATS_NEW_PATH=app/WhatsNewConfig.swift REF_TYPE=branch REF_NAME=main VERIFY_ONLY=true "$GATE"
expect_reject "verify-only with notes that say nothing" \
  env REPO_DIR="$REPO5" WHATS_NEW_PATH=app/WhatsNewConfig.swift REF_TYPE=branch REF_NAME=main VERIFY_ONLY=true "$GATE"

# A tag ref is gated in full even under verify-only, so a verification run is a true rehearsal.
expect_reject "verify-only on a tag whose What's New did not change" \
  env REPO_DIR="$REPO3" WHATS_NEW_PATH=app/WhatsNewConfig.swift REF_TYPE=tag REF_NAME=v2.10.1 VERIFY_ONLY=true "$GATE"

# No tag means NO version, not a guessed one. Everything downstream keys off this being empty:
# the workflow's version assertion hard-fails on an empty VERSION unless verify_only is set.
OUT="$("${BASE[@]}" REF_TYPE=branch REF_NAME=main VERIFY_ONLY=true "$GATE" 2>/dev/null)"
if [ "$(printf '%s\n' "$OUT" | sed -n 's/^TAG=//p')" = "" ] \
  && [ "$(printf '%s\n' "$OUT" | sed -n 's/^VERSION=//p')" = "" ] \
  && [ "$(printf '%s\n' "$OUT" | sed -n 's/^GATE_MODE=//p')" = "verify-only-no-tag" ]; then
  PASS=$((PASS+1)); echo "  ok    exports an empty TAG/VERSION and GATE_MODE=verify-only-no-tag"
else
  FAIL=$((FAIL+1)); echo "  FAIL  verify-only outputs were:"; printf '%s\n' "$OUT" | sed 's/^/        /'
fi

# A real tag still reports itself as a release, so the workflow can tell the two apart.
MODE="$("${BASE[@]}" REF_TYPE=tag REF_NAME=v2.11.0 "$GATE" 2>/dev/null | sed -n 's/^GATE_MODE=//p')"
if [ "$MODE" = "release" ]; then
  PASS=$((PASS+1)); echo "  ok    a tagged run reports GATE_MODE=release"
else
  FAIL=$((FAIL+1)); echo "  FAIL  GATE_MODE was '$MODE', expected release"
fi


# --- multi-path whats_new_path ---------------------------------------------
# The notes are one artifact split across a config file and its translations. Watching only the
# config made check 4 weaker than it claimed for spectacle-2, yahoo-keykey-2 and
# dragon-sample-app, whose entries are L() keys with the TEXT in Localizable.strings — and it
# rejected dragon-sample-app 1.4.2, where the notes genuinely changed but only in .strings.
mk_split_repo() {
  local dir; dir="$(mktemp -d)"
  (
    cd "$dir"; init_repo
    mkdir -p app app/en.lproj
    printf 'WhatsNewContent(\n  date: "2026-01-01",\n  sections: [ChangeSection(kind: .fixed, entries: [L("a.b")])]\n)\n' > app/WhatsNewConfig.swift
    printf '"a.b" = "First";\n' > app/en.lproj/Localizable.strings
    git add -A; git commit -qm init; git tag v1.0.0
    # A real note rewrite that does NOT touch the .swift.
    printf '"a.b" = "Rewritten";\n' > app/en.lproj/Localizable.strings
    git add -A; git commit -qm reword; git tag v1.0.1
  )
  echo "$dir"
}
SPLIT="$(mk_split_repo)"
BOTH="app/WhatsNewConfig.swift app/en.lproj/Localizable.strings"

expect_reject "a strings-only rewrite when only the config is watched" \
  env REPO_DIR="$SPLIT" WHATS_NEW_PATH="app/WhatsNewConfig.swift" REF_TYPE=tag REF_NAME=v1.0.1 "$GATE"
expect_accept "a strings-only rewrite when both are watched" \
  env REPO_DIR="$SPLIT" WHATS_NEW_PATH="$BOTH" REF_TYPE=tag REF_NAME=v1.0.1 "$GATE"
expect_accept "a comma-separated list" \
  env REPO_DIR="$SPLIT" WHATS_NEW_PATH="app/WhatsNewConfig.swift,app/en.lproj/Localizable.strings" REF_TYPE=tag REF_NAME=v1.0.1 "$GATE"
expect_reject "a list containing a path that does not exist" \
  env REPO_DIR="$SPLIT" WHATS_NEW_PATH="$BOTH app/Nope.strings" REF_TYPE=tag REF_NAME=v1.0.1 "$GATE"

# Checks 5 and 6 must read the FIRST entry, not whichever file happens to match.
SPLIT2="$(mk_split_repo)"
( cd "$SPLIT2"
  printf 'WhatsNewContent(\n  version: "1.0.1",\n  date: "2026-01-02",\n  sections: [ChangeSection(kind: .fixed, entries: [L("a.b")])]\n)\n' > app/WhatsNewConfig.swift
  git add -A; git commit -qm pin; git tag v1.0.2 )
expect_reject "an explicit version: in the FIRST entry of a list" \
  env REPO_DIR="$SPLIT2" WHATS_NEW_PATH="$BOTH" REF_TYPE=tag REF_NAME=v1.0.2 "$GATE"

# Nothing changed anywhere -> still rejected. The list widens what counts, it does not excuse.
SPLIT3="$(mk_split_repo)"
( cd "$SPLIT3"; printf 'let x = 1\n' > other.swift; git add -A; git commit -qm unrelated; git tag v1.0.3 )
expect_reject "a release where no watched file changed" \
  env REPO_DIR="$SPLIT3" WHATS_NEW_PATH="$BOTH" REF_TYPE=tag REF_NAME=v1.0.3 "$GATE"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
echo "All tag-gate tests passed."
