#!/usr/bin/env python3
"""Tests for scripts/whats-new-export.py.

The exporter decides what a public release SAYS, in seven languages, on the release page and on
the marketing site. Two failure modes matter, and they are opposite:

  1. It publishes something wrong quietly — an unresolved `app.whatsNew.summary` as visible note
     text, or a section it failed to parse and dropped. Most cases below therefore assert a
     REJECTION, and they assert it by exit code, not by reading a message.
  2. It rejects something real, which blocks a release that should have shipped. So the happy
     paths pin the exact output for all three source styles the five apps actually use.

Fixtures mirror the real repositories: the dotted-key style (spectacle-2, yahoo-keykey-2,
dragon-sample-app), English sentences as keys (clipmenu-2), and bare Swift literals with a
\"\"\" block (ice-2).

Run: python3 scripts/test-whats-new-export.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

EXPORT = Path(__file__).resolve().parent / "whats-new-export.py"

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


def run(app_dir, whats_new_path, *extra, app="test-app", version="1.2.3"):
    """Run the exporter as the workflow does. Returns (returncode, stdout, stderr)."""
    cmd = [sys.executable, str(EXPORT), "--app-name", app, "--repo-dir", str(app_dir),
           "--whats-new-path", whats_new_path, "--version", version, *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return p.returncode, p.stdout, p.stderr


def export_json(app_dir, whats_new_path, *extra, **kw):
    out = Path(app_dir) / "whats-new.json"
    rc, stdout, stderr = run(app_dir, whats_new_path, "--json-out", str(out), *extra, **kw)
    if rc != 0:
        return rc, None, stderr
    return rc, json.loads(out.read_text(encoding="utf-8")), stderr


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def strings_file(pairs):
    return "".join(f'"{k}" = "{v}";\n' for k, v in pairs.items())


def make_app(config_src, tables=None, *, config="Sources/App/WhatsNewConfig.swift",
             strings_dir="Sources/App/Resources"):
    """A throwaway app checkout: one config file and zero or more <lang>.lproj tables."""
    root = Path(tempfile.mkdtemp())
    write(root / config, config_src)
    for lang, pairs in (tables or {}).items():
        write(root / strings_dir / f"{lang}.lproj/Localizable.strings", strings_file(pairs))
    return root


SEVEN = ("en", "es", "fr", "ja", "ko", "zh-Hans", "zh-Hant")

# --- style 1: abstract dotted keys (spectacle-2, yahoo-keykey-2, dragon-sample-app) ----------
DOTTED_CONFIG = """\
import DragonKit

