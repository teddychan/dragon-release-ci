#!/usr/bin/env python3
"""Tests for scripts/asc-submit.py, against an in-memory App Store Connect. No network, no key.

The fake serves only the endpoints the script uses, with the response shapes App Store Connect
returned for ClipMenu 2 (read live with a read-only key). Each test asserts what a release owner
can observe: the exit code, what the store ends up holding, and that a dry run or a refusal
writes nothing.

Run: python3 scripts/test-asc-submit.py
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("asc_submit", HERE / "asc-submit.py")
asc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asc)

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


LANGS = ["en", "fr", "es", "ja", "ko", "zh-Hans", "zh-Hant", "ru"]
STORE_LOCALES = ["en-US", "fr-FR", "es-ES", "ja", "ko", "zh-Hans", "zh-Hant"]
CONTACT = {"contactFirstName": "Ada", "contactLastName": "Lovelace",
           "contactEmail": "review@example.invalid", "contactPhone": "+1 555 0100"}


def whats_new(version="2.23.0", langs=LANGS):
    return {"schema": 1, "app": "clipmenu-2", "version": version, "default_language": "en",
            "languages": {l: {"summary": f"{l} summary.",
                              "sections": [{"kind": "added", "entries": [f"{l} one."]},
                                           {"kind": "fixed", "entries": [f"{l} two."]}]}
                          for l in langs}}


class FakeASC:
    """Just enough of App Store Connect, keyed by resource id."""

    def __init__(self, version_state=None, contact=CONTACT, locales=STORE_LOCALES,
                 builds=(("VALID",),)):
        self.app_id = "app-1"
        self.versions = {}
        self.localizations = {}
        self.review_details = {}
        self.submissions = {}
        self.writes = []
        self.contact, self.locales = contact, locales
        self.builds = [{"id": f"build-{i}", "state": s[0]} for i, s in enumerate(builds)]
        self.build_polls = 0
        self.ids = 0
        if version_state:
            self._create_version("2.23.0", "MANUAL", version_state)

    def _id(self, prefix):
        self.ids += 1
        return f"{prefix}-{self.ids}"

    def _create_version(self, version_string, release_type, state="PREPARE_FOR_SUBMISSION"):
        vid = self._id("ver")
        self.versions[vid] = {"versionString": version_string, "releaseType": release_type,
                              "appVersionState": state, "appStoreState": state, "build": None}
        for locale in self.locales:
            self.localizations[self._id("loc")] = {"version": vid, "locale": locale,
                                                   "whatsNew": "old notes"}
        self.review_details[vid] = dict(self.contact)
        return vid

    def send(self, method, path, body=None):
        url = urllib.parse.urlsplit(path)
        q = dict(urllib.parse.parse_qsl(url.query))
        p = url.path
        if method != "GET":
            self.writes.append((method, p, body))

        def ok(data, status=200):
            return status, {"data": data}

        if method == "GET" and p == "/v1/apps":
            return ok([{"type": "apps", "id": self.app_id}]
                      if q.get("filter[bundleId]") == "com.dragonapp.clipmenu-2" else [])
        if method == "GET" and (m := re.fullmatch(r"/v1/apps/([^/]+)/appStoreVersions", p)):
            return ok([{"type": "appStoreVersions", "id": vid,
                        "attributes": {k: v for k, v in ver.items() if k != "build"}}
                       for vid, ver in self.versions.items()
                       if ver["versionString"] == q.get("filter[versionString]")])
        if method == "POST" and p == "/v1/appStoreVersions":
            a = body["data"]["attributes"]
            vid = self._create_version(a["versionString"], a["releaseType"])
            return ok({"type": "appStoreVersions", "id": vid}, 201)
        if method == "PATCH" and (m := re.fullmatch(r"/v1/appStoreVersions/([^/]+)", p)):
            self.versions[m[1]].update(body["data"]["attributes"])
            return ok({"type": "appStoreVersions", "id": m[1]})
        if method == "GET" and (m := re.fullmatch(
                r"/v1/appStoreVersions/([^/]+)/appStoreVersionLocalizations", p)):
            return ok([{"type": "appStoreVersionLocalizations", "id": lid,
                        "attributes": {"locale": l["locale"], "whatsNew": l["whatsNew"]}}
                       for lid, l in self.localizations.items() if l["version"] == m[1]])
        if method == "PATCH" and (m := re.fullmatch(r"/v1/appStoreVersionLocalizations/([^/]+)", p)):
            self.localizations[m[1]]["whatsNew"] = body["data"]["attributes"]["whatsNew"]
            return ok({"type": "appStoreVersionLocalizations", "id": m[1]})
        if method == "GET" and (m := re.fullmatch(r"/v1/appStoreVersions/([^/]+)/appStoreReviewDetail", p)):
            return ok({"type": "appStoreReviewDetails", "id": "rd",
                       "attributes": self.review_details[m[1]]})
        if method == "GET" and p == "/v1/builds":
            self.build_polls += 1
            return ok([{"type": "builds", "id": b["id"],
                        "attributes": {"version": "100", "processingState": b["state"],
                                       "expired": False}} for b in self.builds])
        if (m := re.fullmatch(r"/v1/appStoreVersions/([^/]+)/relationships/build", p)):
            if method == "GET":
                b = self.versions[m[1]]["build"]
                return ok({"type": "builds", "id": b} if b else None)
            self.versions[m[1]]["build"] = body["data"]["id"]
            return 204, None
        if method == "GET" and p == "/v1/reviewSubmissions":
            return ok([{"type": "reviewSubmissions", "id": sid, "attributes": {"state": s["state"]}}
                       for sid, s in self.submissions.items() if s["state"] == q.get("filter[state]")])
        if method == "POST" and p == "/v1/reviewSubmissions":
            sid = self._id("sub")
            self.submissions[sid] = {"state": "READY_FOR_REVIEW", "items": []}
            return ok({"type": "reviewSubmissions", "id": sid}, 201)
        if method == "GET" and (m := re.fullmatch(r"/v1/reviewSubmissions/([^/]+)/items", p)):
            return ok([{"type": "reviewSubmissionItems", "id": f"item-{v}", "relationships": {
                "appStoreVersion": {"data": {"type": "appStoreVersions", "id": v}}}}
                for v in self.submissions[m[1]]["items"]])
        if method == "POST" and p == "/v1/reviewSubmissionItems":
            r = body["data"]["relationships"]
            self.submissions[r["reviewSubmission"]["data"]["id"]]["items"].append(
                r["appStoreVersion"]["data"]["id"])
            return ok({"type": "reviewSubmissionItems", "id": self._id("item")}, 201)
        if method == "PATCH" and (m := re.fullmatch(r"/v1/reviewSubmissions/([^/]+)", p)):
            sub = self.submissions[m[1]]
            if body["data"]["attributes"].get("submitted"):
                sub["state"] = "WAITING_FOR_REVIEW"
                for vid in sub["items"]:
                    self.versions[vid]["appVersionState"] = "WAITING_FOR_REVIEW"
                    self.versions[vid]["appStoreState"] = "WAITING_FOR_REVIEW"
            return ok({"type": "reviewSubmissions", "id": m[1]})
        return 404, {"errors": [{"code": "NOT_FOUND", "detail": f"fake has no {method} {p}"}]}

    def version(self, version_string="2.23.0"):
        return next(v for v in self.versions.values() if v["versionString"] == version_string)

    def notes(self, version_string="2.23.0"):
        vid = next(k for k, v in self.versions.items() if v["versionString"] == version_string)
        return {l["locale"]: l["whatsNew"] for l in self.localizations.values()
                if l["version"] == vid}


def run(fake, notes=None, dry_run=False, version="2.23.0", timeout=None):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "whats-new.json")
        with open(path, "w") as f:
            json.dump(notes or whats_new(version), f)
        argv = ["--bundle-id", "com.dragonapp.clipmenu-2", "--version", version,
                "--whats-new", path]
        if dry_run:
            argv.append("--dry-run")
        if timeout is not None:
            argv += ["--build-timeout-minutes", str(timeout)]
        lines, now = [], [0.0]

        def sleep(seconds):
            now[0] += seconds

        rc = asc.main(argv, send=fake.send, env={}, sleep=sleep, clock=lambda: now[0],
                      fetch=None, out=lines.append)
        return rc, "\n".join(lines)


def test_new_version():
    print("a new version is created, filled and submitted")
    fake = FakeASC()
    rc, out = run(fake)
    check("exit 0", rc == 0, out)
    v = fake.version()
    check("the version is created with releaseType AFTER_APPROVAL",
          v["releaseType"] == "AFTER_APPROVAL", v)
    check("the VALID build is attached", v["build"] == "build-0", v)
    en = fake.notes()["en-US"]
    check("en-US What's New is the en summary, a blank line, then a bullet per entry",
          en == "en summary.\n\n• en one.\n• en two.", repr(en))
    check("every store locale gets its own language",
          fake.notes()["zh-Hant"].startswith("zh-Hant summary.") and
          fake.notes()["fr-FR"].startswith("fr summary."), fake.notes())
    check("the version is submitted for review", v["appVersionState"] == "WAITING_FOR_REVIEW", v)

    writes = len(fake.writes)
    rc, out = run(fake)
    check("a second run reports it is already submitted", rc == 0 and "already submitted" in out, out)
    check("a second run writes nothing", len(fake.writes) == writes, fake.writes[writes:])


def test_existing_draft_is_corrected():
    print("an existing editable version is corrected, not duplicated")
    fake = FakeASC(version_state="PREPARE_FOR_SUBMISSION")
    rc, out = run(fake)
    check("exit 0", rc == 0, out)
    check("no second version is created", len(fake.versions) == 1, fake.versions)
    check("a MANUAL release type is switched to AFTER_APPROVAL",
          fake.version()["releaseType"] == "AFTER_APPROVAL", fake.version())
    check("it is submitted", fake.version()["appVersionState"] == "WAITING_FOR_REVIEW", out)


def test_already_submitted():
    print("a version already in review is left alone")
    for state in ("WAITING_FOR_REVIEW", "IN_REVIEW", "READY_FOR_DISTRIBUTION"):
        fake = FakeASC(version_state=state)
        rc, out = run(fake)
        check(f"{state}: exit 0 and says already submitted",
              rc == 0 and "already submitted, nothing to do" in out, out)
        check(f"{state}: writes nothing", not fake.writes, fake.writes)


def test_rejected_needs_a_person():
    print("a rejected version is not resubmitted automatically")
    fake = FakeASC(version_state="REJECTED")
    rc, out = run(fake)
    check("exit 1 naming the state", rc == 1 and "REJECTED" in out, out)
    check("writes nothing", not fake.writes, fake.writes)


def test_missing_contact():
    print("an incomplete App Review contact stops the run")
    fake = FakeASC(contact={**CONTACT, "contactPhone": "", "contactEmail": None})
    rc, out = run(fake)
    check("exit 1", rc == 1, out)
    check("the message names exactly the missing fields",
          "contactEmail, contactPhone" in out and "contactFirstName" not in out, out)
    check("nothing is attached, filled or submitted",
          [w for w in fake.writes if w[1] != "/v1/appStoreVersions"] == [], fake.writes)
    check("no invented contact values", fake.review_details[next(iter(fake.versions))]["contactPhone"] == "",
          fake.review_details)


def test_missing_locale_notes():
    print("a store locale with no notes gets the English notes")
    fake = FakeASC(locales=STORE_LOCALES + ["de-DE"])
    rc, out = run(fake, notes=whats_new(langs=[l for l in LANGS if l != "ko"]))
    check("exit 0", rc == 0, out)
    notes = fake.notes()
    check("ko and de-DE get the en notes", notes["ko"] == notes["de-DE"] == notes["en-US"], notes)
    check("other locales keep their own language", notes["ja"].startswith("ja summary."), notes)
    check("says ko used the English fallback",
          "What's New ko: no 'ko' notes in whats-new.json, using the English notes" in out, out)
    check("says de-DE used the English fallback",
          "What's New de-DE: no language mapping, using the English notes" in out, out)
    check("names no fallback for a locale that has its own notes", "ja: no" not in out, out)
    check("the version is submitted", fake.version()["appVersionState"] == "WAITING_FOR_REVIEW", out)


def test_missing_english_notes():
    print("a store locale that needs English stops the run when English is absent")
    fake = FakeASC(locales=STORE_LOCALES + ["de-DE"])
    rc, out = run(fake, notes=whats_new(langs=[l for l in LANGS if l not in ("en", "ko")]))
    check("exit 1", rc == 1, out)
    for locale in ("en-US", "ko", "de-DE"):
        check(f"names {locale}", f"store locale {locale} needs 'en' notes" in out, out)
    check("no What's New is written", not any("Localizations" in w[1] for w in fake.writes),
          fake.writes)
    check("nothing is submitted", not fake.submissions, fake.submissions)


def test_fallback_notes_over_the_limit():
    print("the 4000-character limit applies to English notes used as a fallback")
    langs = [l for l in LANGS if l != "ko"]
    notes = whats_new(langs=langs)
    notes["languages"]["en"]["summary"] = "x" * 4001
    fake = FakeASC()
    rc, out = run(fake, notes=notes)
    check("exit 1", rc == 1, out)
    length = len(asc.render_notes(notes["languages"]["en"]))
    check("names the fallback locale's length",
          f"ko notes are {length} characters; the limit is 4000" in out, out)
    check("nothing is submitted", not fake.submissions, fake.submissions)


def test_notes_for_another_version():
    print("notes for a different version are refused")
    fake = FakeASC(version_state="PREPARE_FOR_SUBMISSION")
    rc, out = run(fake, notes=whats_new(version="2.22.0"))
    check("exit 1 naming both versions", rc == 1 and "2.22.0" in out and "2.23.0" in out, out)
    check("writes nothing", not fake.writes, fake.writes)


def test_dry_run():
    print("--dry-run prints every write and makes none")
    fake = FakeASC(version_state="PREPARE_FOR_SUBMISSION")
    rc, out = run(fake, dry_run=True)
    check("exit 0", rc == 0, out)
    check("no write reaches the store", not fake.writes, fake.writes)
    for what in ("would PATCH /v1/appStoreVersions/", "would PATCH /v1/appStoreVersions/ver-1/relationships/build",
                 "would PATCH /v1/appStoreVersionLocalizations/", "would POST /v1/reviewSubmissions ",
                 "would POST /v1/reviewSubmissionItems", '"submitted": true'):
        check(f"prints: {what}", what in out, out)

    fake = FakeASC()
    rc, out = run(fake, dry_run=True)
    check("a version that does not exist yet: prints the create and stops cleanly",
          rc == 0 and "would POST /v1/appStoreVersions" in out and not fake.writes, out)


def test_build_wait_is_bounded():
    print("waiting for a build is bounded")
    fake = FakeASC(builds=(("PROCESSING",),))
    rc, out = run(fake, timeout=5)
    check("exit 1 with a timeout message naming what it saw",
          rc == 1 and "No VALID build of 2.23.0 after 5 minutes (seen: PROCESSING)" in out, out)
    check("it polled until the deadline", fake.build_polls == 6, fake.build_polls)
    check("nothing is submitted", not fake.submissions, fake.submissions)


def test_jwt_verifies():
    print("the JWT is a valid ES256 signature")
    with tempfile.TemporaryDirectory() as tmp:
        key, pub = os.path.join(tmp, "k.p8"), os.path.join(tmp, "k.pub")
        subprocess.run(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt",
                        "ec_paramgen_curve:P-256", "-out", key], check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", key, "-pubout", "-out", pub],
                       check=True, capture_output=True)
        jwt = asc.make_jwt("KEYID", "issuer", key, now=1000)
        head, payload, sig = jwt.split(".")
        raw = asc.base64.urlsafe_b64decode(sig + "==")
        check("the signature is 64 raw bytes", len(raw) == 64, len(raw))

        def der_int(b):
            b = b.lstrip(b"\0") or b"\0"
            if b[0] & 0x80:
                b = b"\0" + b
            return bytes([2, len(b)]) + b
        body = der_int(raw[:32]) + der_int(raw[32:])
        der = bytes([0x30, len(body)]) + body
        sig_path = os.path.join(tmp, "sig")
        with open(sig_path, "wb") as f:
            f.write(der)
        r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature", sig_path],
                           input=f"{head}.{payload}".encode(), capture_output=True)
        check("openssl verifies it against the public key", r.returncode == 0, r.stdout + r.stderr)
        claims = json.loads(asc.base64.urlsafe_b64decode(payload + "=="))
        check("claims: issuer, audience, 20-minute expiry",
              claims == {"iss": "issuer", "iat": 1000, "exp": 2200, "aud": "appstoreconnect-v1"},
              claims)


def test_missing_credentials():
    print("missing credentials are named, not guessed")
    lines = []
    rc = asc.main(["--bundle-id", "x", "--version", "1.0.0", "--whats-new", "n.json"],
                  env={}, out=lines.append)
    check("exit 1 naming the three variables",
          rc == 1 and "ASC_KEY_ID, ASC_ISSUER_ID and ASC_KEY_P8" in "\n".join(lines), lines)


def main():
    print("asc-submit.py")
    for test in (test_new_version, test_existing_draft_is_corrected, test_already_submitted,
                 test_rejected_needs_a_person, test_missing_contact, test_missing_locale_notes,
                 test_missing_english_notes, test_fallback_notes_over_the_limit,
                 test_notes_for_another_version, test_dry_run, test_build_wait_is_bounded,
                 test_jwt_verifies, test_missing_credentials):
        test()
    print()
    print(f"{PASS} passed, {FAIL} failed")
    if FAIL:
        return 1
    print("All asc-submit tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
