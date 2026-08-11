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
| `spectacle-2`     | `swiftpm`    |
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

`v6.4.0` changes what every `@v6` caller's next release *looks like* — the Release
body is now the app's own notes instead of GitHub's generated PR-title list, and a
`whats-new.json` asset is attached — but it adds no input and needs no caller edit.
It reads the `whats_new_path` the caller already passes.

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

## The What's New pane is the release notes (`v6.4.0`)

The gate proves the notes were updated. `scripts/whats-new-export.py` reads them.
It runs immediately after the gate — before the certificate import, so a bad
export costs nothing — and produces two things:

1. **The GitHub Release body.** Previously `gh release create --generate-notes`,
   i.e. GitHub's summary of the PR titles since the previous tag, which is why
   every published body reads like `docs: realign RELEASING.md with the actual
   release workflow · release: 2.14.6 — …`. That is the commit log, not what the
   app tells its users. The body is now the **English** notes: the summary as a
   paragraph, then one `### Added` / `### Changed` / `### Fixed` / … heading per
   section **in the app's own order** with its entries as a bullet list, then
   GitHub's `**Full Changelog**` compare link — kept because a tag-to-tag diff is
   genuinely useful and is not a restatement of the notes. A first release, with
   no preceding tag, omits the link rather than inventing a baseline.
2. **`whats-new.json`, attached to the Release**, carrying **every language the
   app ships**, so the marketing site renders localized notes without parsing
   Swift. Schema below; treat it as a contract.

```json
{ "schema": 1, "app": "spectacle-2", "version": "2.5.4", "date": "2026-08-11",
  "default_language": "en",
  "languages": { "en": { "summary": "…",
                         "sections": [ {"kind": "fixed",   "entries": ["…", "…"]},
                                       {"kind": "changed", "entries": ["…"]} ] } } }
```

`kind` is DragonKit's `ChangeSection.Kind` (`added, changed, fixed, removed,
improved, security`). `version` is the tag gate's `VERSION`, never a second read
of `Info.plist`, so the artifact and the tag cannot disagree.

**www.dragonapp.com already reads this**, so the schema is a contract with a
deployed consumer, not a proposal. Four things it requires, all pinned by tests:
the asset is named exactly `whats-new.json` (`gh release upload` takes the asset
name from the basename); `"schema"` is `1`; `version` matches the release tag —
on a mismatch the reader **rejects the asset**, which is not a visible failure,
the app just keeps its old row on the site, hence the tag/version agreement check
in the exporter; and language keys are DragonKit's `.lproj` codes, `en` rather
than `en-US`, which the site maps onto `en` itself. The reader ignores `date` in
favour of the Release's `published_at`, and flattens each language to one prose
line — dropping `kind` but **preserving section and entry order**, which is why
the exporter keeps the app's order rather than the enum's.

It handles all three styles the five apps actually use: `L("app.whatsNew.fixed1")`
resolved per language from `Localizable.strings` (spectacle-2, yahoo-keykey-2,
dragon-sample-app), `L("A full English sentence.")` where the key *is* the English
text (clipmenu-2), and bare Swift literals including `"""` blocks with `\`
line-continuations (ice-2, which ships no `.strings` and so emits `en` alone).
Languages are discovered from `*.lproj/Localizable.strings` **under the config
file's own directory**, unioned with any `.strings` named in `whats_new_path`:
scoped that way because yahoo-keykey-2 vendors the whole kit, whose seven tables
do not carry the app's keys, and a repo-wide glob would "discover" five languages
that do not exist and fail every KeyKey release.

**It fails the release rather than guessing.** An unresolved key is not visibly
broken — `app.whatsNew.summary` would publish as this release's note text in one
locale out of seven, on a public page. So a dotted key that does not resolve in
any language, a translation missing where the others have one, a table that echoes
the key back, an empty summary, a section with no entries, an explicit `version:`,
string interpolation, or any construct the parser does not recognise all exit
non-zero, naming the app, the language, the key and the file. There is **no**
fallback to `--generate-notes`; restoring one would restore the original bug
invisibly, and `scripts/test-workflow-contract.py` fails the build if anyone does.

A maintenance-only release is still exportable with `sections: []` — the same
release the gate's check 6 accepts when the summary says so plainly.

The export is deliberately **not** guarded by `verify_only`: like the version
assertion it is a check, and a verification run from a branch is the cheapest
place to discover a missing translation. It publishes nothing on its own — the
Release body and the asset upload are both inside the guarded upload step.

Nothing was backfilled. Releases published before `v6.4.0` keep their generated
bodies and have no `whats-new.json`, and an already-existing Release keeps its
body on a re-run.

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
python3 scripts/test-whats-new-export.py      # the notes export, all three styles
python3 scripts/test-workflow-contract.py     # the workflow still runs both correctly
```

The last one parses `release-macos.yml` and asserts the properties that a
reviewer stops catching in a 900-line file: the gate precedes the export and the
export precedes every sign/archive/notarize/upload/appcast/Homebrew/site-dispatch
step, each of those carries the verify-only guard and withholds its secrets, the
Release body comes from `--notes-file` and no step runs `--generate-notes`, the
scripts checkout uses `job.workflow_sha` + `job.workflow_repository`, and no step
interpolates `${{ ... }}` into a `run:` body. Every check is deny-by-default: a
pattern that matches nothing fails rather than passes quietly.

`ci.yml` runs all three on every PR, plus `actionlint`.

To see what a real app would publish, run the exporter against its clone:

```bash
python3 scripts/whats-new-export.py --app-name spectacle-2 --repo-dir ~/git/spectacle-2 \
  --whats-new-path "$(…the app's own whats_new_path…)" --version 2.5.4 \
  --tag v2.5.4 --previous-tag v2.5.3 --github-repo teddychan/spectacle-2
```
