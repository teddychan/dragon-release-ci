#!/usr/bin/env python3
"""Contract tests for .github/workflows/release-macos.yml.

scripts/test-tag-gate.sh proves the gate rejects what it must reject. This file proves the
WORKFLOW still runs it in the one position where that matters, and that no publishing step can
slip past the verification-only guard. Those are properties of the YAML, not of any script, and
they are exactly the kind that a reviewer's eye stops catching in an 800-line file.

Every check is deny-by-default. A pattern that matches nothing FAILS rather than passes silently:
a contract test that quietly stops recognising the notarization step would report success over a
workflow that notarizes unguarded, which is the same failure mode the gate itself warns about.

Run: python3 scripts/test-workflow-contract.py
"""

import re
import sys
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/release-macos.yml"

# In YAML 1.1 the bare key `on` is the BOOLEAN True, not the string "on". Every parse of a
# GitHub workflow has to know this; indexing d["on"] raises KeyError and looks like a missing
# trigger.
ON_KEY = True

# The guard. Written as a regex so `! inputs.verify_only` with a space also counts, and so a
# near-miss like `inputs.verify_only == false` does NOT.
GUARD_RE = re.compile(r"!\s*inputs\.verify_only")

# Side-effecting work, keyed by category, detected from the step's own shell body rather than
# from its name — a step can be renamed, but it cannot notarize without calling notarytool.
# Every category must match at least one step (see check_side_effects).
SIDE_EFFECTS = {
    "sign": re.compile(r"codesign\s+--force"),
    "archive": re.compile(r"xcodebuild\s+archive|-exportArchive"),
    "notarize": re.compile(r"notarytool\s+submit|stapler\s+staple"),
    "upload": re.compile(r"gh release (create|upload)"),
    "appcast": re.compile(r"generate_appcast"),
    "homebrew": re.compile(r"Casks/"),
    "site_dispatch": re.compile(r"/dispatches"),
    "git_push": re.compile(r"git push"),
    "credentials": re.compile(r"security create-keychain|security import"),
}

GATE_STEP_NAME = "Public tag release gate"

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        for line in str(detail).splitlines():
            print(f"        {line}")


def load():
    text = WORKFLOW.read_text()
    return text, yaml.safe_load(text)


def step_label(i, step):
    return f"[{i}] {step.get('name') or step.get('uses') or '?'}"


# --------------------------------------------------------------------------- inputs
def check_inputs(wf):
    call = (wf.get(ON_KEY) or {}).get("workflow_call")
    check("the workflow is workflow_call (reusable)", isinstance(call, dict), wf.get(ON_KEY))
    inputs = (call or {}).get("inputs") or {}

    for name, want_type in (("whats_new_path", "string"), ("release_tag", "string"),
                            ("verify_only", "boolean")):
        spec = inputs.get(name)
        check(f"input {name!r} exists", spec is not None, sorted(inputs))
        if spec is None:
            continue
        check(f"input {name!r} is type {want_type}", spec.get("type") == want_type, spec)

    # Defaults are the compatibility contract: the four existing callers pass neither, so
    # verify_only must default to publishing and whats_new_path must default to the empty
    # string the gate then rejects (rather than to some path that happens to exist).
    vo = inputs.get("verify_only") or {}
    check("verify_only defaults to false (omitting it publishes, as today)",
          vo.get("default") is False, vo)
    wn = inputs.get("whats_new_path") or {}
    check("whats_new_path defaults to '' so an unset caller fails the gate loudly",
          wn.get("default") == "", wn)