enum WhatsNewConfig {
    @MainActor
    static var content: WhatsNewContent {
        WhatsNewContent(
            date: "2026-08-11",
            // A comment with "quotes" in it and a // inside, plus a URL: https://example.com
            summary: L("app.whatsNew.summary"),
            sections: [
                ChangeSection(kind: .fixed, entries: [
                    L("app.whatsNew.fixed1"),
                    L("app.whatsNew.fixed2"),
                ]),
                ChangeSection(kind: .changed, entries: [
                    L("app.whatsNew.changed1"),
                ]),
            ]
        )
    }
}
"""

DOTTED_KEYS = ("app.whatsNew.summary", "app.whatsNew.fixed1", "app.whatsNew.fixed2",
               "app.whatsNew.changed1")


def dotted_tables(langs=SEVEN, omit=(), lang_omit=None):
    tables = {}
    for lang in langs:
        pairs = {}
        for key in DOTTED_KEYS:
            if key in omit and (lang_omit is None or lang == lang_omit):
                continue
            pairs[key] = f"{lang}:{key.split('.')[-1]}"
        tables[lang] = pairs
    return tables


print("whats-new-export.py")
print("-- style 1: abstract dotted keys, seven languages --")

app = make_app(DOTTED_CONFIG, dotted_tables())
rc, data, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("a seven-language dotted-key app exports cleanly", rc == 0, err)
if data:
    check("schema is 1", data.get("schema") == 1, data.get("schema"))
    check("app and version come from the caller",
          (data["app"], data["version"]) == ("test-app", "1.2.3"), data)
    check("date comes from the config", data["date"] == "2026-08-11", data["date"])
    check("default_language is en", data["default_language"] == "en", data)
    check("all seven languages, in the kit's order",
          list(data["languages"]) == list(SEVEN), list(data["languages"]))
    en = data["languages"]["en"]
    check("summary resolves from the table", en["summary"] == "en:summary", en["summary"])
    check("section order is the app's, not the enum's",
          [s["kind"] for s in en["sections"]] == ["fixed", "changed"],
          [s["kind"] for s in en["sections"]])
    check("entry order is preserved",
          en["sections"][0]["entries"] == ["en:fixed1", "en:fixed2"], en["sections"][0])
    check("each language resolves from its own table",
          data["languages"]["ja"]["sections"][1]["entries"] == ["ja:changed1"],
          data["languages"]["ja"]["sections"][1])

# THE headline property. `app.whatsNew.fixed2` as visible note text on a marketing page, in one
# locale out of seven, is the silent failure this exporter exists to make impossible.
app = make_app(DOTTED_CONFIG, dotted_tables(omit=("app.whatsNew.fixed2",), lang_omit="ja"))
rc, _, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("a dotted key missing from ONE language fails the export", rc != 0, "exit 0")
check("the failure names the app, the language and the key",
      all(t in err for t in ("test-app", "ja", "app.whatsNew.fixed2")), err)

app = make_app(DOTTED_CONFIG, dotted_tables(omit=DOTTED_KEYS, lang_omit="en"))
rc, _, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("a dotted key missing from English fails too", rc != 0, "exit 0")

# A table that maps the key to itself would defeat a naive "is it in the table" check.
echo = dotted_tables()
echo["fr"]["app.whatsNew.summary"] = "app.whatsNew.summary"
app = make_app(DOTTED_CONFIG, echo)
rc, _, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("a table that echoes the key back fails", rc != 0, "exit 0")

blank = dotted_tables()
blank["ko"]["app.whatsNew.fixed1"] = "   "
app = make_app(DOTTED_CONFIG, blank)
rc, _, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("an empty translation fails", rc != 0, "exit 0")

# The keykey hazard: it vendors the whole kit, whose seven tables do not carry the app's keys.
app = make_app(DOTTED_CONFIG, dotted_tables())
write(app / "vendor/dragon-kit/Sources/DragonKit/Resources/de.lproj/Localizable.strings",
      strings_file({"DragonKit.about.title": "Über"}))
rc, data, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("a vendored table outside the config's directory is not discovered",
      rc == 0 and list(data["languages"]) == list(SEVEN), err or list(data["languages"]))

print("-- style 2: the key IS the English sentence (clipmenu-2) --")

SENTENCE = "The About pane now links the original project."
SENTENCE_SUMMARY = "A fix for the About pane."
SENTENCE_CONFIG = """\
import DragonKit

