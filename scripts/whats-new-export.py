#!/usr/bin/env python3
"""Export an app's What's New pane to whats-new.json, and render the GitHub Release body from it.

The What's New pane IS the release notes. Two things have to follow from that and until now
neither did:

  1. The GitHub Release body was `gh release create --generate-notes`, i.e. GitHub's summary of
     the PR titles that landed since the previous tag. Every published body therefore reads like
     "docs: realign RELEASING.md with the actual release workflow · release: 2.14.6 — ..." — the
     commit log, not the notes the app shows its own users, and a second description of the
     release that nobody wrote and nobody proofread.
  2. Nothing carried the notes off the tag at all, so the marketing site re-derived them from the
     Releases API (English, and only what --generate-notes had put there). The localized text
     five of these apps ship in seven languages was reachable only by parsing Swift.

So: parse the app's What's New source once, in the one place that already knows how a Dragon app
is laid out, and emit the notes as data. The tag gate (scripts/tag-gate.sh) reads the same
`whats_new_path` and only greps it — that it contains `ChangeSection(` and no `version:`. This
reads it.

Schema (version 1) — the contract with the consumer, do not change it silently:

    {
      "schema": 1,
      "app": "spectacle-2",
      "version": "2.5.4",
      "date": "2026-08-11",
      "default_language": "en",
      "languages": {
        "en": {"summary": "...",
               "sections": [{"kind": "fixed", "entries": ["...", "..."]},
                            {"kind": "changed", "entries": ["..."]}]},
        "ja": {"summary": "...", "sections": [...]}
      }
    }

`kind` is DragonKit's `ChangeSection.Kind`. Section and entry order is the APP's order, because
that is the order the pane renders and therefore the order the notes were written to be read in —
not the enum's declaration order.

`version` is not read from Info.plist here. It is passed in from the tag gate's exported VERSION,
which is derived from the tag, so this file and the version assertion cannot disagree: two
readers of "the version" is how they drift.

WHAT THE DEPLOYED READER REQUIRES
---------------------------------
www.dragonapp.com renders this artifact, so the schema is a contract with a live consumer rather
than a proposal. Four requirements, each of which this file satisfies by construction:

* The Release asset is named EXACTLY `whats-new.json`. `gh release upload` names an asset from the
  file's basename, so the workflow writes `$RUNNER_TEMP/whats-new.json` and
  scripts/test-workflow-contract.py pins that basename.
* `"schema": 1`. Anything else and the reader keeps the site's existing entry.
* `version` MUST match the release tag (a leading `v` is tolerated). On a mismatch the reader
  rejects the whole asset rather than publishing one version's notes under another — so a wrong
  version does not look broken, it silently costs the app its row on the site. Hence both the
  X.Y.Z check and the tag/version agreement check in main().
* Language keys are DragonKit's `.lproj` codes — `en`, not `en-US`; the site maps its own `en-US`
  locale onto `en` itself. They come from the `.lproj` directory names, so they are those codes.

Two things the reader does that are worth knowing here: it ignores `date` in favour of the
Release's own `published_at` (a hand-written What's New date usually predates the tag by days), and
it flattens each language to one prose line, dropping `kind` but PRESERVING section and entry
order. So the order this file goes to some trouble to keep is load-bearing downstream; the kind
labels are not, today.

WHY THIS FAILS INSTEAD OF GUESSING
----------------------------------
An unresolved localization key does not look broken. `app.whatsNew.summary` is a perfectly
publishable-looking string, and it would go out as the visible release note for one of seven
locales on a public marketing page — the silent failure this whole repository is built against.
There is no partial export and no fallback: every rejection below names the app, the language, the
key and the file, and exits non-zero before anything has been signed or notarized.

THE THREE SOURCE STYLES, all real, all verified against the app at its released tag
----------------------------------------------------------------------------------
* `L("app.whatsNew.fixed1")` — an abstract key. spectacle-2, yahoo-keykey-2, dragon-sample-app.
  Resolved per language from that language's Localizable.strings. Missing anywhere = hard failure.
* `L("A full English sentence.")` — clipmenu-2. The key IS the English text, so English needs no
  table (`L()` returns the key when a lookup misses, by design); the other six languages must
  still have it, or the app itself would show English there.
* A bare Swift literal — ice-2, which ships no .strings at all. Emitted as-is for every language,
  which is exactly what the app does: passthrough here mirrors the pane rather than guessing at
  it. Includes `\"\"\"` blocks with `\\`-at-end-of-line continuations, which is how ice-2's summary
  is written.

Languages are the seven the kit ships (en, es, fr, ja, ko, zh-Hans, zh-Hant), and only the ones
the app actually has: ice-2 emits `en` alone. They are discovered from `*.lproj/Localizable.strings`
under the config file's own directory, unioned with any strings file named in `whats_new_path`.
Discovery is scoped to that directory on purpose — yahoo-keykey-2 vendors the whole kit at
vendor/dragon-kit, whose seven tables do not contain the app's keys, and a repo-wide glob would
"discover" five languages that do not exist and then fail every KeyKey release.

Run:
    whats-new-export.py --app-name spectacle-2 --repo-dir . \
        --whats-new-path "Sources/.../WhatsNewConfig.swift Sources/.../en.lproj/Localizable.strings" \
        --version 2.5.4 --tag v2.5.4 --previous-tag v2.5.3 --github-repo teddychan/spectacle-2 \
        --json-out whats-new.json --body-out release-notes.md
"""

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1

