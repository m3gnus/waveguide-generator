"""Read-only CAD Link evidence collector, for the release owner's clean-room checklist.

Given a WG application data directory -- the directory ``WG2_DATA_DIR`` names,
or the platform default ``server.platform.paths.resolve_data_dir`` resolves --
and an optional operation id, this writes one zip holding:

- ``fusion-status.json``, ``wg-capabilities.json`` and ``wg-endpoint.json``:
  the contents of ``.fusion-status.json``, ``wg-capabilities.json`` and the
  live session's ``wg-endpoint.json`` under the data
  directory's own ``ipc/wglink`` folder (``server/cadlink/fusion_status.py``,
  ``server/cadlink/fusion_delivery.py`` -- this is WG-internal signaling,
  rooted at the application data directory itself, not at the user-chosen
  CAD-link exchange folder ``CadWorkspaceState`` persists: every real caller
  of ``advertise_fusion_delivery``, ``publish_return_request`` and
  ``publish_fusion_handoff`` (``server/cadlink/api.py``,
  ``server/exports/api.py``) passes ``application.state.data_dir``, not the
  selected CAD-link path), with any object key naming a token, secret or
  password redacted, recursively.
- ``request-directories.json``: a listing -- name, size, modification time,
  never contents -- of the WGLink request directories, in that same
  ``ipc/wglink`` folder (``.fusion-return-requests``, ``.fusion-handoffs``).
- ``cad-operations.json``: the ``cad_operations`` rows from ``cadlink.db``,
  read through a read-only sqlite connection (``mode=ro``), filtered to the
  given operation id when one is given.
- ``server.log``: the application log lines naming the operation id, or the
  whole log when no id is given.
- ``manifest.json``: what ran, when, and which of the above was present, plus
  ``notes`` (see below).

Every read goes through ``Path.read_text``/``iterdir``/``stat`` or a sqlite
connection opened ``mode=ro``; nothing here creates, renames or deletes a file
under the data directory, and a missing input is reported in the manifest
rather than raised. This is a script, not a server route: it never imports
anything that starts an event loop or touches a live WG process, so it is
safe to run against a data directory belonging to a WG that is currently
running.

Since CL11b, WG selects a live HTTP heartbeat over the file one when a fresh
one exists (``server.cadlink.fusion_status.select_heartbeat``), but the live
heartbeat lives only in that WG process's in-memory ``LiveRegistry``
(``server/cadlink/live/registry.py``) -- it is never written to a file, and a
WG restart forgets it. This collector is read-only and runs out of process,
so it never calls a running WG over HTTP and can never observe which
transport was selected. Rather than guess or stay silent about that gap, both
``collect_evidence`` and the zip's ``manifest.json`` carry a ``notes`` list
that says so explicitly and points at the one place that does know: WG's own
``POST /api/cadlink/fusion-status`` response, whose ``heartbeatTransport``
field names ``"live"``, ``"file"``, or ``null``.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any
import zipfile

# ``ipc_folder`` (default ``create=False``) only joins a path -- it never
# touches the filesystem -- so importing it is safe for a read-only
# collector. The CAD-link exchange folder WG persists in
# ``cadlink_settings.json`` (``server/workspace/api.py``,
# ``CadWorkspaceState``) is a *different* directory (where a returned STEP
# bundle is written) and is reported here only as informational metadata,
# never used to locate the ipc/wglink signaling files above.
from server.cadlink.fusion_delivery import CAPABILITIES_FILENAME, HANDOFFS, RETURN_REQUESTS, ipc_folder
from server.cadlink.fusion_status import FUSION_STATUS_FILENAME
from server.cadlink.live.endpoint import ENDPOINT_FILENAME
from server.platform.paths import data_paths


SCHEMA_VERSION = 2

# Mirrors CadWorkspaceState.SETTINGS_NAME / SETTINGS_KEY
# (server/workspace/api.py) without importing that stateful class: reported
# as informational metadata only (see module docstring).
CADLINK_SETTINGS_FILENAME = "cadlink_settings.json"
CADLINK_SETTINGS_KEY = "cadLinkPath"

_REDACTED_KEY_TOKENS = ("token", "secret", "password")
_REDACTED_PLACEHOLDER = "*** redacted ***"

# Static, not derived from the data directory: this collector can never know
# the live-vs-file answer offline, regardless of what it finds on disk (see
# the module docstring). ``fusion-status.json`` above is always the file
# transport's payload; it has no ``heartbeatTransport`` field of its own --
# that field only exists on WG's own /fusion-status answer.
_LIVE_HEARTBEAT_TRANSPORT_NOTE = (
    "This collector is read-only and runs offline, out of process: it never "
    "calls a running WG over HTTP, so it cannot know whether the freshest "
    "heartbeat came by the live transport or the file one. The live "
    "heartbeat (CL11b) lives only in WG's in-process LiveRegistry and is "
    "never persisted to a file this collector can read; fusion-status.json "
    "above is always the file transport's payload. See WG's own answer to "
    "POST /api/cadlink/fusion-status -- its heartbeatTransport field names "
    "\"live\", \"file\", or null."
)

# A cap against a hand-placed or unrotated log; the application log itself is
# rotated well under this (server/platform/logging_setup.py).
_MAX_LOG_BYTES = 8 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact(value: Any) -> Any:
    """Blank every mapping value whose key names a credential, recursively.

    A key matches case-insensitively on substring, so ``apiToken``,
    ``client_secret`` and ``Password`` are all caught. Only mapping *values*
    are ever replaced; a list or a nested mapping under a matching key is
    replaced wholesale, since a token is never usefully half-redacted.
    """

    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and any(token in key.lower() for token in _REDACTED_KEY_TOKENS):
                redacted[key] = _REDACTED_PLACEHOLDER
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def cadlink_workspace_root(data_dir: Path) -> Path:
    """Where WG's CAD-link folder is, read the way WG persists it.

    Reads the same settings key ``CadWorkspaceState`` writes
    (``server/workspace/api.py``), without instantiating that class: no
    legacy-settings migration, no folder creation. Falls back to the same
    default path an unconfigured WG would use.
    """

    paths = data_paths(data_dir)
    settings_path = paths.root / CADLINK_SETTINGS_FILENAME
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = None
    raw_path = payload.get(CADLINK_SETTINGS_KEY) if isinstance(payload, Mapping) else None
    if isinstance(raw_path, str) and raw_path.strip():
        return Path(raw_path).expanduser()
    return paths.root / "cadlink"


def _read_json_member(path: Path) -> dict[str, Any]:
    """One evidence member from a JSON file: present, its path, its content."""

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {"path": str(path), "present": False, "content": None}
    try:
        content = json.loads(raw)
    except (ValueError, TypeError):
        return {"path": str(path), "present": True, "content": None, "error": "not valid JSON"}
    return {"path": str(path), "present": True, "content": redact(content)}


def collect_ipc_files(data_dir: Path) -> dict[str, Any]:
    """``.fusion-status.json``, ``wg-capabilities.json`` and ``wg-endpoint.json``, redacted.

    Both live under ``<data_dir>/ipc/wglink`` -- the application data
    directory, not the user-chosen CAD-link exchange folder (see the module
    docstring).
    """

    folder = ipc_folder(data_dir)
    return {
        "fusionStatus": _read_json_member(folder / FUSION_STATUS_FILENAME),
        "wgCapabilities": _read_json_member(folder / CAPABILITIES_FILENAME),
        # The live session endpoint; its ``registrationSecret`` is redacted.
        "wgEndpoint": _read_json_member(folder / ENDPOINT_FILENAME),
    }


def _directory_listing(directory: Path) -> dict[str, Any]:
    if not directory.is_dir():
        return {"path": str(directory), "present": False, "entries": None}
    entries = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        try:
            stat_result = entry.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": entry.name,
                "bytes": stat_result.st_size,
                "modifiedAt": datetime.fromtimestamp(
                    stat_result.st_mtime, tz=timezone.utc
                ).isoformat(timespec="seconds"),
            }
        )
    return {"path": str(directory), "present": True, "entries": entries}


def collect_request_directories(data_dir: Path) -> dict[str, Any]:
    """Names, sizes and modification times of the WGLink request folders.

    Never the contents of a request file: those are Fusion's evidence about
    a specific document, and this collector's job is to say what WG and the
    add-in exchanged, not to carry it. Same ``ipc/wglink`` folder as
    :func:`collect_ipc_files`.
    """

    folder = ipc_folder(data_dir)
    return {
        "returnRequests": _directory_listing(folder / RETURN_REQUESTS.directory),
        "handoffs": _directory_listing(folder / HANDOFFS.directory),
    }


def collect_operations(data_dir: Path, *, operation_id: str | None) -> dict[str, Any]:
    """``cad_operations`` rows, through a read-only sqlite connection.

    ``mode=ro`` in the URI refuses any write the connection might otherwise
    attempt -- including the journal/WAL files a writable connection would
    create -- so this never touches ``cadlink.db`` even when the caller can
    write to it.
    """

    db_path = data_paths(data_dir).db / "cadlink.db"
    if not db_path.exists():
        return {"path": str(db_path), "present": False, "rows": []}
    uri = f"file:{db_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.row_factory = sqlite3.Row
        if operation_id is None:
            cursor = connection.execute("SELECT * FROM cad_operations ORDER BY updated_at")
        else:
            cursor = connection.execute(
                "SELECT * FROM cad_operations WHERE operation_id = ? ORDER BY updated_at",
                (operation_id,),
            )
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()
    return {"path": str(db_path), "present": True, "rows": rows}


def collect_server_log(data_dir: Path, *, operation_id: str | None) -> dict[str, Any]:
    """Lines naming the operation id, or the whole (capped) log."""

    log_path = data_paths(data_dir).logs / "server.log"
    try:
        raw = log_path.read_bytes()
    except OSError:
        return {"path": str(log_path), "present": False, "text": None}
    if len(raw) > _MAX_LOG_BYTES:
        raw = raw[-_MAX_LOG_BYTES:]
    text = raw.decode("utf-8", errors="replace")
    if operation_id is not None:
        text = "\n".join(line for line in text.splitlines() if operation_id in line)
    return {"path": str(log_path), "present": True, "text": text}


def collect_evidence(data_dir: Path, *, operation_id: str | None = None) -> dict[str, Any]:
    """Everything this script reports, gathered without changing anything."""

    data_dir = Path(data_dir)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "createdAt": _now_iso(),
        "dataDir": str(data_dir),
        # Informational only -- the CAD-link exchange folder the user chose
        # (where a returned STEP bundle is written), which is a different
        # directory from ipc/wglink below. See the module docstring.
        "cadLinkExchangeFolder": str(cadlink_workspace_root(data_dir)),
        "operationId": operation_id,
        "ipc": collect_ipc_files(data_dir),
        "requestDirectories": collect_request_directories(data_dir),
        "operations": collect_operations(data_dir, operation_id=operation_id),
        "serverLog": collect_server_log(data_dir, operation_id=operation_id),
        # Caveats about this bundle that are true regardless of what was
        # found on disk. See the module docstring and
        # ``_LIVE_HEARTBEAT_TRANSPORT_NOTE``.
        "notes": [_LIVE_HEARTBEAT_TRANSPORT_NOTE],
    }


def _json_member(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")


def _member_presence(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    ipc = evidence["ipc"]
    directories = evidence["requestDirectories"]
    return [
        {"name": "fusion-status.json", "present": ipc["fusionStatus"]["present"]},
        {"name": "wg-capabilities.json", "present": ipc["wgCapabilities"]["present"]},
        {"name": "wg-endpoint.json", "present": ipc["wgEndpoint"]["present"]},
        {"name": "request-directories.json", "present": True},
        {
            "name": "return-requests",
            "present": directories["returnRequests"]["present"],
        },
        {"name": "handoffs", "present": directories["handoffs"]["present"]},
        {"name": "cad-operations.json", "present": evidence["operations"]["present"]},
        {"name": "server.log", "present": evidence["serverLog"]["present"]},
    ]


def write_zip(evidence: Mapping[str, Any], destination: Path) -> Path:
    """Write the evidence to ``destination`` as one zip. Never overwrites silently."""

    members: dict[str, bytes] = {
        "fusion-status.json": _json_member(evidence["ipc"]["fusionStatus"]),
        "wg-capabilities.json": _json_member(evidence["ipc"]["wgCapabilities"]),
        "wg-endpoint.json": _json_member(evidence["ipc"]["wgEndpoint"]),
        "request-directories.json": _json_member(evidence["requestDirectories"]),
        "cad-operations.json": _json_member(evidence["operations"]),
    }
    log = evidence["serverLog"]
    members["server.log"] = (log["text"] or "").encode("utf-8") if log["present"] else b""

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "createdAt": evidence["createdAt"],
        "dataDir": evidence["dataDir"],
        "cadLinkExchangeFolder": evidence["cadLinkExchangeFolder"],
        "operationId": evidence["operationId"],
        "members": _member_presence(evidence),
        # Carried up from the evidence dict so the release owner sees it in
        # manifest.json without unzipping every member (see module docstring).
        "notes": evidence["notes"],
    }
    members["manifest.json"] = _json_member(manifest)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, payload in sorted(members.items()):
            archive.writestr(name, payload)
    return destination


def default_output_name(operation_id: str | None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = f"-{operation_id}" if operation_id else ""
    return f"cadlink-evidence{suffix}-{stamp}.zip"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("data_dir", type=Path, help="WG application data directory (WG2_DATA_DIR)")
    parser.add_argument(
        "--operation-id",
        default=None,
        help="Only this operation's cad_operations row(s) and server.log lines",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Zip path to write (default: ./cadlink-evidence[-<id>]-<timestamp>.zip)",
    )
    args = parser.parse_args(argv)

    evidence = collect_evidence(args.data_dir, operation_id=args.operation_id)
    destination = args.output or Path(default_output_name(args.operation_id))
    write_zip(evidence, destination)
    print(f"Wrote {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "SCHEMA_VERSION",
    "cadlink_workspace_root",
    "collect_evidence",
    "collect_ipc_files",
    "collect_operations",
    "collect_request_directories",
    "collect_server_log",
    "default_output_name",
    "main",
    "redact",
    "write_zip",
]