# --------------------------------------------------------------------------- checkouts
def check_scripts_checkout(text, steps):
    hits = [(i, s) for i, s in enumerate(steps)
            if str(s.get("uses", "")).startswith("actions/checkout")
            and (s.get("with") or {}).get("path") == ".release-ci"]
    check("the release-CI scripts checkout exists (path: .release-ci)", len(hits) == 1, hits)
    if len(hits) != 1:
        return
    with_ = hits[0][1]["with"]

    ref = str(with_.get("ref", ""))
    repo = str(with_.get("repository", ""))
    check("scripts checkout ref uses job.workflow_sha", "job.workflow_sha" in ref, ref)
    check("scripts checkout repository uses job.workflow_repository",
          "job.workflow_repository" in repo, repo)
    # A hardcoded owner/repo silently keeps working while the workflow is moved or forked, and
    # then checks out somebody else's scripts.
    check("scripts checkout repository is not a hardcoded owner/repo",
          "${{" in repo and "teddychan/" not in repo, repo)
    # Scoped to ${{ }} expressions so the comment that RECORDS this mistake may keep naming it.
    undocumented = [e for e in re.findall(r"\$\{\{.*?\}\}", text, re.S)
                    if "github.job_workflow_sha" in e or "github.job_workflow_ref" in e]
    check("no expression uses the undocumented github.job_workflow_sha/_ref",
          not undocumented, "\n".join(undocumented))

    # An undefined context renders empty and actions/checkout treats an empty ref as the default
    # branch, so something must refuse to continue BEFORE the checkout runs.
    idx = hits[0][0]
    earlier = "\n".join((s.get("run") or "") for s in steps[:idx])
    check("an earlier step fails loudly when either resolves empty",
          "WORKFLOW_SHA" in earlier and "WORKFLOW_REPOSITORY" in earlier and "exit 1" in earlier,
          "no preceding step asserts job.workflow_sha / job.workflow_repository are non-empty")


def check_source_checkout(steps):
    hits = [(i, s) for i, s in enumerate(steps)
            if str(s.get("uses", "")).startswith("actions/checkout")
            and (s.get("with") or {}).get("path") is None]
    check("the caller's source checkout exists", len(hits) == 1, hits)
    if len(hits) != 1:
        return
    with_ = hits[0][1]["with"] or {}
    # Required by `git rev-list --count HEAD` build numbers AND by the gate's preceding-tag
    # lookup: fetch-depth 0 is what fetches every v* tag regardless of the ref below.
    check("the source checkout keeps fetch-depth: 0", with_.get("fetch-depth") == 0, with_)
    ref = str(with_.get("ref", ""))
    check("the source checkout resolves release_tag to a tag ref, not the branch head",
          "inputs.release_tag" in ref and "refs/tags/" in ref, ref or "(no ref: builds the branch head)")


# --------------------------------------------------------------------------- the gate
def check_gate(steps):
    idxs = [i for i, s in enumerate(steps) if s.get("name") == GATE_STEP_NAME]
    check(f"the {GATE_STEP_NAME!r} step exists", len(idxs) == 1, [s.get("name") for s in steps])
    if len(idxs) != 1:
        return None
    gate = steps[idxs[0]]
    env = gate.get("env") or {}
    for var in ("REF_NAME", "REF_TYPE", "RELEASE_TAG", "WHATS_NEW_PATH", "VERIFY_ONLY"):
        check(f"the gate is passed {var}", var in env, sorted(env))
    check("the gate runs the checked-out script, not an inline copy",
          "tag-gate.sh" in (gate.get("run") or ""), gate.get("run"))
    check("the gate is unguarded: no input can opt out of it",
          gate.get("if") is None, gate.get("if"))
    return idxs[0]


# --------------------------------------------------------------------------- guards
def check_side_effects(steps, gate_idx):
    found = {}
    for i, step in enumerate(steps):
        run = step.get("run") or ""
        for category, pattern in SIDE_EFFECTS.items():
            if pattern.search(run):
                found.setdefault(category, []).append(i)

    # Deny-by-default: a category that matches nothing means the detector went blind, not that
    # the workflow stopped doing it.
    for category in SIDE_EFFECTS:
        check(f"a {category} step is still recognised", category in found,
              f"no step's run: matches {SIDE_EFFECTS[category].pattern!r} — "
              "either the step was removed or this test has gone blind")

    for category, idxs in sorted(found.items()):
        for i in idxs:
            step = steps[i]
            label = f"{category}: {step_label(i, step)}"
            if gate_idx is not None:
                check(f"the gate precedes {label}", i > gate_idx,
                      f"gate at {gate_idx}, step at {i}")
            check(f"verify-only guard on {label}", bool(GUARD_RE.search(str(step.get("if") or ""))),
                  f"if: {step.get('if')!r}")