# DragonKit's seven. The order is the kit's own; it decides the key order in the JSON so a diff of
# two exports is readable.
KIT_LANGUAGES = ("en", "es", "fr", "ja", "ko", "zh-Hans", "zh-Hant")
DEFAULT_LANGUAGE = "en"

# DragonKit's ChangeSection.Kind, exhaustively. A seventh kind added to the kit must fail here
# rather than reach the consumer as a value it does not know.
KINDS = ("added", "changed", "fixed", "removed", "improved", "security")

# Release-body headings. Title case, not the pane's uppercase `label` ("FIXED"): the pane is a
# dense list inside a settings window, the body is Markdown on a release page.
KIND_TITLES = {
    "added": "Added",
    "changed": "Changed",
    "fixed": "Fixed",
    "removed": "Removed",
    "improved": "Improved",
    "security": "Security",
}

# "Looks like a dotted key": an identifier with at least one dot and no whitespace. This is the
# shape that must never be published as visible text, and it is also how a key is told apart from
# clipmenu-2's English-sentence keys, which always contain spaces.
DOTTED_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+\Z")

LABEL_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")


class ExportError(Exception):
    """Every rejection. Printed as a ::error:: annotation and exits non-zero."""


def fail(message):
    raise ExportError(message)


# --------------------------------------------------------------------------- Swift scanning
def _scan_string(src, i, *, decode, where):
    """Scan the string literal starting at src[i] == '"'. Returns (value_or_None, end_index).

    Handles both `"..."` and `\"\"\"...\"\"\"`. With decode=False it only finds the end, which is
    what comment-stripping needs: that walks the WHOLE file, and validating escapes in some
    unrelated literal would fail a release over a string the notes never touch.
    """
    if src.startswith('"""', i):
        return _scan_multiline_string(src, i, decode=decode, where=where)
    j = i + 1
    n = len(src)
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            raw = src[i + 1:j]
            return (_decode_escapes(raw, where) if decode else None), j + 1
        if c == "\n":
            break
        j += 1
    fail(f"{where}: unterminated string literal starting at offset {i}.")


def _scan_multiline_string(src, i, *, decode, where):
    """Scan a `\"\"\"` block. Swift strips the closing delimiter's indentation from every line."""
    body_start = src.find("\n", i + 3)
    if body_start < 0 or src[i + 3:body_start].strip():
        fail(f'{where}: a """ literal must be followed by a newline (offset {i}).')
    body_start += 1
    j = body_start
    n = len(src)
    while j < n:
        if src[j] == "\\":
            j += 2
            continue
        if src.startswith('"""', j):
            raw = src[body_start:j]
            return (_decode_multiline(raw, where) if decode else None), j + 3
        j += 1
    fail(f'{where}: unterminated """ literal starting at offset {i}.')


