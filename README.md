# dragon-release-ci

Shared GitHub Actions release pipeline for the public macOS Dragon-App repos.

`.github/workflows/release-macos.yml` is a **reusable workflow** (`workflow_call`)
that gates the release, builds, code-signs (Developer ID + hardened runtime),
notarizes, staples, and publishes a macOS app, then publishes the EdDSA-signed
Sparkle appcast and bumps the Homebrew cask.

## Callers

Each app repo has a thin `release.yml` that calls this on a `v*` tag:

| Repo | `build_kind` |
|------|--------------|
| `clipmenu-2`      | `swiftpm`    |
| `ice-2`           | `xcodebuild` |
| `yahoo-keykey-2`  | `script`     |

`clipmenu-2-premium` does **not** use this — it builds the Mac App Store variant
on its own self-hosted runner.

## Usage

```yaml
jobs:
  release:
    uses: teddychan/dragon-release-ci/.github/workflows/release-macos.yml@v6
    secrets: inherit
    with:
      build_kind: swiftpm
      app_slug: clipmenu-2
      bot_name: ClipMenu Release Bot
      zip_name_template: 'ClipMenu-{MAJOR}-{TAG}.zip'
      whats_new_path: app/Sources/ClipMenu/WhatsNewConfig.swift   # required from v6
      # ...
```

See the workflow's `inputs:`/`secrets:` block for the full parameter list.
Pin callers to a tag, never `@main`, since `secrets: inherit` exposes the
caller's secrets to this workflow.

## Versions: `@v6` is a breaking change, `@v5` is frozen

**`v5` does not move and is not affected by anything below.** Callers pinned
`@v5` keep the exact behaviour they have today. `sync-major-tag.yml` only moves
the major tag derived from the 3-part tag being pushed, so releasing `v6.0.0`
creates/moves `v6` and never touches `v5`.

`@v6` is breaking for one reason: **`whats_new_path` is mandatory.** The public
tag release gate refuses to run without it, because a release-notes check that
cannot fail is worse than no check. Upgrading a caller from `@v5` to `@v6` means
bumping the pin *and* passing `whats_new_path`; nothing else in the interface
changed incompatibly.

## The public tag release gate

Canonical spec: `dragon-kit/docs/MAC-APP-RELEASE-LIFECYCLE.md`. Implementation:
`scripts/tag-gate.sh`, which runs before the certificate import, all three build
front-ends, notarization and publication — so a rejection costs nothing and
nothing has been signed. It:

1. accepts only an exact public `vX.Y.Z` tag (no `sample-v*`, `mas-v*`, `app-v*`);
2. derives the version from that tag rather than from `${TAG##*v}`, which used to
   turn a branch ref named `main` into the version string `main`;
3. finds the preceding *public* tag, ignoring historical prefixed families;
4. requires `whats_new_path` to exist and to have changed since that tag;
5. rejects an explicit `version:` argument in `WhatsNewContent`; and
6. requires real notes or an explicit maintenance-only statement.

The gate has no opt-out input. A failed gate publishes nothing: fix the problem,
pick a fresh version, push a fresh tag — never delete and re-push a release tag,
because GitHub demotes the published Release to a draft whose asset 404s.

### New inputs

| Input | Type | Default | What it does |
|---|---|---|---|
| `whats_new_path` | string | `''` | Repo-relative path to the app's What's New source. **Required** — the gate fails when unset. |
| `release_tag` | string | `''` | For a manual dispatch, where the ref is a branch: names an **already existing** exact public `vX.Y.Z` tag. The whole job then checks out *that commit*, so the build is the tagged code and not the branch head. |
| `verify_only` | boolean | `false` | Run the gate and the build, then stop. See below. |

## Verification-only runs

`verify_only: true` is the "or run as verification-only" half of the lifecycle
spec, and the only way to exercise this pipeline from a branch with no tag.

It runs: the tag gate, the build, and the
`assert_tag_matches_plist` version check. It cannot sign, notarize, zip, upload,
create a GitHub Release, publish the appcast, bump the Homebrew cask, or dispatch
the marketing site.

How that is enforced — two independent layers on every side-effecting step:

1. **Its own `if: ${{ !inputs.verify_only }}`.** Per step, not one shared
   condition, so a new publishing step added later has to opt in explicitly.
   `scripts/test-workflow-contract.py` fails the build if one is missing.
2. **Its credentials are withheld.** Each secret is passed as
   `${{ !inputs.verify_only && secrets.X || '' }}`, so a step that somehow ran
   anyway gets an empty token and either fails or takes its existing "not set"
   skip. A verification run never receives a signing or notary secret at all.

The mode is announced in the first step of the log and in the run summary, and a
closing step states what was verified and what was deliberately not done.

Two things it deliberately does **not** relax:

- With no tag, the gate exports an **empty** `VERSION` rather than a guessed one,
  and the version-assertion step hard-fails on an empty `VERSION` on any
  publishing run.
- Everything that does not need a tag stays enforced with no tag: What's New must
  exist, must not hardcode a version, and must say something. A tag that *is*
  present is still held to the exact-`vX.Y.Z` format.

Only the literal string `true` enables it. Any other value — `1`, `yes`, `True`,
empty — leaves the gate at full strength, and the guards fail closed (they block
publishing) rather than open.

For `xcodebuild` callers the verification build compiles the same scheme and
configuration with signing disabled, because `xcodebuild archive` signs inside
the archive and cannot produce an unsigned artifact.

## Tests

```bash
./scripts/test-tag-gate.sh                    # the gate's own rejections
python3 scripts/test-workflow-contract.py     # the workflow still runs it correctly
```

The second one parses `release-macos.yml` and asserts the properties that a
reviewer stops catching in an 800-line file: the gate precedes every
sign/archive/notarize/upload/appcast/Homebrew/site-dispatch step, each of those
carries the verify-only guard and withholds its secrets, the scripts checkout
uses `job.workflow_sha` + `job.workflow_repository`, and no step interpolates
`${{ ... }}` into a `run:` body. Every check is deny-by-default: a pattern that
matches nothing fails rather than passes quietly.