def check_credential_starvation(steps):
    """Second, independent layer: a guarded step must also be denied its credentials.

    If someone deletes an `if:` the step still cannot publish, because the token it needs
    renders as the empty string and the step's own "not set" branch fires.
    """
    leaks = []
    checked = 0
    for i, step in enumerate(steps):
        for var, value in (step.get("env") or {}).items():
            value = str(value)
            if "secrets." not in value and "github.token" not in value:
                continue
            checked += 1
            if not GUARD_RE.search(value):
                leaks.append(f"{step_label(i, step)} env {var}: {value}")
    check("some step actually passes a secret (the check has something to check)", checked > 0,
          "no env value references secrets. or github.token")
    check("every secret/token is withheld when verify_only is true", not leaks, "\n".join(leaks))


# --------------------------------------------------------------------------- injection
def check_no_run_interpolation(steps):
    """No ${{ }} inside a run: block — every value arrives through env:.

    A `${{ inputs.x }}` spliced into a shell body is textual substitution before bash ever sees
    it, so a caller's input can close the quote and run its own commands. release_tag is the
    sharp one: it comes from a workflow_dispatch field, not from the caller's checked-in YAML.
    """
    leaks = []
    for i, step in enumerate(steps):
        for expr in re.findall(r"\$\{\{[^}]*\}\}", step.get("run") or ""):
            leaks.append(f"{step_label(i, step)}: {expr}")
    check("no step interpolates an expression into its run: body", not leaks, "\n".join(leaks))


def main():
    if not WORKFLOW.exists():
        print(f"missing {WORKFLOW}")
        return 1
    text, wf = load()
    print("release-macos.yml contract")

    jobs = wf.get("jobs") or {}
    check("the workflow has exactly one job", len(jobs) == 1, sorted(jobs))
    steps = (jobs.get("build") or {}).get("steps") or []
    check("job 'build' has steps", bool(steps), sorted(jobs))
    if not steps:
        return 1

    check_inputs(wf)
    check_source_checkout(steps)
    check_scripts_checkout(text, steps)
    gate_idx = check_gate(steps)
    check_side_effects(steps, gate_idx)
    check_credential_starvation(steps)
    check_no_run_interpolation(steps)
    check_secrets_are_optional(wf)
    check_build_offset_wiring(wf, steps)
    check_appcast_mirror_wiring(wf, steps)

    print()
    print(f"{PASS} passed, {FAIL} failed")
    if FAIL:
        return 1
    print("All workflow-contract tests passed.")
    return 0



def check_secrets_are_optional(wf):
    """No workflow_call secret may be `required: true`.

    GitHub enforces required secrets at workflow STARTUP, before any step or `if:` is evaluated,
    so a caller using `secrets: inherit` from a repo with no signing secrets cannot even BEGIN a
    verification-only run. Dragon Sample App's first dispatch died as `startup_failure` in two
    seconds with no log explaining it. A verification run is meant to need no signing secret at
    all, and that is only expressible in this block.

    A real release is still protected: the gate runs first, then signing receives an empty value
    and hard-fails, before notarization, upload, appcast, Homebrew and the site dispatch — each
    separately guarded.
    """
    secrets = (wf.get(ON_KEY) or {}).get("workflow_call", {}).get("secrets") or {}
    check("workflow_call declares secrets", bool(secrets), list(secrets))
    for name, spec in sorted(secrets.items()):
        required = (spec or {}).get("required")
        check(f"secret {name} is optional", required is not True, f"required={required}")


