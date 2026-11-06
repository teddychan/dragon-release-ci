#!/usr/bin/env python3
"""Tests for scripts/changelog-notes.py.

Fixtures are real entries from the five apps' CHANGELOG.md files, one per heading style:
`## [2.8.0] - date` under a file intro (spectacle-2), `## 2.22.0 (date)` and `## 2.21.2 — date`
(clipmenu-2), `## 2.16.3 - date` (ice-2) and a bare `## 2.17.0` (yahoo-keykey-2).
dragon-sample-app uses spectacle-2's style.

Run: python3 scripts/test-changelog-notes.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "changelog-notes.py"
COMPARE = "**Full Changelog**: https://github.com/teddychan/app/compare/v1.0.0...v1.1.0"
WHATS_NEW_BODY = f"The short What's New summary.\n\n### Fixed\n\n- One entry.\n\n{COMPARE}\n"

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


def run(changelog, version, whats_new_body=WHATS_NEW_BODY):
    """Run the script as the workflow does. Returns (returncode, body or None, stdout + stderr)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        wn = tmp / "release-notes.md"
        wn.write_text(whats_new_body, encoding="utf-8")
        cl = tmp / "CHANGELOG.md"
        if changelog is not None:
            cl.write_text(changelog, encoding="utf-8")
        out = tmp / "release-notes-changelog.md"
        p = subprocess.run([sys.executable, str(SCRIPT), "--changelog", str(cl),
                            "--version", version, "--whats-new-body", str(wn), "--out", str(out)],
                           capture_output=True, text=True, check=False)
        body = out.read_text(encoding="utf-8") if out.exists() else None
        return p.returncode, body, p.stdout + p.stderr


SPECTACLE = """\
# Changelog

All notable changes to Spectacle 2 are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [2.8.0] - 2026-11-03

Six new layouts put the front window in a fixed third or two thirds of the screen.

### Added
- **Left, Center and Right Third, and Left, Center and Right Two Thirds.** They are in the menu bar
  menu and in Settings › Shortcuts, where you can give each one a shortcut; none has one by
  default. Each lands on the same columns on every press, and the single thirds and the left and
  right two thirds match where drag to snap puts a window.

### Changed
- **Next Third and Previous Third are no longer in the menu bar menu.** Their shortcuts still work,
  and they stay in Settings › Shortcuts.

## [2.7.0] - 2026-11-03

Every window action is now in the menu bar menu, so you can arrange windows with a mouse or
trackpad.
"""

SPECTACLE_2_8_0 = f"""\
Six new layouts put the front window in a fixed third or two thirds of the screen.

### Added
- **Left, Center and Right Third, and Left, Center and Right Two Thirds.** They are in the menu bar menu and in Settings › Shortcuts, where you can give each one a shortcut; none has one by default. Each lands on the same columns on every press, and the single thirds and the left and right two thirds match where drag to snap puts a window.

### Changed
- **Next Third and Previous Third are no longer in the menu bar menu.** Their shortcuts still work, and they stay in Settings › Shortcuts.

{COMPARE}
"""

CLIPMENU = """\
# Changelog

Developer-facing notes for ClipMenu. User-facing release notes live in
`App/Sources/ClipMenu/WhatsNewConfig.swift` (shown in the in-app What's New pane).

## 2.22.0 (2026-11-03)

Russian, and the strings that still showed English in every language.

### Added
- **Russian.** `ru.lproj` translates every key in `en.lproj`, in the same order and with the
  same printf specifiers.

  Russian has three plural forms and these tables are not `.stringsdict`, so counts read
  "Label: %d".

### Fixed
- **A snippet editor test failed on macOS 27.** On macOS 27, ViewInspector's snapshot of the
  hosted editor never contains `@Query` rows.

## 2.21.2 — 2026-08-20

The app agrees with itself about its own name.

### Changed
- **`AppInfo.displayName`'s fallback is now "ClipMenu 2".** Only reachable outside a configured
  bundle, e.g. `swift run`.
"""

CLIPMENU_2_22_0 = f"""\
Russian, and the strings that still showed English in every language.

### Added
- **Russian.** `ru.lproj` translates every key in `en.lproj`, in the same order and with the same printf specifiers.

  Russian has three plural forms and these tables are not `.stringsdict`, so counts read "Label: %d".

### Fixed
- **A snippet editor test failed on macOS 27.** On macOS 27, ViewInspector's snapshot of the hosted editor never contains `@Query` rows.

{COMPARE}
"""

ICE = """\
# Changelog

## 2.16.4 - 2026-11-04

### Fixed

- Something later.

## 2.16.3 - 2026-11-03

### Added

- **Russian.** Ice 2 and its shared Settings panes are now available in Russian, translated by
  shendrykau ([#132](https://github.com/teddychan/ice-2-macos/issues/132)).
"""