enum WhatsNewConfig {
    @MainActor
    static var content: WhatsNewContent {
        WhatsNewContent(
            date: "2026-08-11",
            summary: L("%s"),
            sections: [
                ChangeSection(kind: .fixed, entries: [
                    L("%s"),
                ]),
            ]
        )
    }
}
""" % (SENTENCE_SUMMARY, SENTENCE)

full = {lang: {SENTENCE_SUMMARY: f"{lang} summary", SENTENCE: f"{lang} sentence"}
        for lang in SEVEN}
full["en"] = {SENTENCE_SUMMARY: SENTENCE_SUMMARY, SENTENCE: SENTENCE}
app = make_app(SENTENCE_CONFIG, full)
rc, data, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("sentence keys resolve in all seven languages", rc == 0, err)
if data:
    check("English is the sentence itself",
          data["languages"]["en"]["sections"][0]["entries"] == [SENTENCE], data["languages"]["en"])
    check("other languages are translated",
          data["languages"]["zh-Hant"]["sections"][0]["entries"] == ["zh-Hant sentence"],
          data["languages"]["zh-Hant"])

# English needs no table: DragonKit's L() returns the key when a lookup misses, so the key is what
# the app shows.
missing_en = {lang: dict(pairs) for lang, pairs in full.items()}
del missing_en["en"]
app = make_app(SENTENCE_CONFIG, missing_en)
rc, data, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("an English sentence key with no en table is still English", rc == 0, err)
if data:
    check("...and yields the key verbatim",
          data["languages"]["en"]["summary"] == SENTENCE_SUMMARY, data["languages"]["en"])

# But a missing TRANSLATION is a real gap: that language would show English.
partial = {lang: dict(pairs) for lang, pairs in full.items()}
del partial["fr"][SENTENCE]
app = make_app(SENTENCE_CONFIG, partial)
rc, _, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("a sentence key missing from one translation fails", rc != 0, "exit 0")
check("...and names the language", "fr" in err, err)

print("-- style 3: bare literals, including \"\"\" with continuations (ice-2) --")

LITERAL_CONFIG = '''\
import DragonKit

enum WhatsNewConfig {
    static var content: WhatsNewContent {
        WhatsNewContent(
            date: "2026-08-11",
            summary: """
                Two fixes to Settings ▸ About, both found by putting all five apps' \\
                About panes side by side. Nothing outside that pane changed.
                """,
            sections: [
                ChangeSection(kind: .fixed, entries: [
                    "Settings ▸ About now links the original project. It said \\"Based on Ice\\", and pointed nowhere.",
                ]),
                // A comment between two sections, with "quotes" in it.
                ChangeSection(kind: .changed, entries: [
                    "About names one copyright holder.",
                ]),
            ]
        )
    }
}
'''

app = make_app(LITERAL_CONFIG)
rc, data, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("an app with no .strings at all exports English only",
      rc == 0 and list(data["languages"]) == ["en"], err or list(data["languages"]))
if data:
    en = data["languages"]["en"]
    check('the """ block joins its \\-continued lines into one paragraph',
          en["summary"] == ("Two fixes to Settings ▸ About, both found by putting all five "
                           "apps' About panes side by side. Nothing outside that pane changed."),
          repr(en["summary"]))
    check("escaped quotes inside a literal are decoded",
          '"Based on Ice"' in en["sections"][0]["entries"][0], en["sections"][0])
    check("a comment between two sections does not eat the second one",
          [s["kind"] for s in en["sections"]] == ["fixed", "changed"],
          [s["kind"] for s in en["sections"]])

# A bare literal in an app that DOES ship tables is passthrough, not a failure: the app itself
# shows that English text in every language, and the export mirrors the pane.
app = make_app(LITERAL_CONFIG, {lang: {"unused.key": "x"} for lang in SEVEN})
rc, data, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("bare literals pass through to every language the app ships",
      rc == 0 and data["languages"]["ja"]["sections"][1]["entries"]
      == ["About names one copyright holder."], err or data)

print("-- rejections that keep a half-read config off a release page --")

def rejects(name, config_src, tables=None, path="Sources/App/WhatsNewConfig.swift", **kw):
    app = make_app(config_src, tables)
    rc, _, err = run(app, path, **kw)
    check(name, rc != 0, f"exit 0 (stderr: {err.strip()[:200]})")


BASE = """\
import DragonKit
enum WhatsNewConfig {
    static var content: WhatsNewContent {
        WhatsNewContent(
%s
        )
    }
}
"""

rejects("an explicit version: argument",
        BASE % '            version: "1.2.3",\n            date: "2026-01-01",\n'
               '            summary: "x",\n'
               '            sections: [ChangeSection(kind: .fixed, entries: ["a"])],')
rejects("an empty summary",
        BASE % '            date: "2026-01-01",\n            summary: "",\n'
               '            sections: [ChangeSection(kind: .fixed, entries: ["a"])],')
rejects("no summary at all",
        BASE % '            date: "2026-01-01",\n'
               '            sections: [ChangeSection(kind: .fixed, entries: ["a"])],')
rejects("no date",
        BASE % '            summary: "x",\n'
               '            sections: [ChangeSection(kind: .fixed, entries: ["a"])],')
