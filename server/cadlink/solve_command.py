"""A one-shot request from CAD to ingest one return and start a solve.

The intent is deliberately not part of ``wgreturn.json``: that manifest is
immutable geometry evidence, and WG re-reads returns whenever its coordinator
remounts or a listing revision arrives. A flag inside the evidence would be
re-observed and re-solved. A separate marker carrying its own command id can be
recorded as spent exactly once, which is what makes the automatic path safe.

The ledger records terminal outcomes only. A command whose gates block is not
terminal: it stays available so the user can acknowledge findings and run it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


SOLVE_REQUEST_FILENAME = ".wg-solve-request.json"
LEDGER_FILENAME = "solve-commands.json"
IPC_SUBDIRECTORY = Path("ipc") / "wglink"
# Enough history to make replay-after-restart impossible in practice without
# letting a machine-local file grow without bound.
LEDGER_LIMIT = 200


@dataclass(frozen=True)
class PendingSolveCommand:
    """A CAD-authored request to prepare and solve one exact return bundle."""

    marker_path: Path
    command_id: str
    return_id: str
    bundle_path: str
    manifest_sha256: str
    requested_at: str

    def payload(self) -> dict[str, Any]:
        return {
            "commandId": self.command_id,
            "returnId": self.return_id,
            "bundlePath": self.bundle_path,
            "manifestSha256": self.manifest_sha256,
            "requestedAt": self.requested_at,
        }


def ipc_folder(data_dir: Path, *, create: bool = False) -> Path:
    folder = data_dir.resolve() / IPC_SUBDIRECTORY
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write_atomically(target: Path, payload: Mapping[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f"{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def read_solve_command(data_dir: Path) -> PendingSolveCommand | None:
    """The pending command, or None when there is none or it is malformed."""

    marker = ipc_folder(data_dir) / SOLVE_REQUEST_FILENAME
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    if payload.get("schemaVersion") != 1 or payload.get("target") != "waveguide-generator":
        return None
    command_id = payload.get("commandId")
    bundle_path = payload.get("bundlePath")
    manifest_sha256 = payload.get("manifestSha256")
    if not all(isinstance(value, str) and value for value in (command_id, bundle_path, manifest_sha256)):
        return None
    return PendingSolveCommand(
        marker_path=marker,
        command_id=str(command_id),
        return_id=str(payload.get("returnId") or ""),
        bundle_path=str(bundle_path),
        manifest_sha256=str(manifest_sha256),
        requested_at=str(payload.get("requestedAt") or ""),
    )


def clear_solve_command(data_dir: Path, command_id: str) -> bool:
    """Remove the marker only when it still names this exact command."""

    current = read_solve_command(data_dir)
    if current is None or current.command_id != command_id:
        return False
    try:
        current.marker_path.unlink()
    except OSError:
        return False
    return True


def _ledger_path(data_dir: Path) -> Path:
    return ipc_folder(data_dir) / LEDGER_FILENAME


def read_ledger(data_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_ledger_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    entries = payload.get("commands") if isinstance(payload, Mapping) else None
    return dict(entries) if isinstance(entries, Mapping) else {}


def ledger_entry(data_dir: Path, command_id: str) -> dict[str, Any] | None:
    entry = read_ledger(data_dir).get(command_id)
    return dict(entry) if isinstance(entry, Mapping) else None


def record_outcome(
    data_dir: Path,
    command_id: str,
    *,
    state: str,
    job_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Record a terminal outcome for a command id.

    ``accepted`` means a job exists: re-processing that command must surface
    the same job rather than submit a second one. ``refused`` means the command
    can never succeed as written. A blocked command is deliberately not
    recorded, so the user can satisfy the gate and run it.
    """

    if state not in {"accepted", "refused"}:
        raise ValueError(f"Unknown solve-command outcome: {state!r}")
    entries = read_ledger(data_dir)
    entries[command_id] = {
        "state": state,
        "jobId": job_id,
        "reason": reason,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    if len(entries) > LEDGER_LIMIT:
        ordered = sorted(entries.items(), key=lambda item: str(item[1].get("at") or ""))
        entries = dict(ordered[-LEDGER_LIMIT:])
    _write_atomically(_ledger_path(data_dir), {"schemaVersion": 1, "commands": entries})
    return entries[command_id]
