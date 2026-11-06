#!/usr/bin/env python3
"""Submit a Mac App Store version for review through the App Store Connect API.

release-mas.yml uploads the .pkg; this does what a person used to do in App Store Connect
afterwards. Every step reads before it writes, so a re-run after any failure converges:

  1. find the app; find or create the MAC_OS appStoreVersion X.Y.Z, releaseType AFTER_APPROVAL
  2. pick each store locale's notes from whats-new.json (English when its language has none),
     and check that the review contact is complete
  3. wait (bounded) for a VALID build of X.Y.Z and attach it
  4. set whatsNew on every appStoreVersionLocalization
  5. submit: a MAC_OS reviewSubmission holding this version, then submitted=true

A version already WAITING_FOR_REVIEW or later is reported and left alone (exit 0).

Usage:
  ASC_KEY_ID=... ASC_ISSUER_ID=... ASC_KEY_P8=/path/AuthKey.p8 \\
    python3 scripts/asc-submit.py --bundle-id com.dragonapp.clipmenu-2 --version 2.22.0 \\
      --whats-new https://github.com/<owner>/<repo>/releases/download/v2.22.0/whats-new.json \\
      [--dry-run] [--build-timeout-minutes 60]

The key needs the App Manager role: Developer can read but every write returns 403.
Stdlib only. The JWT is signed with `openssl` and requests go through `curl`, because
python.org's Python on macOS ships without CA certificates.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse

API = "https://api.appstoreconnect.apple.com"
PLATFORM = "MAC_OS"
RELEASE_TYPE = "AFTER_APPROVAL"
WHATS_NEW_LIMIT = 4000
POLL_SECONDS = 60

# appVersionState (or the older appStoreState) decides whether there is anything to do.
EDITABLE_STATES = {"PREPARE_FOR_SUBMISSION", "READY_FOR_REVIEW", "DEVELOPER_REJECTED"}
SUBMITTED_STATES = {
    "WAITING_FOR_REVIEW", "IN_REVIEW", "PENDING_APPLE_RELEASE", "PENDING_DEVELOPER_RELEASE",
    "PROCESSING_FOR_DISTRIBUTION", "PROCESSING_FOR_APP_STORE", "READY_FOR_DISTRIBUTION",
    "READY_FOR_SALE", "ACCEPTED", "PREORDER_READY_FOR_SALE", "REPLACED_WITH_NEW_VERSION",
    "REMOVED_FROM_SALE", "DEVELOPER_REMOVED_FROM_SALE",
}

STORE_LOCALE_TO_LANGUAGE = {
    "en-US": "en", "fr-FR": "fr", "es-ES": "es", "ja": "ja", "ko": "ko",
    "zh-Hans": "zh-Hans", "zh-Hant": "zh-Hant", "ru": "ru",
}
# whats-new-export.py always writes English as "en" (its default_language).
FALLBACK_LANGUAGE = "en"

REVIEW_CONTACT_FIELDS = ("contactFirstName", "contactLastName", "contactEmail", "contactPhone")


class Stop(Exception):
    """A condition a person must resolve. The message says what and how."""


# --------------------------------------------------------------------------- auth + transport
def der_to_raw(der):
    """ECDSA DER SEQUENCE{INTEGER r, INTEGER s} -> the 64-byte r||s that JWS ES256 wants."""
    if der[0] != 0x30:
        raise ValueError("not a DER sequence")
    i = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7F)
    raw = b""
    for _ in range(2):
        if der[i] != 0x02:
            raise ValueError("not a DER integer")
        n = der[i + 1]
        raw += der[i + 2:i + 2 + n].lstrip(b"\0").rjust(32, b"\0")
        i += 2 + n
    return raw


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(key_id, issuer_id, p8_path, now=None):
    now = int(now if now is not None else time.time())
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    payload = {"iss": issuer_id, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"}
    signing_input = (b64url(json.dumps(header, separators=(",", ":")).encode()) + "." +
                     b64url(json.dumps(payload, separators=(",", ":")).encode()))
    signed = subprocess.run(["openssl", "dgst", "-sha256", "-sign", p8_path],
                            input=signing_input.encode(), capture_output=True)
    if signed.returncode != 0:
        raise Stop(f"openssl could not sign with ASC_KEY_P8: {signed.stderr.decode().strip()}")
    return signing_input + "." + b64url(der_to_raw(signed.stdout))


def curl_transport(token):
    """send(method, path, body) -> (status, parsed JSON or None). The JWT goes in on stdin."""
    def send(method, path, body=None):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            cmd = ["curl", "-sS", "--globoff", "--max-time", "120", "-X", method, "-H", "@-",
                   "-o", out, "-w", "%{http_code}", API + path]
            headers = f"Authorization: Bearer {token()}\nAccept: application/json\n"
            if body is not None:
                with open(os.path.join(tmp, "body"), "w") as f:
                    json.dump(body, f)
                cmd += ["--data-binary", "@" + os.path.join(tmp, "body")]
                headers += "Content-Type: application/json\n"
            for attempt in range(3):
                r = subprocess.run(cmd, input=headers, capture_output=True, text=True)
                status = int(r.stdout or 0) if r.returncode == 0 else 0
                if method != "GET" or (status and status < 500 and status != 429):
                    break
                time.sleep(5 * (attempt + 1))
            if r.returncode != 0:
                raise Stop(f"curl {method} {path} failed: {r.stderr.strip()}")
            text = open(out).read() if os.path.exists(out) else ""
            try:
                return status, json.loads(text)
            except ValueError:
                return status, None
    return send


def token_source(key_id, issuer_id, p8_path):
    cache = {}

    def token():
        if time.time() - cache.get("at", 0) > 600:
            cache["jwt"], cache["at"] = make_jwt(key_id, issuer_id, p8_path), time.time()
        return cache["jwt"]
    return token


# --------------------------------------------------------------------------- API wrapper
class Api:
    def __init__(self, send, dry_run, out):
        self.send, self.dry_run, self.out = send, dry_run, out

    def request(self, method, path, body=None):
        status, doc = self.send(method, path, body)
        if status >= 300:
            errors = (doc or {}).get("errors") or []
            detail = "; ".join(f"{e.get('code', '')}: {e.get('detail') or e.get('title', '')}"
                               for e in errors) or "no error body"
            raise Stop(f"App Store Connect {method} {path.split('?')[0]} returned {status}: {detail}")
        return doc or {}

    def get(self, path, **query):
        if query:
            path += "?" + urllib.parse.urlencode(query, safe="[],.")
        return self.request("GET", path).get("data")

    def write(self, method, path, body):
        if self.dry_run:
            self.out(f"DRY RUN: would {method} {path} {json.dumps(body, ensure_ascii=False)}")
            return None
        return self.request(method, path, body).get("data")


def resource(type_, id_=None, attributes=None, **relationships):
    data = {"type": type_}
    if id_:
        data["id"] = id_
    if attributes:
        data["attributes"] = attributes
    if relationships:
        data["relationships"] = {k: {"data": {"type": t, "id": i}}
                                 for k, (t, i) in relationships.items()}
    return {"data": data}


# --------------------------------------------------------------------------- notes
def load_whats_new(source, version, fetch):
    text = fetch(source) if source.startswith("https://") else open(source).read()
    doc = json.loads(text)
    if doc.get("version") and doc["version"] != version:
        raise Stop(f"{source} is for version {doc['version']}, not {version}.")
    languages = doc.get("languages")
    if not isinstance(languages, dict) or not languages:
        raise Stop(f"{source} has no 'languages' object.")
    return languages


def render_notes(lang):
    lines = [lang["summary"].strip(), ""]
    lines += [f"• {entry.strip()}" for s in lang.get("sections", []) for entry in s["entries"]]
    return "\n".join(lines).strip()


def plan_whats_new(localizations, languages, out):
    """{localization id: (locale, text)} for every store locale, or Stop naming every gap.

    A store locale with no language mapping, or whose language has no notes, gets the English
    notes; only missing English stops the run.
    """
    plan, problems = {}, []
    for loc in localizations:
        locale = loc["attributes"]["locale"]
        lang = STORE_LOCALE_TO_LANGUAGE.get(locale)
        if lang is None or lang not in languages:
            gap = "no language mapping" if lang is None else f"no '{lang}' notes in whats-new.json"
            lang = FALLBACK_LANGUAGE
            if lang in languages:
                out(f"What's New {locale}: {gap}, using the English notes")
        if lang not in languages:
            problems.append(f"store locale {locale} needs '{lang}' notes, absent from whats-new.json")
        else:
            text = render_notes(languages[lang])
            if len(text) > WHATS_NEW_LIMIT:
                problems.append(f"{locale} notes are {len(text)} characters; the limit is {WHATS_NEW_LIMIT}")
            plan[loc["id"]] = (locale, text)
    if problems:
        raise Stop("What's New cannot be filled for every store locale:\n  " + "\n  ".join(problems))
    return plan


# --------------------------------------------------------------------------- the run
def version_state(version):
    a = version["attributes"]
    return a.get("appVersionState") or a.get("appStoreState") or "UNKNOWN"


def submit(api, args, languages_loader, sleep, clock, out):
    apps = api.get("/v1/apps", **{"filter[bundleId]": args.bundle_id, "limit": 2})
    if not apps:
        raise Stop(f"No app with bundle id {args.bundle_id} is visible to this key.")
    app_id = apps[0]["id"]
    out(f"App {args.bundle_id}: {app_id}")

    versions = api.get(f"/v1/apps/{app_id}/appStoreVersions",
                       **{"filter[platform]": PLATFORM, "filter[versionString]": args.version,
                          "limit": 2})
    version = versions[0] if versions else None
    state = version_state(version) if version else None
    if state in SUBMITTED_STATES:
        out(f"Version {args.version} is already {state}: already submitted, nothing to do.")
        return 0
    languages = languages_loader()
    if version:
        if state not in EDITABLE_STATES:
            raise Stop(f"Version {args.version} is {state}, which needs a person in App Store Connect.")
        version_id = version["id"]
        out(f"Version {args.version}: {version_id} ({state})")
        if version["attributes"].get("releaseType") != RELEASE_TYPE:
            api.write("PATCH", f"/v1/appStoreVersions/{version_id}",
                      resource("appStoreVersions", version_id, {"releaseType": RELEASE_TYPE}))
    else:
        created = api.write("POST", "/v1/appStoreVersions", resource(
            "appStoreVersions",
            attributes={"platform": PLATFORM, "versionString": args.version,
                        "releaseType": RELEASE_TYPE},
            app=("apps", app_id)))
        if created is None:
            out("DRY RUN: the remaining steps read the new version, so the dry run stops here.")
            return 0
        version_id = created["id"]
        out(f"Created version {args.version}: {version_id}")

    localizations = api.get(f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations",
                            limit=200) or []
    if not localizations:
        raise Stop(f"Version {args.version} has no store localizations to fill.")
    notes = plan_whats_new(localizations, languages, out)

    detail = api.get(f"/v1/appStoreVersions/{version_id}/appStoreReviewDetail")
    attrs = (detail or {}).get("attributes") or {}
    missing = [f for f in REVIEW_CONTACT_FIELDS if not (attrs.get(f) or "").strip()]
    if missing:
        raise Stop(f"App Review contact is incomplete for {args.version}: missing "
                   f"{', '.join(missing)}. Fill App Review Information in App Store Connect.")

    build_id = wait_for_build(api, app_id, args, sleep, clock, out)
    attached = api.get(f"/v1/appStoreVersions/{version_id}/relationships/build")
    if build_id and (attached or {}).get("id") != build_id:
        api.write("PATCH", f"/v1/appStoreVersions/{version_id}/relationships/build",
                  {"data": {"type": "builds", "id": build_id}})
        out(f"Attached build {build_id}")
    elif build_id:
        out(f"Build {build_id} already attached")

    by_id = {loc["id"]: loc for loc in localizations}
    for loc_id, (locale, text) in sorted(notes.items(), key=lambda kv: kv[1][0]):
        if (by_id[loc_id]["attributes"].get("whatsNew") or "").strip() == text:
            out(f"What's New {locale}: already current")
            continue
        api.write("PATCH", f"/v1/appStoreVersionLocalizations/{loc_id}",
                  resource("appStoreVersionLocalizations", loc_id, {"whatsNew": text}))
        out(f"What's New {locale}: set")

    drafts = api.get("/v1/reviewSubmissions",
                     **{"filter[app]": app_id, "filter[platform]": PLATFORM,
                        "filter[state]": "READY_FOR_REVIEW", "limit": 10}) or []
    if drafts:
        submission_id = drafts[0]["id"]
        out(f"Reusing draft review submission {submission_id}")
        items = api.get(f"/v1/reviewSubmissions/{submission_id}/items",
                        include="appStoreVersion", limit=50) or []
        holds = any((((i.get("relationships") or {}).get("appStoreVersion") or {})
                     .get("data") or {}).get("id") == version_id for i in items)
    else:
        created = api.write("POST", "/v1/reviewSubmissions", resource(
            "reviewSubmissions", attributes={"platform": PLATFORM}, app=("apps", app_id)))
        submission_id = created["id"] if created else "(new reviewSubmission)"
        holds = False
    if not holds:
        api.write("POST", "/v1/reviewSubmissionItems", resource(
            "reviewSubmissionItems",
            reviewSubmission=("reviewSubmissions", submission_id),
            appStoreVersion=("appStoreVersions", version_id)))
    api.write("PATCH", f"/v1/reviewSubmissions/{submission_id}",
              resource("reviewSubmissions", submission_id, {"submitted": True}))
    out(("DRY RUN: would submit" if api.dry_run else "Submitted") +
        f" {args.bundle_id} {args.version} for App Review.")
    return 0


def wait_for_build(api, app_id, args, sleep, clock, out):
    deadline = clock() + args.build_timeout_minutes * 60
    while True:
        builds = api.get("/v1/builds", **{
            "filter[app]": app_id, "filter[preReleaseVersion.version]": args.version,
            "filter[preReleaseVersion.platform]": PLATFORM, "sort": "-uploadedDate",
            "limit": 50}) or []
        live = [b for b in builds if not b["attributes"].get("expired")]
        valid = [b for b in live if b["attributes"].get("processingState") == "VALID"]
        if valid:
            b = valid[0]
            out(f"Build {b['attributes'].get('version')} ({b['id']}) is VALID")
            return b["id"]
        seen = ", ".join(sorted(b["attributes"].get("processingState", "?") for b in live)) or "none"
        if api.dry_run:
            out(f"DRY RUN: no VALID build of {args.version} yet (seen: {seen}); a real run "
                f"would wait up to {args.build_timeout_minutes} minutes.")
            return None
        if clock() >= deadline:
            raise Stop(f"No VALID build of {args.version} after {args.build_timeout_minutes} "
                       f"minutes (seen: {seen}). Re-run once App Store Connect finishes "
                       "processing; every step is safe to repeat.")
        out(f"Waiting for a VALID build of {args.version} (seen: {seen})")
        sleep(POLL_SECONDS)


# --------------------------------------------------------------------------- entry point
def fetch_url(url):
    r = subprocess.run(["curl", "-sSfL", "--max-time", "60", url], capture_output=True, text=True)
    if r.returncode != 0:
        raise Stop(f"Could not download {url}: {r.stderr.strip()}")
    return r.stdout


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--bundle-id", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--whats-new", required=True, help="whats-new.json path or https URL")
    p.add_argument("--dry-run", action="store_true", help="print every write, make none")
    p.add_argument("--build-timeout-minutes", type=int, default=60)
    return p.parse_args(argv)


def main(argv=None, send=None, env=os.environ, sleep=time.sleep, clock=time.monotonic,
         fetch=fetch_url, out=print):
    args = parse_args(argv)
    try:
        if send is None:
            creds = [env.get(k) for k in ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_P8")]
            if not all(creds):
                raise Stop("Set ASC_KEY_ID, ASC_ISSUER_ID and ASC_KEY_P8 (path to the .p8).")
            send = curl_transport(token_source(*creds))
        api = Api(send, args.dry_run, out)
        return submit(api, args, lambda: load_whats_new(args.whats_new, args.version, fetch),
                      sleep, clock, out)
    except Stop as e:
        out(f"error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
