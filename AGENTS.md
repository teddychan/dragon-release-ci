# AGENTS.md

dragon-release-ci: the shared reusable GitHub Actions workflow that builds, signs, notarizes and publishes the Dragon macOS apps. Tagging and the release gate are described in [README.md](README.md).

## Project rules

- Merging is not releasing. Nothing tags automatically, so a merged change reaches no app until a `vX.Y.Z` tag is pushed (`sync-major-tag.yml` then moves the floating major tag that callers pin). After merging, compare `git rev-parse <major> origin/main` (find the current major with `git tag --sort=-v:refname`): if they differ, cut a tag or say plainly that the change hasn't shipped.
- Choose the version by caller impact: a MINOR for a change consumers will see in their releases, a PATCH for CI hygiene with no consumer effect, and a new MAJOR only when caller inputs change incompatibly.