KEYKEY = """\
# What's New in Yahoo! KeyKey 2

A plain-language list of changes in each version, newest first.

## 2.17.0

- **以空白鍵確認字根 now covers 倉頡 when the new setting is on.** Every 倉頡 code then shows
  candidates before you finish it.

## 2.16.3

- **Internal: the build script now asks for the macOS SDK by name.**
"""

BLOCKS = """\
## 1.1.0

Run this
once:

```sh
# not a heading
brew upgrade
  --cask app
```

| Key | Value |
|-----|-------|
| a   | b     |

1. First step
   continues here.
   - nested item
     continues too.
2. Second step.

Line with a hard break\\
stays on its own line.

## 1.0.0

Older.
"""

BLOCKS_1_1_0 = """\
Run this once:

```sh
# not a heading
brew upgrade
  --cask app
```

| Key | Value |
|-----|-------|
| a   | b     |

1. First step continues here.
   - nested item continues too.
2. Second step.

Line with a hard break\\
stays on its own line.
"""


def main():
    print("changelog-notes.py")

    rc, body, log = run(SPECTACLE, "2.8.0")
    check("spectacle-2: `## [2.8.0] - date` under a file intro, unwrapped, compare link appended",
          rc == 0 and body == SPECTACLE_2_8_0, f"rc={rc}\n{log}\n{body}")

    rc, body, log = run(CLIPMENU, "2.22.0")
    check("clipmenu-2: `## 2.22.0 (date)`, a list item's second paragraph keeps its indent",
          rc == 0 and body == CLIPMENU_2_22_0, f"rc={rc}\n{log}\n{body}")

    rc, body, log = run(CLIPMENU, "2.21.2")
    check("clipmenu-2: `## 2.21.2 — date` is the last entry, read to the end of the file",
          rc == 0 and body is not None
          and body.startswith("The app agrees with itself about its own name.\n")
          and "configured bundle, e.g. `swift run`.\n\n" + COMPARE in body,
          f"rc={rc}\n{log}\n{body}")

    rc, body, log = run(ICE, "2.16.3")
    check("ice-2: `## 2.16.3 - date` below a newer entry",
          rc == 0 and body is not None and body.startswith("### Added\n\n- **Russian.**")
          and "Something later" not in body
          and "translated by shendrykau ([#132]" in body, f"rc={rc}\n{log}\n{body}")

    rc, body, log = run(KEYKEY, "2.17.0")
    check("yahoo-keykey-2: bare `## 2.17.0`",
          rc == 0 and body == "- **以空白鍵確認字根 now covers 倉頡 when the new setting is on.** "
                              "Every 倉頡 code then shows candidates before you finish it.\n\n"
                              + COMPARE + "\n", f"rc={rc}\n{log}\n{body}")

    rc, body, log = run(BLOCKS, "1.1.0")
    check("code fences, tables, nested lists and hard breaks survive the unwrap",
          rc == 0 and body == BLOCKS_1_1_0 + "\n" + COMPARE + "\n", f"rc={rc}\n{log}\n{body}")

    rc, body, log = run(SPECTACLE, "2.8.0", whats_new_body="Notes.\n")
    check("no compare link in the What's New body (a first release) adds none",
          rc == 0 and body == SPECTACLE_2_8_0.replace("\n\n" + COMPARE, ""),
          f"rc={rc}\n{log}\n{body}")

    near_misses = "# Changelog\n\n## 12.8.0\n\n- Not this.\n\n## 2.8.01\n\n- Nor this.\n\n" \
                  "## [2.8.0-beta.1]\n\n- Nor this.\n"
    rc, body, log = run(near_misses, "2.8.0")
    check("2.8.0 matches none of 12.8.0, 2.8.01 and 2.8.0-beta.1, so the run fails",
          rc == 1 and body is None, f"rc={rc}\n{log}\n{body}")
    check("...with an error naming the file and the version",
          "::error::" in log and "CHANGELOG.md" in log and "2.8.0" in log, log)

    rc, body, log = run("# Changelog\n\n## 1.1.0\n\n## 1.0.0\n\n- Older.\n", "1.1.0")
    check("an empty entry fails rather than publishing only a compare link",
          rc == 1 and body is None, f"rc={rc}\n{log}\n{body}")

    rc, body, log = run(None, "1.1.0")
    check("no CHANGELOG.md keeps the What's New body, with a notice",
          rc == 0 and body == WHATS_NEW_BODY and "::notice::" in log, f"rc={rc}\n{log}\n{body}")

    rc, body, log = run("# Changelog\n\n## 0.9.0\n\n- Older.\n", "")
    check("a verification run with no VERSION skips the lookup and keeps the What's New body",
          rc == 0 and body == WHATS_NEW_BODY and "::notice::" in log, f"rc={rc}\n{log}\n{body}")

    print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
