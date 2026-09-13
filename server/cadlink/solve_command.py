"""A one-shot request from CAD to ingest one return and start a solve.

The intent is deliberately not part of ``wgreturn.json``: that manifest is
immutable geometry evidence, and WG re-reads returns whenever its coordinator
remounts or a listing revision arrives. A flag inside the evidence would be
re-observed and re-solved. A separate marker carrying its own command id can be
recorded as spent exactly once, which is what makes the automatic path safe.

Terminal outcomes live in the CAD operation store: one ``prepare_and_solve``
operation per command id (``cad_operations`` in ``cadlink.db``). The ledger
helpers below are that store's view, in the shape these routes have always
returned. A command whose gates block is not terminal: it stays available so
the user can acknowledge findings and run it.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .identity import utc_now
from .operations import (
    ACCEPTED,
    PREPARE_AND_SOLVE,
    REJECTED,
    TERMINAL_STATES,
    prepare_and_solve_request,
    request_digest,
)

if TYPE_CHECKING:
    from .store import CadLinkStore


SOLVE_REQUEST_FILENAME = ".wg-solve-request.json"
# The JSON outcome ledger of earlier versions. The store imports it once and
# renames it; nothing reads it after that.
LEDGER_FILENAME = "solve-commands.json"
IPC_SUBDIRECTORY = Path("ipc") / "wglink"
# How many recent outcomes ``read_ledger`` returns.
LEDGER_LIMIT = 200

_LEDGER_STATES = {"accepted": ACCEPTED, "refused": REJECTED}


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


class SolveOutcomeConflict(ValueError):
    """A different terminal outcome already stands for this command id.

    ``existing`` is the outcome that stands, in ledger shape. The first
    terminal outcome is never overwritten.
    """

    def __init__(self, command_id: str, existing: dict[str, Any]) -> None:
        self.command_id = command_id
        self.existing = existing
        super().__init__(f"solve command {command_id!r} already has a different outcome")


def ipc_folder(data_dir: Path, *, create: bool = False) -> Path:
    folder = data_dir.resolve() / IPC_SUBDIRECTORY
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def legacy_ledger_path(data_dir: Path) -> Path:
    """Where earlier versions kept terminal solve-command outcomes."""

    return ipc_folder(Path(data_dir)) / LEDGER_FILENAME


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


def solve_command_request(command: PendingSolveCommand) -> tuple[dict[str, Any], dict[str, Any]]:
    """The operation target and inputs a solve command names.

    ``requestedAt`` and the marker's location are transport, not identity.
    """

    return prepare_and_solve_request(
        return_id=command.return_id,
        bundle_path=command.bundle_path,
        manifest_sha256=command.manifest_sha256,
    )


def _entry(row: Mapping[str, Any]) -> dict[str, Any]:
    outcome = json.loads(row["outcome_json"]) if row.get("outcome_json") else {}
    message = outcome.get("message") if isinstance(outcome, Mapping) else None
    return {
        "state": "accepted" if row["state"] == ACCEPTED else "refused",
        "jobId": row["job_id"],
        "reason": message if message is not None else row["reason"],
        "at": row["updated_at"],
    }


def _refusal(reason: str) -> dict[str, Any]:
    """A refusal of one delivery, in ledger shape. Nothing is stored for it."""

    return {"state": "refused", "jobId": None, "reason": reason, "at": utc_now()}


def _delivery_conflict(row: Mapping[str, Any], digest: str | None) -> dict[str, Any] | None:
    """A refusal when a delivery under this row's id is a different request."""

    if row["kind"] != PREPARE_AND_SOLVE:
        return _refusal("This command id already names a different CAD operation.")
    # A legacy row has no digest, so it is matched by id alone, as before.
    if digest is not None and not row["legacy"] and row["request_digest"] != digest:
        return _refusal(
            "This command id was already used for a different request. "
            "Send it again from Fusion."
        )
    return None