rejects("a section with no entries",
        BASE % '            date: "2026-01-01",\n            summary: "x",\n'
               '            sections: [ChangeSection(kind: .fixed, entries: [])],')
rejects("an unknown ChangeSection kind",
        BASE % '            date: "2026-01-01",\n            summary: "x",\n'
               '            sections: [ChangeSection(kind: .deprecated, entries: ["a"])],')
rejects("an argument this exporter does not know",
        BASE % '            date: "2026-01-01",\n            summary: "x",\n'
               '            footnote: "?",\n'
               '            sections: [ChangeSection(kind: .fixed, entries: ["a"])],')
rejects("string interpolation in an entry",
        BASE % '            date: "2026-01-01",\n            summary: "x",\n'
               '            sections: [ChangeSection(kind: .fixed, entries: ["v\\(version)"])],')
rejects("string concatenation in an entry",
        BASE % '            date: "2026-01-01",\n            summary: "x",\n'
               '            sections: [ChangeSection(kind: .fixed, entries: ["a" + "b"])],')
rejects("a value that is neither a literal nor L()",
        BASE % '            date: "2026-01-01",\n            summary: Self.blurb,\n'
               '            sections: [ChangeSection(kind: .fixed, entries: ["a"])],')
rejects("two WhatsNewContent( in one file",
        (BASE % '            date: "2026-01-01",\n            summary: "x",\n'
                '            sections: [ChangeSection(kind: .fixed, entries: ["a"])],')
        + "\nlet other = WhatsNewContent(date: \"2026-01-01\", summary: \"y\")\n")
rejects("a whats_new_path whose config does not exist", DOTTED_CONFIG, path="Sources/App/Nope.swift")
rejects("a version that is not X.Y.Z",
        BASE % '            date: "2026-01-01",\n            summary: "x",\n'
               '            sections: [ChangeSection(kind: .fixed, entries: ["a"])],',
        version="v1.2.3")

# Blindness, deny-by-default: a file that mentions ChangeSection but yields none means the parser
# stopped seeing them, which would publish notes with every entry quietly missing.
rejects("sections that parse to nothing while the file mentions ChangeSection",
        BASE % '            date: "2026-01-01",\n            summary: "x",\n'
               '            sections: Self.sections,')

# A localized date cannot be represented: the schema carries one.
rejects("a localized date", BASE % '            date: L("app.date"),\n            summary: "x",\n'
                                   '            sections: [ChangeSection(kind: .fixed, entries: ["a"])],',
        {"en": {"app.date": "2026-01-01"}})

print("-- what must NOT be rejected --")

# tag-gate.sh check 6 accepts a maintenance-only release that says so in its summary and has no
# sections. If the exporter refused that, a release the gate passed would die here instead.
maint = BASE % ('            date: "2026-01-01",\n'
                '            summary: "Maintenance only: nothing user-facing changed.",')
app = make_app(maint)
rc, data, err = export_json(app, "Sources/App/WhatsNewConfig.swift")
check("a maintenance-only release with no sections exports", rc == 0, err)
if data:
    check("...as an empty sections array", data["languages"]["en"]["sections"] == [], data)

print("-- language discovery --")

# The union: a strings file named in whats_new_path but living outside the config's directory is
# still a language the app ships.
app = make_app(DOTTED_CONFIG, dotted_tables(langs=("en",)))
write(app / "Translations/ja.lproj/Localizable.strings",
      strings_file({k: f"ja:{k.split('.')[-1]}" for k in DOTTED_KEYS}))
rc, data, err = export_json(
    app, "Sources/App/WhatsNewConfig.swift Translations/ja.lproj/Localizable.strings")
check("a listed strings file outside the config's directory is included",
      rc == 0 and list(data["languages"]) == ["en", "ja"], err or list(data["languages"]))

app = make_app(DOTTED_CONFIG, dotted_tables(langs=("en", "ja")))
rc, data, err = export_json(app, "Sources/App/WhatsNewConfig.swift,"
                                 "Sources/App/Resources/ja.lproj/Localizable.strings")
