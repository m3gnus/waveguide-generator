"""Find, read, replay and export WG's incident records (developer tool).

    python -m server.tools.cad_diag list [--since 1d] [--kind internal-failure]
    python -m server.tools.cad_diag show <id>
    python -m server.tools.cad_diag replay <id>
    python -m server.tools.cad_diag export <id> --out DIR [--redact]

Records live in ``<data>/diagnostics/incidents`` (``server.platform.incidents``).
``replay`` runs ``server.tools.cad_replay`` in its own process on the recorded
request or retained bundle, in a new scratch directory, and says whether the
recorded failure is fixed, still fails, or fails differently. Nothing here
writes into the live data directory.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

from server.platform.incidents import IncidentStore, Redactor, account_name, redaction_rules, summary
from server.platform.paths import app_root, resolve_data_dir


FIXED = "fixed"
STILL_FAILS = "still fails"
FAILS_DIFFERENTLY = "fails differently"
#: The replay never reached the pipeline: its request or bundle is gone.
NOT_REPLAYED = "could not replay"
#: Exit codes: a script can tell the outcomes apart.
EXIT_CODES = {FIXED: 0, STILL_FAILS: 1, NOT_REPLAYED: 2, FAILS_DIFFERENTLY: 3}

_DURATION = re.compile(r"^(\d+)([mhdw])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
#: Request ids ``cad_replay --from-request`` accepts.
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_DIGEST = re.compile(r"(?:sha256:)?[0-9a-f]{64}")
_STEP_FILE_NAME = re.compile(r"FILE_NAME\s*\(.*?\)\s*;", re.DOTALL)
REDACTED_NAME = "redacted-model"


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """``30m``, ``2h``, ``1d`` or ``1w`` before now."""

    match = _DURATION.match(value.strip())
    if match is None:
        raise argparse.ArgumentTypeError("use a duration such as 30m, 2h, 1d or 1w")
    amount, unit = int(match.group(1)), match.group(2)
    return (now or datetime.now(timezone.utc)) - timedelta(**{_UNITS[unit]: amount})


def _store(data_dir: Path) -> IncidentStore:
    return IncidentStore(data_dir)


def _require(store: IncidentStore, incident_id: str) -> dict[str, Any]:
    record = store.get(incident_id)
    if record is None:
        raise SystemExit(f"No incident {incident_id!r} (or the prefix is ambiguous) in {store.root}")
    return record


# -- list / show -----------------------------------------------------------------


def list_lines(records: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = []
    for record in records:
        line = summary(record)
        lines.append(
            f"{line['id']}  {line['lastSeen']}  x{line['count']}  {line['phase']}"
            + (f"  {line['message']}" if line.get("message") else "")
        )
    return lines


# -- replay -----------------------------------------------------------------------


def replay_arguments(record: Mapping[str, Any], data_dir: Path) -> list[str]:
    """The ``cad_replay`` arguments that reproduce this incident's request."""

    ids = (record.get("latest") or {}).get("ids") or {}
    operation = str(ids.get("operation_id") or "")
    if _REQUEST_ID.fullmatch(operation):
        return ["--from-request", operation, "--live-data-dir", str(data_dir)]
    digest = str(ids.get("manifest_sha256") or "")
    if _DIGEST.fullmatch(digest):
        bundle = retained_bundle(data_dir, digest)
        if bundle.is_dir():
            return [str(bundle), "--retained"]
    raise SystemExit(
        "This incident names no replayable request: it has no inbox request id and no "
        "retained bundle digest whose copy still exists."
    )


def retained_bundle(data_dir: Path, digest: str) -> Path:
    from server.cadlink.intake.ingest import retained_snapshot_path

    return retained_snapshot_path(data_dir, digest)


def run_cad_replay(arguments: Sequence[str]) -> dict[str, Any]:
    """Run the replay tool in its own process and return its JSON report."""

    environment = {key: value for key, value in os.environ.items() if key != "WG2_DATA_DIR"}
    completed = subprocess.run(
        [sys.executable, "-m", "server.tools.cad_replay", *arguments, "--json"],
        cwd=app_root(), env=environment, capture_output=True, text=True, check=False,
    )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"cad_replay did not produce a report (exit {completed.returncode}):\n{completed.stderr[-4000:]}"
        ) from exc


def _diagnostics(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        message["cad_diagnostic"] for message in payload.get("logs") or ()
        if isinstance(message, Mapping) and isinstance(message.get("cad_diagnostic"), Mapping)
    ]


def _same_failure(record: Mapping[str, Any], diagnostic: Mapping[str, Any]) -> bool:
    if diagnostic.get("stage") != record.get("kind"):
        return False
    recorded = ((record.get("latest") or {}).get("evidence") or {}).get("code")
    replayed = (diagnostic.get("evidence") or {}).get("code")
    return recorded is None or recorded == replayed