def conflicting_delivery(
    store: CadLinkStore, command: PendingSolveCommand
) -> dict[str, Any] | None:
    """Refuse a delivered command whose id already names a different request.

    Returns the refusal to answer the delivery with, or None when the id is
    free or names this same request. The stored operation is never touched,
    and its own outcome is never the answer to a different request.
    """

    row = store.get_operation(command.command_id)
    if row is None:
        return None
    target, inputs = solve_command_request(command)
    return _delivery_conflict(row, request_digest(PREPARE_AND_SOLVE, target, inputs))


def _is_terminal_solve(row: Mapping[str, Any] | None) -> bool:
    return (
        row is not None
        and row["kind"] == PREPARE_AND_SOLVE
        and row["state"] in TERMINAL_STATES
    )


def read_ledger(store: CadLinkStore) -> dict[str, Any]:
    """The most recent terminal solve-command outcomes, newest first."""

    rows = store.list_operations(
        kind=PREPARE_AND_SOLVE, states=TERMINAL_STATES, limit=LEDGER_LIMIT
    )
    return {str(row["operation_id"]): _entry(row) for row in rows}


def ledger_entry(store: CadLinkStore, command_id: str) -> dict[str, Any] | None:
    row = store.get_operation(command_id)
    return _entry(row) if _is_terminal_solve(row) else None


def record_outcome(
    store: CadLinkStore,
    command_id: str,
    *,
    state: str,
    job_id: str | None = None,
    reason: str | None = None,
    command: PendingSolveCommand | None = None,
) -> dict[str, Any]:
    """Record a terminal outcome for a command id, and return the one that stands.

    ``accepted`` means a job exists: re-processing that command must surface
    the same job rather than submit a second one. ``refused`` means the command
    can never succeed as written. A blocked command is deliberately not
    recorded, so the user can satisfy the gate and run it.

    The first terminal outcome stands. Recording the same outcome again returns
    it unchanged; a different one raises :class:`SolveOutcomeConflict` and
    writes nothing. ``command`` is the request as delivered, when WG still
    holds it: the operation then keeps its request identity. Without it the
    outcome is kept as a legacy row, because its request is unknown.
    """

    if state not in _LEDGER_STATES:
        raise ValueError(f"Unknown solve-command outcome: {state!r}")
    stored_state = _LEDGER_STATES[state]
    message = reason or None
    outcome = {"message": message} if message else None
    requested = (state, job_id, message)
    delivered: tuple[dict[str, Any], dict[str, Any], str] | None = None
    if command is not None and command.command_id == command_id:
        target, inputs = solve_command_request(command)
        delivered = (target, inputs, request_digest(PREPARE_AND_SOLVE, target, inputs))
    # Each pass acts on a committed row; a pass loses only to a concurrent
    # writer, and the next pass then sees what that writer committed.
    for _attempt in range(3):
        row = store.get_operation(command_id)
        if row is None:
            if delivered is not None:
                target, inputs, digest = delivered
                row, _result = store.accept_operation(
                    command_id, PREPARE_AND_SOLVE, digest, target, inputs
                )
            else:
                row, _created = store.record_legacy_outcome(
                    command_id,
                    kind=PREPARE_AND_SOLVE,
                    state=stored_state,
                    job_id=job_id,
                    outcome=outcome,
                )
        # Same id, different request or kind: refuse this report and leave the
        # operation that holds the id exactly as it is.
        refusal = _delivery_conflict(row, delivered[2] if delivered is not None else None)
        if refusal is not None:
            raise SolveOutcomeConflict(command_id, refusal)
        if row["state"] in TERMINAL_STATES:
            existing = _entry(row)
            if (existing["state"], existing["jobId"], existing["reason"]) == requested:
                return existing
            raise SolveOutcomeConflict(command_id, existing)
        recorded = store.record_outcome(
            command_id,
            int(row["attempt_generation"]),
            stored_state,
            job_id=job_id,
            outcome=outcome,
        )
        if recorded is not None:
            return _entry(recorded)
    raise RuntimeError(
        f"solve command {command_id!r} kept changing while its outcome was recorded"
    )
