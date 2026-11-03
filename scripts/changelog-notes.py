#!/usr/bin/env python3
"""Render the GitHub Release body from the app's CHANGELOG.md entry for this version.

The Release notes are the version's changelog entry. The What's New pane is a short in-app
summary, so a body rendered from it came out shorter than the changelog (Spectacle 2 v2.7.0).
whats-new.json is unchanged and still comes from the pane; only the Release body moves here.

  - Finds the `## ` heading for exactly VERSION, in each style the five apps use:
    `## 2.22.0 (2026-11-03)`, `## 2.21.2 — 2026-08-20`, `## 2.16.3 - 2026-11-03`, `## 2.17.0`
    and `## [2.8.0] - 2026-11-03`. `2.8.0` never matches `2.8.01` or `12.8.0`.
  - Takes everything up to the next `#`/`##` heading, without the heading line itself.
  - Unwraps hard-wrapped paragraphs and list-item continuation lines, because GitHub renders a
    single newline in a Release body as a line break. Headings, blank lines, code fences, tables
    and new list items stay on their own lines.
  - Appends the `**Full Changelog**` line from the What's New exporter's body, so the compare link
    has one author.

A repo with no CHANGELOG.md (dragon-sample-app) keeps the What's New body, and so does a
verification run with no tag, where VERSION is empty and nothing is published. A CHANGELOG.md
with no entry for VERSION fails: that is a release whose notes were never written.

Run: python3 scripts/changelog-notes.py --changelog CHANGELOG.md --version 2.8.0 \
       --whats-new-body release-notes.md --out release-notes-changelog.md
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

ENTRY_END_RE = re.compile(r"#{1,2}\s")
FENCE_RE = re.compile(r"\s*(```|~~~)")
LIST_ITEM_RE = re.compile(r"\s*([-*+]|\d+[.)])\s")
# Lines that always start a new block, so nothing joins onto them and they join onto nothing:
# headings, table rows, blockquotes and thematic breaks.
BLOCK_RE = re.compile(r"\s*(#{1,6}\s|\||>|([-*_])(\s*\2){2,}\s*$)")
COMPARE_PREFIX = "**Full Changelog**"


def fail(message):
    print(f"::error::{message}", file=sys.stderr)
    sys.exit(1)


def heading_re(version):
    return re.compile(rf"##\s+\[?{re.escape(version)}\]?(?=$|[\s(])")


def find_entry(text, version):
    """The entry's lines, heading excluded, or None when VERSION has no heading."""
    lines = text.splitlines()
    match = heading_re(version)
    for i, line in enumerate(lines):
        if match.match(line):
            in_fence = False
            for j in range(i + 1, len(lines)):
                if FENCE_RE.match(lines[j]):
                    in_fence = not in_fence
                elif not in_fence and ENTRY_END_RE.match(lines[j]):
                    return lines[i + 1:j]
            return lines[i + 1:]
    return None


def unwrap(lines):
    out = []
    joinable = False
    in_fence = False
    for line in lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            joinable = False
        elif in_fence:
            out.append(line)
        elif not line.strip() or BLOCK_RE.match(line):
            out.append(line)
            joinable = False
        elif LIST_ITEM_RE.match(line):
            out.append(line)
            joinable = True
        elif joinable and not out[-1].endswith(("  ", "\\")):
            out[-1] = out[-1].rstrip() + " " + line.strip()
        else:
            out.append(line)
            joinable = True
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def compare_link(whats_new_body):
    return next((line for line in reversed(whats_new_body.splitlines())
                 if line.startswith(COMPARE_PREFIX)), None)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render the Release body from CHANGELOG.md.")
    ap.add_argument("--changelog", required=True, help="the app's CHANGELOG.md")
    ap.add_argument("--version", default="",
                    help="X.Y.Z from the tag gate's VERSION. Empty on a verification run with "
                         "no tag, where nothing is published.")
    ap.add_argument("--whats-new-body", required=True,
                    help="the body whats-new-export.py wrote: the fallback, and the compare link")
    ap.add_argument("--out", required=True, help="write the Release body here")
    args = ap.parse_args(argv)

    changelog = Path(args.changelog)
    if not args.version:
        print("::notice::No VERSION (verification run with no tag): the Release body stays the "
              "What's New notes, and CHANGELOG.md is not read.")
        shutil.copyfile(args.whats_new_body, args.out)
        return 0
    if not changelog.is_file():
        print(f"::notice::{changelog.name} does not exist, so the Release body for {args.version} "
              "is the What's New notes.")
        shutil.copyfile(args.whats_new_body, args.out)
        return 0

    entry = find_entry(changelog.read_text(encoding="utf-8"), args.version)
    if entry is None:
        fail(f"{changelog.name} has no `## ` heading for {args.version}. The Release notes are "
             "the version's changelog entry: add it, then release a fresh version. Nothing has "
             "been signed or published.")
    body = unwrap(entry)
    if not body:
        fail(f"The {args.version} entry in {changelog.name} is empty.")

    link = compare_link(Path(args.whats_new_body).read_text(encoding="utf-8"))
    text = "\n".join(body) + "\n"
    if link:
        text += "\n" + link + "\n"
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"Release body for {args.version}: {len(body)} line(s) from {changelog.name}"
          + (", plus the compare link" if link else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