def _decode_multiline(raw, where):
    lines = raw.split("\n")
    # The last element is whatever precedes the closing delimiter on its own line: the indentation
    # Swift removes from the whole block.
    indent = lines[-1]
    if indent.strip():
        fail(f'{where}: the closing """ must be on a line of its own.')
    body = lines[:-1]
    stripped = []
    for line in body:
        if not line.strip():
            stripped.append("")
        elif line.startswith(indent):
            stripped.append(line[len(indent):])
        else:
            fail(f'{where}: line {line!r} is indented less than the closing """.')
    # Continuations before escapes: a trailing `\` means "no newline here", which is how ice-2
    # writes a one-paragraph summary across five source lines.
    out = []
    pending = ""
    for line in stripped:
        if line.endswith("\\") and not line.endswith("\\\\"):
            pending += line[:-1]
            continue
        out.append(pending + line)
        pending = ""
    if pending:
        out.append(pending)
    return _decode_escapes("\n".join(out), where, multiline=True)


def _decode_escapes(raw, where, *, multiline=False):
    out = []
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            fail(f"{where}: string ends in a lone backslash.")
        nxt = raw[i + 1]
        simple = {"\\": "\\", '"': '"', "'": "'", "n": "\n", "t": "\t", "r": "\r", "0": "\0"}
        if nxt in simple:
            out.append(simple[nxt])
            i += 2
            continue
        if nxt == "u" and raw[i + 2:i + 3] == "{":
            end = raw.find("}", i + 3)
            if end < 0:
                fail(rf"{where}: unterminated \u{{...}} escape.")
            out.append(chr(int(raw[i + 3:end], 16)))
            i = end + 1
            continue
        if nxt == "(":
            # A release note assembled at runtime cannot be exported as text, and emitting the
            # literal "\(foo)" would publish source code as a note.
            fail(f"{where}: string interpolation is not supported in release notes "
                 f"({raw[i:i + 24]!r}). Write the finished sentence, or put it in Localizable.strings.")
        if multiline and nxt == "\n":
            i += 2
            continue
        fail(rf"{where}: unrecognized escape \{nxt} in a string literal.")
    return "".join(out)


