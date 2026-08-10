#!/usr/bin/env python3
"""O1 spike — does the Onshape half of the wglink contract actually work?

Throwaway. Proves or disproves the five things CAD-LINK-PLAN.md §8.5 says the
Onshape adapter depends on, before any of it is built for real:

  1. authenticate with an API key pair
  2. create a document and a Variable Studio, push ``wg_*`` variables into it
  3. upload a STEP as a blob element and poll the translation to DONE
  4. RE-UPLOAD a modified STEP and confirm the imported part updates in place
  5. confirm a downstream reference survives that update

Step 4 is the one that matters. Everything else is plumbing; if a blob
re-upload does not propagate to the imported part, the Onshape adapter needs a
different substrate and §8.2's mechanism map is wrong.

Credentials are read from ``~/.config/hornlab/onshape.env`` (a KEY=VALUE file
outside every git repo, mode 600), or from environment variables of the same
names, which take precedence. Never hard-code them and never pass them as
command-line arguments — argv is visible to other processes and lands in shell
history.

Usage:

    python3 onshape_o1_spike.py verify-endpoints   # no account changes
    python3 onshape_o1_spike.py auth               # no account changes
    python3 onshape_o1_spike.py run --step-a a.step --step-b b.step
    python3 onshape_o1_spike.py cleanup --did <document-id>

``run`` CREATES a document in your Onshape account. On the free tier it will be
PUBLIC — do not point this at anything confidential. It prints the document id
and leaves it in place so you can inspect the result in the browser; run
``cleanup`` when you are done.

Requires: requests  (pip install requests)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - spike
    sys.exit("This spike needs `requests`:  pip install requests")

BASE = os.environ.get("ONSHAPE_BASE_URL", "https://cad.onshape.com/api")

# Endpoint paths this spike depends on. Several were taken from documentation
# and forum evidence rather than verified against a live account, so
# `verify-endpoints` checks them against the published OpenAPI spec before
# anything is created. Treat a MISSING result as "the plan's §8.2 mechanism map
# needs correcting", not as a bug in this script.
ENDPOINTS_UNDER_TEST = [
    ("GET", "/users/sessioninfo", "auth check"),
    ("POST", "/documents", "create document"),
    ("POST", "/blobelements/d/{did}/w/{wid}", "upload + translate STEP"),
    ("POST", "/blobelements/d/{did}/w/{wid}/e/{eid}", "RE-UPLOAD (the step that matters)"),
    ("GET", "/translations/{tid}", "poll translation"),
    # Verified 2026-08-08 against the live spec: creation lives under /variables,
    # NOT at /variablestudios/... as originally guessed.
    ("POST", "/variables/d/{did}/w/{wid}/variablestudio", "create Variable Studio"),
    ("POST", "/variables/d/{did}/w/{wid}/e/{eid}/variables", "push wg_* variables"),
    # Spec templates the workspace selector as {wvm}/{wvmid}; at runtime the
    # literal is "w"/<workspace-id>, so the request URL is …/w/{wid}/elements.
    ("GET", "/documents/d/{did}/{wvm}/{wvmid}/elements", "list elements"),
    ("DELETE", "/documents/{did}", "cleanup"),
]

# What WG would push for a freestanding R-OSSE horn. Names are namespaced per
# instance because Fusion's parameter table is document-global (plan §4.3); we
# keep the same convention here even though a Variable Studio scopes naturally,
# so one bundle produces identical names in both CAD systems.
SAMPLE_VARIABLES = [
    {"name": "wg_demo_throat_dia", "value": "25.4 mm", "description": "realized throat diameter"},
    {"name": "wg_demo_mouth_w", "value": "320 mm", "description": "realized mouth width"},
    {"name": "wg_demo_mouth_h", "value": "200 mm", "description": "realized mouth height"},
    {"name": "wg_demo_depth", "value": "190 mm", "description": "throat plane to mouth plane"},
]


# Credentials file, deliberately outside every git repo so it cannot be
# committed by accident. Real environment variables win over it.
CREDENTIALS_FILE = Path.home() / ".config" / "hornlab" / "onshape.env"


def _load_credentials_file() -> dict[str, str]:
    """Parse a minimal KEY=VALUE file. Absent file is not an error."""
    if not CREDENTIALS_FILE.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def credentials() -> tuple[str, str]:
    from_file = _load_credentials_file()
    access = os.environ.get("ONSHAPE_ACCESS_KEY") or from_file.get("ONSHAPE_ACCESS_KEY")
    secret = os.environ.get("ONSHAPE_SECRET_KEY") or from_file.get("ONSHAPE_SECRET_KEY")
    if not access or not secret:
        sys.exit(
            f"No credentials found.\n\n"
            f"Paste the key pair into:  {CREDENTIALS_FILE}\n"
            f"  ONSHAPE_ACCESS_KEY=...\n"
            f"  ONSHAPE_SECRET_KEY=...\n\n"
            "Generate a pair at https://dev-portal.onshape.com/keys — and if a\n"
            "secret has ever been pasted into a chat, a terminal share, or a\n"
            "commit, revoke it there and issue a new one first.\n\n"
            "Environment variables of the same names override the file."
        )
    return access, secret


def session() -> requests.Session:
    access, secret = credentials()
    s = requests.Session()
    s.auth = (access, secret)
    s.headers.update({"Accept": "application/json;charset=UTF-8; qs=0.09"})
    return s


def call(s: requests.Session, method: str, path: str, **kw: Any) -> requests.Response:
    url = f"{BASE}{path}"
    r = s.request(method, url, **kw)
    remaining = r.headers.get("X-Rate-Limit-Remaining")
    suffix = f"  [rate-limit remaining: {remaining}]" if remaining else ""
    print(f"  {method} {path} -> {r.status_code}{suffix}")
    if r.status_code >= 400:
        print(f"    {r.text[:500]}")
    return r


# --------------------------------------------------------------------------
# verify-endpoints — resolve my uncertainty before touching the account
# --------------------------------------------------------------------------

def cmd_verify_endpoints(_args: argparse.Namespace) -> int:
    """Check every path this spike uses against the published OpenAPI spec.

    Cheap, read-only, and it converts 'I think this endpoint exists' into a
    fact before the spike creates anything.
    """
    print("Fetching OpenAPI spec ...")
    s = session()
    r = call(s, "GET", "/openapi")
    if r.status_code >= 400:
        print("\nCould not fetch the spec. Check the endpoints by hand in the")
        print("API Explorer: https://cad.onshape.com/glassworks/explorer")
        return 1

    paths = r.json().get("paths", {})
    print(f"\nSpec lists {len(paths)} paths. Checking the {len(ENDPOINTS_UNDER_TEST)} this spike needs:\n")

    missing = 0
    for method, path, why in ENDPOINTS_UNDER_TEST:
        # Spec templates use the same {braces}; compare on the literal template.
        found = path in paths and method.lower() in paths.get(path, {})
        if not found:
            # Tolerate parameter-name differences (…/{did} vs …/{documentId})
            # but NOT structural ones. An earlier version substring-matched the
            # literal segments, which made "d" match almost any path and
            # reported two wrong endpoints as OK. Require the same segment
            # count and identical literal segments, placeholders aligned.
            want = path.strip("/").split("/")
            for candidate in paths:
                got = candidate.strip("/").split("/")
                if len(got) != len(want) or method.lower() not in paths[candidate]:
                    continue
                if all(
                    w == g or (w.startswith("{") and g.startswith("{"))
                    for w, g in zip(want, got)
                ):
                    found = True
                    break
        mark = "OK     " if found else "MISSING"
        if not found:
            missing += 1
        print(f"  [{mark}] {method:6} {path}\n            {why}")

    print()
    if missing:
        print(f"{missing} endpoint(s) not found as written. Correct them here and in")
        print("CAD-LINK-PLAN.md §8.2 before running `run`.")
        return 1
    print("All endpoints resolve. Safe to proceed to `run`.")
    return 0


def cmd_auth(_args: argparse.Namespace) -> int:
    print("Checking credentials ...")
    s = session()
    r = call(s, "GET", "/users/sessioninfo")
    if r.status_code >= 400:
        print("\nAuthentication failed. Regenerate the key pair and re-export it.")
        return 1
    info = r.json()
    print(f"\n  Authenticated as: {info.get('name', '?')}  <{info.get('email', '?')}>")
    print("  (No documents were created.)")
    return 0


# --------------------------------------------------------------------------
# run — the actual spike
# --------------------------------------------------------------------------

def poll_translation(s: requests.Session, tid: str, timeout_s: int = 300) -> dict[str, Any] | None:
    """Poll until DONE or FAILED. Translation is asynchronous — §8.2."""
    print(f"  polling translation {tid} ...")
    deadline = time.monotonic() + timeout_s
    delay = 2.0
    while time.monotonic() < deadline:
        r = call(s, "GET", f"/translations/{tid}")
        if r.status_code >= 400:
            return None
        body = r.json()
        state = body.get("requestState")
        if state == "DONE":
            print(f"    DONE — result elements: {body.get('resultElementIds')}")
            return body
        if state == "FAILED":
            print(f"    FAILED — {body.get('failureReason')}")
            return None
        time.sleep(delay)
        delay = min(delay * 1.5, 15.0)
    print("    timed out")
    return None


def upload_step(
    s: requests.Session, did: str, wid: str, step: Path, eid: str | None = None
) -> dict[str, Any] | None:
    """Upload a STEP as a blob element, or re-upload over an existing one.

    ``eid=None`` creates the element; passing an ``eid`` is the update path —
    the single most important call in this spike.
    """
    path = f"/blobelements/d/{did}/w/{wid}" + (f"/e/{eid}" if eid else "")
    with step.open("rb") as fh:
        files = {"file": (step.name, fh, "application/step")}
        data = {
            "encodedFilename": step.name,
            "fileContentLength": str(step.stat().st_size),
            "translate": "true",
            "flattenAssemblies": "false",
            "yAxisIsUp": "false",
            "allowFaultyParts": "true",
        }
        r = call(s, "POST", path, files=files, data=data)
    if r.status_code >= 400:
        return None
    return r.json()


def cmd_run(args: argparse.Namespace) -> int:
    step_a = Path(args.step_a)
    step_b = Path(args.step_b)
    for p in (step_a, step_b):
        if not p.is_file():
            sys.exit(f"Not a file: {p}")
    if step_a.read_bytes() == step_b.read_bytes():
        sys.exit(
            "step-a and step-b are identical — the spike would prove nothing.\n"
            "Export the same waveguide twice from WG with a changed coverage\n"
            "angle so the wall topology genuinely differs."
        )

    s = session()
    results: dict[str, str] = {}

    print("\n[1/5] Authenticate")
    if call(s, "GET", "/users/sessioninfo").status_code >= 400:
        return 1
    results["auth"] = "PASS"

    print("\n[2/5] Create document")
    r = call(
        s,
        "POST",
        "/documents",
        json={"name": "WGLINK O1 spike (delete me)", "ownerType": 0, "isPublic": True},
    )
    if r.status_code >= 400:
        return 1
    doc = r.json()
    did = doc["id"]
    wid = doc["defaultWorkspace"]["id"]
    print(f"    document {did}  workspace {wid}")
    print(f"    https://cad.onshape.com/documents/{did}/w/{wid}")
    results["create_document"] = "PASS"

    print("\n[3/5] Variable Studio + wg_* variables")
    r = call(s, "POST", f"/variables/d/{did}/w/{wid}/variablestudio", json={"name": "WGLink Variables"})
    if r.status_code >= 400:
        results["variable_studio"] = "FAIL — could not create Variable Studio"
    else:
        veid = r.json().get("id")
        r = call(
            s,
            "POST",
            f"/variables/d/{did}/w/{wid}/e/{veid}/variables",
            json=SAMPLE_VARIABLES,
        )
        results["variable_studio"] = "PASS" if r.status_code < 400 else "FAIL — could not push variables"

    print("\n[4/5] Upload STEP A and translate")
    created = upload_step(s, did, wid, step_a)
    if not created:
        results["import"] = "FAIL — upload rejected"
        summarise(results, did)
        return 1
    blob_eid = created.get("id")
    tid = created.get("translationId") or created.get("id")
    done = poll_translation(s, tid) if tid else None
    results["import"] = "PASS" if done else "FAIL — translation did not reach DONE"
    print(f"    blob element: {blob_eid}")

    print("\n[5/5] RE-UPLOAD STEP B over the same blob element  <-- the decisive step")
    print("      Before continuing, open the document in a browser and add a")
    print("      downstream feature that references the imported part and one")
    print("      wg_* variable. Then come back and press Enter.")
    if not args.yes:
        input("      Press Enter when the downstream feature exists ... ")

    updated = upload_step(s, did, wid, step_b, eid=blob_eid)
    if not updated:
        results["reimport"] = "FAIL — re-upload rejected"
    else:
        tid2 = updated.get("translationId") or updated.get("id")
        done2 = poll_translation(s, tid2) if tid2 else None
        results["reimport"] = "PASS" if done2 else "FAIL — re-translation did not reach DONE"

    results["downstream_survived"] = "MANUAL — inspect the document and record the answer"
    summarise(results, did)
    return 0


def summarise(results: dict[str, str], did: str) -> None:
    print("\n" + "=" * 68)
    print("O1 SPIKE RESULT")
    print("=" * 68)
    for k, v in results.items():
        print(f"  {k:22} {v}")
    print(f"\n  document: https://cad.onshape.com/documents/{did}")
    print(f"  cleanup:  python3 {Path(__file__).name} cleanup --did {did}")
    print("\n  Record the outcome in CAD-LINK-PLAN.md §8 and, if any step failed,")
    print("  correct the §8.2 mechanism map before writing adapter code.")


def cmd_cleanup(args: argparse.Namespace) -> int:
    s = session()
    r = call(s, "DELETE", f"/documents/{args.did}")
    return 0 if r.status_code < 400 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify-endpoints", help="check API paths against the OpenAPI spec (read-only)")
    sub.add_parser("auth", help="verify credentials (read-only)")

    run = sub.add_parser("run", help="run the full spike (CREATES a public document)")
    run.add_argument("--step-a", required=True, help="first STEP export")
    run.add_argument("--step-b", required=True, help="second STEP, same design, changed parameter")
    run.add_argument("--yes", action="store_true", help="skip the manual downstream-feature pause")

    cl = sub.add_parser("cleanup", help="delete a spike document")
    cl.add_argument("--did", required=True)

    args = ap.parse_args()
    return {
        "verify-endpoints": cmd_verify_endpoints,
        "auth": cmd_auth,
        "run": cmd_run,
        "cleanup": cmd_cleanup,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
