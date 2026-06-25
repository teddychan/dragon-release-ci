# dragon-release-ci

Shared GitHub Actions release pipeline for the public macOS Dragon-App repos.

`.github/workflows/release-macos.yml` is a **reusable workflow** (`workflow_call`)
that builds, code-signs (Developer ID + hardened runtime), notarizes, staples,
and publishes a macOS app, then publishes the EdDSA-signed Sparkle appcast to
`www.dragonapp.com` and bumps the Homebrew cask.

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
    uses: teddychan/dragon-release-ci/.github/workflows/release-macos.yml@v1
    secrets: inherit
    with:
      build_kind: swiftpm
      app_slug: clipmenu-2
      bot_name: ClipMenu Release Bot
      zip_name_template: 'ClipMenu-{MAJOR}-{TAG}.zip'
      # ...
```

See the workflow's `inputs:`/`secrets:` block for the full parameter list.
Pin callers to a tag (`@v1`), never `@main`, since `secrets: inherit` exposes
the caller's secrets to this workflow.