def strip_comments(src, where):
    """Replace comments with blanks, leaving string literals byte-for-byte.

    Not cosmetic. spectacle-2's config explains itself with `About gains its "Open-source
    licenses" row.` — quotes inside a `//` comment — and ice-2's sections carry a comment between
    two entries. A parser that does not know what a comment is reads those as content.
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            _, j = _scan_string(src, i, decode=False, where=where)
            out.append(src[i:j])
            i = j
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
            continue
        if src.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth += 1
                    j += 2
                elif src.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            if depth:
                fail(f"{where}: unterminated /* comment.")
            # Keep the newlines so offsets in error messages still land on the right line.
            out.append("".join("\n" if ch == "\n" else " " for ch in src[i:j]))
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _matching(src, i, open_ch, close_ch, where):
    """Index just past the bracket that closes src[i] == open_ch, skipping string literals."""
    depth, j, n = 0, i, len(src)
    while j < n:
        c = src[j]
        if c == '"':
            _, j = _scan_string(src, j, decode=False, where=where)
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    fail(f"{where}: unbalanced {open_ch}{close_ch}.")


def split_top_level(src, where):
    """Split on commas that are not inside a string, (), [] or {}. Drops blank chunks.

    Blank chunks come from Swift's trailing commas, which every one of these configs uses.
    """
    parts, depth, start, j, n = [], 0, 0, 0, len(src)
    while j < n:
        c = src[j]
        if c == '"':
            _, j = _scan_string(src, j, decode=False, where=where)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(src[start:j])
            start = j + 1
        j += 1
    parts.append(src[start:])
    return [p for p in parts if p.strip()]


# --------------------------------------------------------------------------- the config file
class Literal:
    """One piece of app-supplied text: either a localization key or a finished string."""

    __slots__ = ("text", "is_key")

    def __init__(self, text, is_key):
        self.text = text
        self.is_key = is_key


def _parse_value(chunk, where):
    """`L("...")` or a string literal. Anything else is a parse this exporter does not recognize."""
    s = chunk.strip()
    if s.startswith("L(") or s.startswith("L ("):
        end = _matching(s, s.index("("), "(", ")", where)
        if end != len(s):
            fail(f"{where}: trailing text after L(...): {s[end:]!r}")
        inner = s[s.index("(") + 1:end - 1].strip()
        if not inner.startswith('"'):
            fail(f"{where}: L() takes a string literal, got {inner!r}.")
        value, consumed = _scan_string(inner, 0, decode=True, where=where)
        if inner[consumed:].strip():
            fail(f"{where}: L() takes one string literal, got {inner!r}.")
        return Literal(value, is_key=True)
    if s.startswith('"'):
        value, consumed = _scan_string(s, 0, decode=True, where=where)
        if s[consumed:].strip():
            fail(f"{where}: expected one string literal, got {s!r}. String concatenation and "
                 "interpolation are not supported — write the finished sentence.")
        return Literal(value, is_key=False)
    fail(f"{where}: expected L(\"...\") or a string literal, got {s[:80]!r}.")


def _named_args(args_src, where, allowed):
    """Split `label: value, label: value` and reject a label this exporter does not know.

    Deny-by-default, like everything else in this repo: silently dropping an argument means
    silently dropping whatever the app put in it.
    """
    out = {}
    for chunk in split_top_level(args_src, where):
        m = LABEL_RE.match(chunk)
        if not m:
            fail(f"{where}: cannot read the argument {chunk.strip()[:60]!r} — expected `label:`.")
        label = m.group(1)
        if label not in allowed:
            fail(f"{where}: unexpected argument {label!r}. Known: {', '.join(sorted(allowed))}. "
                 "An argument this exporter does not understand is content it would drop.")
        if label in out:
            fail(f"{where}: {label!r} given twice.")
        out[label] = chunk[m.end():]
    return out


class ParsedNotes:
    def __init__(self, date, summary, sections):
        self.date = date
        self.summary = summary
        self.sections = sections  # [(kind, [Literal])]


def parse_config(path, app):
    where = f"{app}: {path}"
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"{where}: cannot read the What's New config ({exc}).")
    code = strip_comments(src, where)

    hits = [m.start() for m in re.finditer(r"\bWhatsNewContent\s*\(", code)]
    if len(hits) != 1:
        fail(f"{where}: found {len(hits)} `WhatsNewContent(` in code (comments excluded); "
             "expected exactly one. This exporter will not guess which one ships.")
    open_paren = code.index("(", hits[0])
    end = _matching(code, open_paren, "(", ")", where)
    args = _named_args(code[open_paren + 1:end - 1], where,
                       allowed={"version", "date", "summary", "sections", "bundle"})

    if "version" in args:
        # The same rejection as tag-gate.sh check 5, by parsing rather than by grep: an explicit
        # version is a second, hand-typed source of truth that disagrees with the bundle — and
        # with the tag this export is named after — on the release that forgets to bump it.
        fail(f"{where}: WhatsNewContent passes an explicit `version:`. Remove it; the version "
             "comes from the tag (and the pane's from CFBundleShortVersionString).")

    if "date" not in args:
        fail(f"{where}: WhatsNewContent has no `date:`.")
    date_lit = _parse_value(args["date"], where)
    if date_lit.is_key:
        fail(f"{where}: `date:` must be a plain string literal — the export carries one date for "
             "every language, so a localized date cannot be represented.")
    if not date_lit.text.strip():
        fail(f"{where}: `date:` is empty.")

    if "summary" not in args:
        fail(f"{where}: WhatsNewContent has no `summary:`. Every release says what it is, "
             "including a maintenance-only one.")
    summary = _parse_value(args["summary"], where)
    if not summary.text.strip():
        fail(f"{where}: `summary:` is empty.")

    sections = []
    if "sections" in args:
        sections_src = args["sections"].strip()
        if not sections_src.startswith("["):
            fail(f"{where}: `sections:` must be an array literal, got {sections_src[:60]!r}.")
        inner = sections_src[1:_matching(sections_src, 0, "[", "]", where) - 1]
        for element in split_top_level(inner, where):
            sections.append(_parse_section(element, where))

    if not sections and "ChangeSection" in code:
        # Deny-by-default. A maintenance-only release legitimately has no sections — tag-gate.sh
        # check 6 accepts one that says so in its summary — but a file that MENTIONS ChangeSection
        # and yields none means this parser went blind, which would publish notes with the entries
        # quietly missing.
        fail(f"{where}: the file contains `ChangeSection` but no section was parsed out of "
             "`sections:`. Refusing to publish notes whose entries this exporter cannot see.")

    return ParsedNotes(date_lit.text, summary, sections)


def _parse_section(element, where):
    s = element.strip()
    if not re.match(r"ChangeSection\s*\(", s):
        fail(f"{where}: expected ChangeSection(...) in `sections:`, got {s[:60]!r}.")
    open_paren = s.index("(")
    end = _matching(s, open_paren, "(", ")", where)
    if s[end:].strip():
        fail(f"{where}: trailing text after ChangeSection(...): {s[end:][:40]!r}")
    args = _named_args(s[open_paren + 1:end - 1], where, allowed={"kind", "entries"})

    if "kind" not in args:
        fail(f"{where}: ChangeSection has no `kind:`.")
    kind = args["kind"].strip().lstrip(".")
    kind = kind.split(".")[-1]  # `.fixed` or `ChangeSection.Kind.fixed`
    if kind not in KINDS:
        fail(f"{where}: unknown ChangeSection kind {kind!r}. DragonKit defines: "
             f"{', '.join(KINDS)}.")

    if "entries" not in args:
        fail(f"{where}: the {kind!r} section has no `entries:`.")
    entries_src = args["entries"].strip()
    if not entries_src.startswith("["):
        fail(f"{where}: `entries:` must be an array literal, got {entries_src[:60]!r}.")
    inner = entries_src[1:_matching(entries_src, 0, "[", "]", where) - 1]
    entries = [_parse_value(chunk, where) for chunk in split_top_level(inner, where)]
    if not entries:
        fail(f"{where}: the {kind!r} section has no entries. Drop the section or fill it.")
    return kind, entries


# --------------------------------------------------------------------------- .strings tables
STRINGS_PAIR_RE = re.compile(r'"((?:\\.|[^"\\])*)"\s*=\s*"((?:\\.|[^"\\])*)"\s*;', re.S)


def parse_strings(path, where):
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Xcode still writes UTF-16 .strings for some projects; none of the five do today, and a
        # wrong guess would produce mojibake notes, so try it explicitly and fail if it is neither.
        try:
            text = path.read_text(encoding="utf-16")
        except (OSError, UnicodeDecodeError) as exc:
            fail(f"{where}: {path} is neither UTF-8 nor UTF-16 ({exc}).")
    except OSError as exc:
        fail(f"{where}: cannot read {path} ({exc}).")
    table = {}
    for m in STRINGS_PAIR_RE.finditer(strip_comments(text, where)):
        key = _decode_escapes(m.group(1), where)
        table[key] = _decode_escapes(m.group(2), where)
    if not table:
        fail(f"{where}: {path} defines no key/value pairs. An empty table would silently fall "
             "back to English for that language.")
    return table


def _language_of(path):
    """The `<lang>` of a `<lang>.lproj/Localizable.strings` path, or None."""
    for part in path.parts:
        if part.endswith(".lproj"):
            return part[:-len(".lproj")]
    return None


def find_tables(repo_dir, config_path, listed, app):
    """Language tables: everything under the config's directory, plus anything listed explicitly.

    A union, deliberately. clipmenu-2's caller lists only the .swift (its keys are English
    sentences, so the .swift alone is already a real gate check) yet the app ships seven
    translations, and an export that dropped six of them because of how the gate input is worded
    would be the same silent narrowing in a new place. Adding a language can only make the export
    more complete, and every language added is then held to the same "every key resolves" rule.
    """
    tables = {}
    root = (repo_dir / config_path).parent
    for found in sorted(root.rglob("*.lproj/Localizable.strings")):
        lang = _language_of(found.relative_to(repo_dir))
        if lang:
            tables[lang] = found
    for rel in listed:
        if rel.name != "Localizable.strings":
            continue
        lang = _language_of(rel)
        if not lang:
            fail(f"{app}: whats_new_path lists {rel} which is not inside a *.lproj directory, so "
                 "its language cannot be determined.")
        tables.setdefault(lang, repo_dir / rel)
    return tables


# --------------------------------------------------------------------------- resolution
def resolve(lit, lang, table, app, table_path):
    """One literal in one language, or a hard failure. There is no third outcome."""
    if not lit.is_key:
        # ice-2: no .strings in the repository at all, so the app itself shows this text in every
        # language. Passing it through mirrors the pane; it does not guess at a translation.
        return lit.text

    key = lit.text
    dotted = bool(DOTTED_KEY_RE.match(key))
    if key in table:
        value = table[key]
        if not value.strip():
            fail(f'{app} / {lang}: L("{key}") is defined but empty in {table_path}.')
        if DOTTED_KEY_RE.match(value):
            # A table that echoes the key back defeats the check below, and would publish
            # "app.whatsNew.summary" as the note text just as surely as a missing key.
            fail(f'{app} / {lang}: L("{key}") resolves to {value!r} in {table_path}, which is '
                 "itself a localization key, not release-note text.")
        return value

    if dotted:
        fail(f'{app} / {lang}: L("{key}") does not resolve. Expected it in '
             f"{table_path or f'a {lang}.lproj/Localizable.strings'}.\n"
             f"    Publishing the key itself would put \"{key}\" on the release page and the "
             "marketing site as this release's notes, in that language. Add the string, or take "
             "the entry out of WhatsNewConfig.swift.")

    if lang == DEFAULT_LANGUAGE:
        # clipmenu-2's style: the key IS the English sentence, and DragonKit's L() returns the key
        # when a lookup misses, so this is the text the app shows too.
        return key

    fail(f'{app} / {lang}: L("{key}") has no translation in '
         f"{table_path or f'a {lang}.lproj/Localizable.strings'}.\n"
         "    The key is English text, so this language would show English. Translate it, or "
         "remove that language's table.")


def build_export(app, version, notes, tables, languages):
    out = {}
    for lang in languages:
        path = tables.get(lang)
        table = parse_strings(path, f"{app} / {lang}") if path else {}
        out[lang] = {
            "summary": resolve(notes.summary, lang, table, app, path),
            "sections": [
                {"kind": kind,
                 "entries": [resolve(e, lang, table, app, path) for e in entries]}
                for kind, entries in notes.sections
            ],
        }
    return {
        "schema": SCHEMA_VERSION,
        "app": app,
        "version": version,
        "date": notes.date,
        "default_language": DEFAULT_LANGUAGE,
        "languages": out,
    }


def order_languages(found):
    """The kit's seven first, in the kit's order, then anything else alphabetically.

    English is always present: it is `default_language`, every consumer falls back to it, and for
    the two English-authored styles it needs no table at all.
    """
    langs = set(found) | {DEFAULT_LANGUAGE}
    known = [l for l in KIT_LANGUAGES if l in langs]
    return known + sorted(langs - set(KIT_LANGUAGES))


# --------------------------------------------------------------------------- the release body
def render_body(export, github_repo, tag, previous_tag):
    """The GitHub Release body: the English notes, and nothing this file did not read.

    Shape: the summary as a paragraph, then one `### Kind` heading per section in the app's order
    with its entries as a bullet list, then GitHub's own `**Full Changelog**` compare link — the
    one part of --generate-notes worth keeping, since a diff between two tags is genuinely useful
    and is not a restatement of the notes. The link is omitted for a first release, where there is
    no preceding tag to compare against.
    """
    en = export["languages"][DEFAULT_LANGUAGE]
    parts = [en["summary"].strip()]
    for section in en["sections"]:
        lines = [f"### {KIND_TITLES[section['kind']]}", ""]
        for entry in section["entries"]:
            # An entry from a """ block can carry newlines; indent continuation lines so they stay
            # inside the same bullet instead of ending the list.
            lines.append("- " + entry.strip().replace("\n", "\n  "))
        parts.append("\n".join(lines))
    if previous_tag and tag and github_repo:
        parts.append(f"**Full Changelog**: "
                     f"https://github.com/{github_repo}/compare/{previous_tag}...{tag}")
    return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------- CLI