def verdict(record: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """Fixed, still fails, fails differently, or could not replay: the replay against the record.

    The recorded diagnostic recurring is "still fails", whatever the outcome
    (a fallback incident recurs on a preparation that succeeds). Otherwise a
    clean preparation is "fixed", and any other refusal or error is new.
    """

    stages = [stage for stage in payload.get("stages") or () if isinstance(stage, Mapping)]
    if not stages or any(stage.get("stage") == "locate source" and stage.get("outcome") != "ok" for stage in stages):
        return NOT_REPLAYED
    if any(_same_failure(record, diagnostic) for diagnostic in _diagnostics(payload)):
        return STILL_FAILS
    return FIXED if payload.get("outcome") == "ok" else FAILS_DIFFERENTLY


def describe_replay(payload: Mapping[str, Any]) -> str:
    refusal = payload.get("refusal") or {}
    exception = payload.get("exception") or {}
    parts = [f"outcome {payload.get('outcome')}"]
    if refusal:
        parts.append(f"refusal {refusal.get('code')}: {refusal.get('detail') or refusal.get('message')}")
    if exception:
        parts.append(f"{exception.get('type')}: {exception.get('message')}")
    parts.append(f"scratch {payload.get('data_dir')}")
    return "; ".join(str(part) for part in parts)


# -- export -----------------------------------------------------------------------


def export_incident(
    record: Mapping[str, Any], data_dir: Path, destination: Path, *, redact: bool,
) -> list[Path]:
    """Write the record, and its retained bundle when there is one, as a fixture.

    The bundle is WG's retained copy, which never holds the CAD document. With
    ``redact`` the document name becomes ``redacted-model``, the STEP header's
    author fields are blanked (and the manifest's file hashes follow), the ids
    are dropped, and paths and the account name are rewritten.
    """

    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"{destination} must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)
    redactor = Redactor(redaction_rules(data_dir=data_dir, app_root=app_root()), user=account_name())
    exported = dict(record)
    if redact:
        exported["occurrences"] = [{"at": item.get("at")} for item in record.get("occurrences") or ()]
        latest = dict(record.get("latest") or {})
        latest["ids"] = {key: "<redacted>" for key in (latest.get("ids") or {})}
        exported["latest"] = latest
        exported["redacted"] = True
    written = [destination / "incident.json"]
    written[0].write_text(
        json.dumps(redactor.value(exported) if redact else exported, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ids = (record.get("latest") or {}).get("ids") or {}
    digest = str(ids.get("manifest_sha256") or "")
    if _DIGEST.fullmatch(digest) and (bundle := retained_bundle(data_dir, digest)).is_dir():
        target = destination / ("model.wgreturn" if redact else bundle.name)
        shutil.copytree(bundle, target, ignore=shutil.ignore_patterns("*.f3d"))
        if redact:
            _redact_bundle(target)
        written.append(target)
    return written


def _redact_bundle(bundle: Path) -> None:
    import hashlib

    manifest_path = bundle / "wgreturn.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(manifest.get("document"), dict):
        manifest["document"]["name"] = REDACTED_NAME
    for name, entry in (manifest.get("files") or {}).items():
        path = bundle / name
        if not path.is_file() or not name.lower().endswith((".step", ".stp")):
            continue
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        redacted = _STEP_FILE_NAME.sub(
            f"FILE_NAME('{REDACTED_NAME}','',(''),(''),'','','');", text, count=1,
        )
        if redacted != text:
            payload = redacted.encode("utf-8", errors="surrogateescape")
            path.write_bytes(payload)
            if isinstance(entry, dict):
                entry["sha256"] = "sha256:" + hashlib.sha256(payload).hexdigest()
                entry["size_bytes"] = len(payload)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# -- entry point --------------------------------------------------------------------


def main(
    argv: list[str] | None = None,
    *,
    replay_runner: Callable[[Sequence[str]], dict[str, Any]] = run_cad_replay,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m server.tools.cad_diag", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, help="WG data directory (default: this machine's)")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="Incidents, most recent first")
    listing.add_argument("--since", type=parse_since, help="Only those seen within, e.g. 2h or 1d")
    listing.add_argument("--kind", help="Only this kind, e.g. internal-failure")
    listing.add_argument("--json", action="store_true")
    show = commands.add_parser("show", help="One incident record in full")
    show.add_argument("id", help="Incident id or a unique prefix")
    replay = commands.add_parser("replay", help="Replay the incident's request in scratch")
    replay.add_argument("id")
    replay.add_argument("--json", action="store_true", help="Print the verdict and the replay report")
    export = commands.add_parser("export", help="Write the record (and retained bundle) to a folder")
    export.add_argument("id")
    export.add_argument("--out", type=Path, required=True, help="New or empty folder")
    export.add_argument("--redact", action="store_true",
                        help="Replace the model's name, blank STEP author fields, drop ids")
    args = parser.parse_args(argv)

    data_dir = (args.data_dir or resolve_data_dir()).expanduser().resolve()
    store = _store(data_dir)
    if args.command == "list":
        records = store.list(since=args.since, kind=args.kind)
        if args.json:
            print(json.dumps([summary(record) for record in records], indent=2))
        else:
            print("\n".join(list_lines(records)) if records else f"No incidents in {store.root}")
        return 0
    record = _require(store, args.id)
    if args.command == "show":
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0
    if args.command == "replay":
        payload = replay_runner(replay_arguments(record, data_dir))
        outcome = verdict(record, payload)
        if args.json:
            print(json.dumps({"incident": record.get("id"), "verdict": outcome, "replay": payload},
                             indent=2, default=str))
        else:
            print(f"{record.get('id')}: {outcome}")
            print(f"  replay: {describe_replay(payload)}")
        return EXIT_CODES[outcome]
    written = export_incident(record, data_dir, args.out.expanduser().resolve(), redact=args.redact)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
