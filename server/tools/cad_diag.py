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


class NotReplayable(Exception):
    """The replay cannot start: no record, no request, no bundle, or no report."""


def _store(data_dir: Path) -> IncidentStore:
    return IncidentStore(data_dir)


def _require(store: IncidentStore, incident_id: str) -> dict[str, Any]:
    record = store.get(incident_id)
    if record is None:
        raise NotReplayable(f"No incident {incident_id!r} (or the prefix is ambiguous) in {store.root}")
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
    raise NotReplayable(
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
        raise NotReplayable(
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

#: What an identifying value becomes in a redacted export.
REDACTED = "<redacted>"
#: Keys whose values name a run, a request, a design, a document or a return.
IDENTIFYING_KEYS = frozenset({
    "job_id", "jobId", "operation_id", "operationId", "ingest_id", "ingestId",
    "preparation_id", "request_id", "run_number", "manifest_sha256", "manifestSha256",
    "snapshot", "snapshotSha256", "design_id", "designId", "design_ids", "designIds",
    "export_id", "lineage_id", "bundle_id", "origin_bundle_id", "native_id",
    "document_native_id", "return_id", "project_id",
})
#: Keys whose values are a model's or a body's name.
NAME_KEYS = frozenset({"document_name", "documentName", "model_name", "modelName"})
#: Manifest values that are protocol vocabulary, never a user's words: a body
#: named like one must not turn the manifest invalid.
_MANIFEST_VOCABULARY = frozenset({
    "adapter", "adapter_version", "body_kind", "build_mode", "cad_app", "cad_version",
    "chirality", "external_reference", "formula", "handedness", "kind", "length_unit",
    "local_body_state", "matrix_convention", "media_type", "patch_policy", "purpose",
    "reason", "required_features", "role", "selection", "severity", "status",
    "wgreturn_version", "appearance_labels", "sha256", "signature_hash", "design_hash",
    "geometry_hash", "instance_id", "wglink_instance_id", "solver_anchor_instance_id",
    "default_drive_channel_id", "id", "created_at", "observed_at",
})
#: Manifest identifiers: replaced by stable stand-ins (one per original value).
_MANIFEST_IDS = ("design_id", "export_id", "lineage_id")
_MIN_NAME = 3
_STEP_STRING = re.compile(r"'((?:[^']|'')*)'")
_STEP_PRODUCT = re.compile(r"\bPRODUCT\s*\(\s*'((?:[^']|'')*)'\s*,\s*'((?:[^']|'')*)'", re.IGNORECASE)
_STEP_ESCAPE = re.compile(
    r"\\X2\\((?:[0-9A-F]{4})+)\\X0\\|\\X4\\((?:[0-9A-F]{8})+)\\X0\\|\\X\\([0-9A-F]{2})|\\\\|''",
    re.IGNORECASE,
)


def _step_decode(raw: str) -> str:
    """A STEP string literal's contents as text (ISO 10303-21 escapes)."""

    def one(match: re.Match[str]) -> str:
        if match.group(1):
            hexed = match.group(1)
            return "".join(chr(int(hexed[index:index + 4], 16)) for index in range(0, len(hexed), 4))
        if match.group(2):
            hexed = match.group(2)
            return "".join(chr(int(hexed[index:index + 8], 16)) for index in range(0, len(hexed), 8))
        if match.group(3):
            return bytes([int(match.group(3), 16)]).decode("latin-1")
        return match.group(0)[0]

    return _STEP_ESCAPE.sub(one, raw)


def _step_encode(text: str) -> str:
    """Text as a STEP string literal's contents: ASCII, quotes doubled, the rest escaped."""

    parts = []
    for character in text:
        code = ord(character)
        if character == "'":
            parts.append("''")
        elif character == "\\":
            parts.append("\\\\")
        elif 32 <= code < 127:
            parts.append(character)
        elif code <= 0xFFFF:
            parts.append(f"\\X2\\{code:04X}\\X0\\")
        else:
            parts.append(f"\\X4\\{code:08X}\\X0\\")
    return "".join(parts)


class ExportRedactor:
    """Every name and id an exported incident carries, replaced consistently.

    Names (the document's, the components', bodies' and occurrences') become
    ``redacted-model`` and ``name-<n>``; ids become ``<redacted>``. The same
    original always becomes the same stand-in, in the record, the logs, the
    manifest and the STEP file alike, so the fixture still replays.
    """

    def __init__(self, base: Redactor) -> None:
        self._base = base
        self._names: dict[str, str] = {}
        self._ids: set[str] = set()
        self._pattern: re.Pattern[str] | None = None

    def add_name(self, value: Any, placeholder: str | None = None) -> None:
        name = str(value or "").strip()
        if len(name) < _MIN_NAME or name.lower() in self._names or name == REDACTED_NAME:
            return
        self._names[name.lower()] = placeholder or f"name-{len(self._names) + 1}"
        self._pattern = None

    def add_id(self, value: Any) -> None:
        text = str(value or "").strip()
        for spelling in {text, text.removeprefix("sha256:")}:
            if len(spelling) >= 4 and spelling != REDACTED:
                self._ids.add(spelling)
                self._pattern = None

    def names(self) -> list[str]:
        return list(self._names)

    def _compiled(self) -> re.Pattern[str] | None:
        if self._pattern is None:
            terms = sorted({*self._names, *(item.lower() for item in self._ids)}, key=len, reverse=True)
            if not terms:
                return None
            self._pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
        return self._pattern

    def text(self, value: str, *, names: bool = True) -> str:
        """Paths, the account name and ids replaced; and the names, unless ``names`` is false."""

        value = self._base.text(value)
        pattern = self._compiled()
        if pattern is None:
            return value
        lowered_ids = {item.lower() for item in self._ids}

        def replace(match: re.Match[str]) -> str:
            found = match.group(0).lower()
            if found in lowered_ids:
                return REDACTED
            return self._names[found] if names else match.group(0)

        return pattern.sub(replace, value)

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            # A key is vocabulary ("code", "detail"), never a name: a body
            # called like one must not rename the record's fields.
            return {
                self.text(str(key), names=False): (
                    REDACTED if str(key) in IDENTIFYING_KEYS and item not in (None, "", [], {})
                    else self.value(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        return value

    # -- what to look for ------------------------------------------------------

    def collect_record(self, value: Any) -> None:
        """The ids and names a record holds, wherever they are."""

        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key) in IDENTIFYING_KEYS:
                    for each in item if isinstance(item, (list, tuple)) else (item,):
                        if isinstance(each, (str, int)) and not isinstance(each, bool):
                            self.add_id(each)
                elif str(key) in NAME_KEYS:
                    self.add_name(item, None if self._names else REDACTED_NAME)
                self.collect_record(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.collect_record(item)

    def collect_manifest(self, manifest: Mapping[str, Any]) -> None:
        document = manifest.get("document") if isinstance(manifest.get("document"), Mapping) else {}
        self.add_name(document.get("name"), REDACTED_NAME)
        self.add_id(document.get("native_id"))
        self.add_id((manifest.get("return") or {}).get("id") if isinstance(manifest.get("return"), Mapping) else None)
        for instance in _mappings(manifest.get("instances")):
            for key in (*_MANIFEST_IDS, "origin_bundle_id"):
                self.add_id(instance.get(key))
            for part in re.split(r"[/\\|]+", str(instance.get("occurrence_path") or "")):
                # ``Name:1`` is occurrence 1 of ``Name``: the name is what is replaced.
                self.add_name(re.sub(r":\d+$", "", part))
            prefix = str(instance.get("parameter_prefix") or "")
            self.add_name(re.sub(r"^wg_|_$", "", prefix))
        scope = manifest.get("scope") if isinstance(manifest.get("scope"), Mapping) else {}
        for group in ("included", "skipped", "fem_air_volumes"):
            for entry in _mappings(scope.get(group)):
                for key in ("name", "component", "object_id"):
                    self.add_name(entry.get(key))
        for source in _mappings(manifest.get("sources")):
            for body in (source.get("observed") or {}).get("bodies") or ():
                self.add_name(body)

    def collect_step(self, text: str) -> None:
        for match in _STEP_PRODUCT.finditer(text):
            for raw in match.groups():
                self.add_name(_step_decode(raw))

    # -- rewriting -------------------------------------------------------------

    def step(self, text: str) -> str:
        """Every string literal with its names and ids replaced; the file stays valid STEP."""

        text = _STEP_FILE_NAME.sub(f"FILE_NAME('{REDACTED_NAME}','',(''),(''),'','','');", text, count=1)

        def literal(match: re.Match[str]) -> str:
            decoded = _step_decode(match.group(1))
            redacted = self.text(decoded)
            return match.group(0) if redacted == decoded else f"'{_step_encode(redacted)}'"

        return _STEP_STRING.sub(literal, text)

    def manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """The manifest with no document or origin-bundle id, stand-in names and ids (valid ULIDs)."""

        stand_ins: dict[str, str] = {}

        def stand_in(value: Any) -> Any:
            if not isinstance(value, str) or not value:
                return value
            # ``wgr_`` plus 26 ULID characters, as the protocol requires: an
            # all-digit ULID that names nothing.
            prefix = value.split("_", 1)[0] if "_" in value else "id"
            return stand_ins.setdefault(value, f"{prefix}_{len(stand_ins) + 1:026d}")

        if isinstance(manifest.get("document"), dict):
            manifest["document"]["name"] = REDACTED_NAME
            if "native_id" in manifest["document"]:
                manifest["document"]["native_id"] = None
        if isinstance(manifest.get("return"), dict) and "id" in manifest["return"]:
            manifest["return"]["id"] = stand_in(manifest["return"]["id"])
        for instance in _mappings(manifest.get("instances")):
            instance.pop("origin_bundle_id", None)
            for key in _MANIFEST_IDS:
                if key in instance:
                    instance[key] = stand_in(instance[key])
        return self._manifest_names(manifest)

    def _manifest_names(self, value: Any, key: str | None = None) -> Any:
        if key in _MANIFEST_VOCABULARY:
            return value
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {name: self._manifest_names(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [self._manifest_names(item, key) for item in value]
        return value


def _mappings(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def export_incident(
    record: Mapping[str, Any], data_dir: Path, destination: Path, *, redact: bool,
) -> list[Path]:
    """Write the record, and its retained bundle when there is one, as a fixture.

    The bundle is WG's retained copy, which never holds the CAD document. With
    ``redact`` every name the record and the bundle hold (the document's, the
    components', bodies' and occurrences', the STEP products') becomes
    ``redacted-model`` or ``name-<n>``, every id ``<redacted>`` (the manifest's
    by stand-ins), the STEP header's author fields are blanked (the manifest's
    file hashes follow), the job logs are keyed ``job-<n>``, and paths and the
    account name are rewritten.
    """

    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"{destination} must be new or empty")
    ids = (record.get("latest") or {}).get("ids") or {}
    digest = str(ids.get("manifest_sha256") or "")
    bundle = retained_bundle(data_dir, digest) if _DIGEST.fullmatch(digest) else None
    if bundle is not None and not bundle.is_dir():
        bundle = None
    destination.mkdir(parents=True, exist_ok=True)
    written = [destination / "incident.json"]
    if not redact:
        written[0].write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if bundle is not None:
            target = destination / bundle.name
            shutil.copytree(bundle, target, ignore=shutil.ignore_patterns("*.f3d"))
            written.append(target)
        return written

    redactor = ExportRedactor(Redactor(redaction_rules(data_dir=data_dir, app_root=app_root()), user=account_name()))
    manifest: dict[str, Any] | None = None
    if bundle is not None:
        # The manifest first: its document name is the one ``redacted-model`` stands for.
        manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
        redactor.collect_manifest(manifest)
        for name in manifest.get("files") or {}:
            path = bundle / name
            if name.lower().endswith((".step", ".stp")) and path.is_file():
                redactor.collect_step(path.read_text(encoding="utf-8", errors="surrogateescape"))
    redactor.collect_record(record)
    for job_id in (record.get("jobLogs") or {}):
        redactor.add_id(job_id)
    exported = dict(record)
    exported["occurrences"] = [{"at": item.get("at")} for item in record.get("occurrences") or ()]
    exported["jobLogs"] = {
        f"job-{index}": log for index, log in enumerate((record.get("jobLogs") or {}).values(), start=1)
    }
    exported["redacted"] = True
    written[0].write_text(json.dumps(redactor.value(exported), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if bundle is not None and manifest is not None:
        target = destination / "model.wgreturn"
        _write_redacted_bundle(bundle, manifest, target, redactor)
        written.append(target)
    return written


def _write_redacted_bundle(bundle: Path, manifest: dict[str, Any], target: Path, redactor: ExportRedactor) -> None:
    """Only the manifest and the members it lists; names and ids replaced, hashes updated."""

    import hashlib

    target.mkdir()
    members: dict[str, Any] = {}
    renamed: dict[str, str] = {}
    for name, entry in (manifest.get("files") or {}).items():
        source = bundle / name
        if not source.is_file() or source.suffix.lower() == ".f3d":
            continue
        new_name = redactor.text(name)
        renamed[name] = new_name
        destination = target / new_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if name.lower().endswith((".step", ".stp")):
            payload = redactor.step(source.read_text(encoding="utf-8", errors="surrogateescape")).encode(
                "utf-8", errors="surrogateescape",
            )
        else:
            payload = source.read_bytes()
        destination.write_bytes(payload)
        entry = dict(entry) if isinstance(entry, Mapping) else entry
        if isinstance(entry, dict):
            entry["sha256"] = "sha256:" + hashlib.sha256(payload).hexdigest()
            entry["size_bytes"] = len(payload)
        members[new_name] = entry
    manifest["files"] = members
    manifest = redactor.manifest(manifest)
    manifest["files"] = members  # member names are already rewritten, and hashed
    for section in ("assembly",):
        if isinstance(manifest.get(section), dict) and manifest[section].get("file") in renamed:
            manifest[section]["file"] = renamed[manifest[section]["file"]]
    (target / "wgreturn.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
                        help="Replace every name and id (record, logs, manifest, STEP); blank STEP author fields")
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
    if args.command == "replay":
        # "Could not replay" is its own outcome with its own exit code, never
        # the "still fails" a bare SystemExit(message) would read as.
        try:
            record = _require(store, args.id)
            payload = replay_runner(replay_arguments(record, data_dir))
        except NotReplayable as exc:
            print(f"{args.id}: {NOT_REPLAYED}\n  {exc}", file=sys.stderr)
            if args.json:
                print(json.dumps({"incident": args.id, "verdict": NOT_REPLAYED, "reason": str(exc)}, indent=2))
            return EXIT_CODES[NOT_REPLAYED]
        outcome = verdict(record, payload)
        if args.json:
            print(json.dumps({"incident": record.get("id"), "verdict": outcome, "replay": payload},
                             indent=2, default=str))
        else:
            print(f"{record.get('id')}: {outcome}")
            print(f"  replay: {describe_replay(payload)}")
        return EXIT_CODES[outcome]
    try:
        record = _require(store, args.id)
    except NotReplayable as exc:
        raise SystemExit(str(exc)) from exc
    if args.command == "show":
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0
    written = export_incident(record, data_dir, args.out.expanduser().resolve(), redact=args.redact)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