check("whats_new_path splits on commas as well as whitespace",
      rc == 0 and list(data["languages"]) == ["en", "ja"], err or list(data["languages"]))

print("-- the rendered English release body --")

app = make_app(DOTTED_CONFIG, dotted_tables())
body_path = Path(app) / "notes.md"
rc, _, err = run(app, "Sources/App/WhatsNewConfig.swift", "--body-out", str(body_path),
                 "--tag", "v1.2.3", "--previous-tag", "v1.2.2",
                 "--github-repo", "teddychan/test-app")
check("the body renders", rc == 0, err)
body = body_path.read_text(encoding="utf-8") if body_path.exists() else ""
expected = (
    "en:summary\n"
    "\n"
    "### Fixed\n"
    "\n"
    "- en:fixed1\n"
    "- en:fixed2\n"
    "\n"
    "### Changed\n"
    "\n"
    "- en:changed1\n"
    "\n"
    "**Full Changelog**: https://github.com/teddychan/test-app/compare/v1.2.2...v1.2.3\n"
)
check("the body is the English notes, in the app's order, with the compare link",
      body == expected, f"got:\n{body}\nwant:\n{expected}")
check("the body is not GitHub's generated PR-title list",
      "generate-notes" not in body and "Full Changelog" in body, body)

body_path.unlink()
rc, _, err = run(app, "Sources/App/WhatsNewConfig.swift", "--body-out", str(body_path),
                 "--tag", "v1.2.3", "--github-repo", "teddychan/test-app")
first = body_path.read_text(encoding="utf-8") if body_path.exists() else ""
check("a first release omits the compare link rather than inventing a baseline",
      rc == 0 and first and "Full Changelog" not in first, err or first)

# A """ entry keeps its newlines; they must stay inside the bullet.
multi = BASE % ('            date: "2026-01-01",\n'
                '            summary: "x",\n'
                '            sections: [ChangeSection(kind: .fixed, entries: ["""\n'
                '                one\n'
                '                two\n'
                '                """])],')
app = make_app(multi)
body_path = Path(app) / "notes.md"
rc, _, err = run(app, "Sources/App/WhatsNewConfig.swift", "--body-out", str(body_path))
check("a multi-line entry stays one bullet",
      rc == 0 and "- one\n  two\n" in body_path.read_text(encoding="utf-8"),
      err or body_path.read_text(encoding="utf-8"))

print("-- the contract with the live marketing-site reader --")

# www.dragonapp.com renders this artifact. It compares `version` to the release tag and REJECTS the
# whole asset on a mismatch rather than publishing one version's notes under another number — so a
# wrong version is not a visible failure, the app just quietly keeps its old row on the site.
app = make_app(DOTTED_CONFIG, dotted_tables())
rc, _, err = run(app, "Sources/App/WhatsNewConfig.swift", "--tag", "v9.9.9", version="1.2.3")
check("a --version that disagrees with --tag fails", rc != 0, "exit 0")
rc, data, err = export_json(app, "Sources/App/WhatsNewConfig.swift", "--tag", "v1.2.3",
                            version="1.2.3")
check("the tag's own version is accepted (a leading v is the only difference)", rc == 0, err)
if data:
    check("language keys are DragonKit's .lproj codes, so en and not en-US",
          "en" in data["languages"] and not any("-US" in l for l in data["languages"]),
          list(data["languages"]))
    check("every emitted kind is one the schema allows",
          all(s["kind"] in ("added", "changed", "fixed", "removed", "improved", "security")
              for s in data["languages"]["en"]["sections"]), data["languages"]["en"])
    check("date is still emitted even though the reader prefers the Release's published_at",
          data["date"] == "2026-08-11", data.get("date"))

print("-- verification runs (no tag) --")

app = make_app(DOTTED_CONFIG, dotted_tables())
rc, data, err = export_json(app, "Sources/App/WhatsNewConfig.swift", version="")
check("an empty --version is accepted, so a branch with no tag can still be checked",
      rc == 0 and data["version"] == "", err or data)

print()
print(f"{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
print("All whats-new-export tests passed.")