def check_build_offset_wiring(wf, steps):
    """Every step that reads BUILD_NUMBER_OFFSET must receive it via `env:`.

    Wired by hand across three build front-ends, which is exactly the kind of edit that gets one
    of them. A step reading an unset BUILD_NUMBER_OFFSET silently falls back to offset 0 — and a
    zero offset in the app that needs one produces a CFBundleVersion BELOW the shipped build, so
    Sparkle stops offering updates. Silent, and only visible to users.
    """
    ins = (wf.get(ON_KEY) or {}).get("workflow_call", {}).get("inputs") or {}
    check("build_number_offset input exists", "build_number_offset" in ins, sorted(ins)[:5])
    readers = [s for s in steps if "BUILD_NUMBER_OFFSET" in (s.get("run") or "")]
    check("some step reads BUILD_NUMBER_OFFSET", bool(readers), len(readers))
    for s in readers:
        check(f"step '{s.get('name')}' gets BUILD_NUMBER_OFFSET via env",
              "BUILD_NUMBER_OFFSET" in (s.get("env") or {}), sorted((s.get("env") or {})))


def check_appcast_mirror_wiring(wf, steps):
    """The appcast may be published to a second, legacy destination during a feed migration.

    MAC-APP-RELEASE-LIFECYCLE.md requires migrating a feed "by mirroring the old and new locations
    until installed versions have moved to the app-owned URL". With one destination that was
    impossible: repointing appcast_repo at the app's own repo STOPS publishing to the site, and
    every already-installed copy reads its SUFeedURL from there — so it silently stops seeing
    updates, which is invisible until a user complains.

    Deny-by-default, like everything else here. Each assertion names a way the wiring could be
    present in the YAML and still not mirror anything.
    """
    ins = (wf.get(ON_KEY) or {}).get("workflow_call", {}).get("inputs") or {}
    spec = ins.get("appcast_mirror_repo")
    check("input 'appcast_mirror_repo' exists", spec is not None, sorted(ins))
    if spec is not None:
        check("appcast_mirror_repo is type string", spec.get("type") == "string", spec)
        # The compatibility contract: five callers pass nothing, and must keep publishing to
        # exactly one place. A default naming any repo would start mirroring for all of them.
        check("appcast_mirror_repo defaults to '' so an unset caller mirrors nowhere",
              spec.get("default") == "", spec)

    publishers = [s for s in steps if "generate_appcast" in (s.get("run") or "")]
    check("some step generates the appcast", len(publishers) == 1, len(publishers))
    if not publishers:
        return
    step = publishers[0]
    body = step.get("run") or ""
    env = step.get("env") or {}

    check("the appcast step gets APPCAST_MIRROR_REPO via env",
          "APPCAST_MIRROR_REPO" in env, sorted(env))
    check("the appcast step reads APPCAST_MIRROR_REPO", "APPCAST_MIRROR_REPO" in body, None)

    # Publishing is factored into one function so the primary and the mirror cannot drift apart —
    # two hand-written clone/commit/push blocks is how one of them loses a fix.
    calls = re.findall(r"^\s*publish_appcast_to\s+(\S+)", body, re.M)
    check("the appcast is published through one reusable function, called twice",
          len(calls) == 2, calls)
    check("one call publishes to APPCAST_REPO",
          any("APPCAST_REPO" in c and "MIRROR" not in c for c in calls), calls)
    check("the other publishes to APPCAST_MIRROR_REPO",
          any("APPCAST_MIRROR_REPO" in c for c in calls), calls)

    # A mirror that names the primary would push the feed to itself and read, in the log, as
    # migration coverage that does not exist.
    check("naming the same repo twice is rejected",
          re.search(r'APPCAST_MIRROR_REPO"?\s*=\s*"?\$\{?APPCAST_REPO', body) is not None
          and "::error::appcast_mirror_repo equals appcast_repo" in body, None)

    # The whole step is already guarded, but the mirror is the destination installed copies read,
    # so a failure there must not be swallowed the way the site changelog nudge deliberately is.
    check("the mirror publish is not made non-fatal",
          "publish_appcast_to \"$APPCAST_MIRROR_REPO\"" in body
          and "publish_appcast_to \"$APPCAST_MIRROR_REPO\" \"migration mirror\" || true" not in body,
          None)
    check("the appcast step is still guarded by verify_only",
          GUARD_RE.search(str(step.get("if") or "")) is not None, step.get("if"))


if __name__ == "__main__":
    sys.exit(main())