def split_paths(raw):
    """Split whats_new_path exactly as the gate does: on commas and whitespace."""
    return [Path(p) for p in raw.replace(",", " ").split()]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export a Dragon app's What's New pane as JSON.")
    ap.add_argument("--app-name", required=True, help="the app slug, e.g. spectacle-2")
    ap.add_argument("--repo-dir", default=".", help="the app checkout (default: .)")
    ap.add_argument("--whats-new-path", required=True,
                    help="the caller's whats_new_path: config file first, then .strings")
    ap.add_argument("--version", default="",
                    help="X.Y.Z from the tag gate's VERSION. Empty on a verification run with "
                         "no tag, where nothing is published.")
    ap.add_argument("--tag", default="")
    ap.add_argument("--previous-tag", default="")
    ap.add_argument("--github-repo", default="", help="owner/name, for the compare link")
    ap.add_argument("--json-out", default="", help="write whats-new.json here")
    ap.add_argument("--body-out", default="", help="write the English Release body here")
    args = ap.parse_args(argv)

    if args.version and not VERSION_RE.match(args.version):
        fail(f"--version {args.version!r} is not X.Y.Z. It must be the tag gate's VERSION, so "
             "the artifact and the tag cannot disagree.")
    if args.version and args.tag and args.tag.lstrip("v") != args.version:
        # The marketing site's reader compares this field to the release tag and REJECTS the whole
        # asset when they differ, rather than publishing one version's notes under another number.
        # A rejected asset is not a visible failure — the app just quietly keeps its old row on the
        # site. The two values arrive from one source (the gate derives VERSION from TAG), so a
        # disagreement means somebody rewired the step; say so here instead of shipping it.
        fail(f"--version {args.version!r} does not match --tag {args.tag!r}. The artifact's version "
             "must be the tag's, or the site's reader discards the notes without publishing them.")

    repo_dir = Path(args.repo_dir).resolve()
    paths = split_paths(args.whats_new_path)
    if not paths:
        fail("--whats-new-path is empty. It is the same value the tag gate requires.")
    config_rel = paths[0]
    config = repo_dir / config_rel
    if not config.is_file():
        fail(f"{args.app_name}: the What's New config {config_rel} does not exist in the checkout.")

    notes = parse_config(config, args.app_name)
    tables = find_tables(repo_dir, config_rel, paths[1:], args.app_name)
    languages = order_languages(tables)
    export = build_export(args.app_name, args.version, notes, tables, languages)

    kinds = ", ".join(f"{s['kind']}×{len(s['entries'])}" for s in
                      export["languages"][DEFAULT_LANGUAGE]["sections"]) or "no sections"
    print(f"{args.app_name} {args.version or '(no tag)'}: {len(languages)} language(s) "
          f"[{' '.join(languages)}], {kinds}")

    # Not a failure — a new .lproj should not block a release — but say it out loud. The site's
    # reader maps only DragonKit's seven codes, so a language outside them is carried in the
    # artifact and then ignored, which is the sort of thing to learn from a log rather than from
    # a missing translation on the site.
    extra = [l for l in languages if l not in KIT_LANGUAGES]
    if extra:
        print(f"::warning::{args.app_name}: {', '.join(extra)} is not one of DragonKit's "
              f"{len(KIT_LANGUAGES)} language codes ({', '.join(KIT_LANGUAGES)}). It is exported, "
              "but the marketing site maps only those.")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(export, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json_out}")
    if args.body_out:
        Path(args.body_out).write_text(
            render_body(export, args.github_repo, args.tag, args.previous_tag), encoding="utf-8")
        print(f"wrote {args.body_out}")
    if not args.json_out and not args.body_out:
        json.dump(export, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ExportError as exc:
        print(f"::error::whats-new export: {exc}", file=sys.stderr)
        sys.exit(1)
