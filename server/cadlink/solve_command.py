"""A one-shot request from CAD to ingest one return and start a solve.

The intent is deliberately not part of ``wgreturn.json``: that manifest is
immutable geometry evidence, and WG re-reads returns whenever its coordinator
remounts or a listing revision arrives. A flag inside the evidence would be
re-observed and re-solved. A separate request carrying its own command id can
be recorded as spent exactly once, which is what makes the automatic path safe.

Delivery. Fusion writes each command as its own file,
``.wg-solve-requests/<commandId>.json``, with ``schemaVersion`` 3 (delivery
version 3; docs/architecture/CAD-OPERATIONS.md). Each delivered file is claimed
by renaming it, read, persisted as a ``prepare_and_solve`` operation in the CAD
operation store (``cad_operations`` in ``cadlink.db``), and only then deleted.
What a WGLink older than version 3 writes -- the single slot
``.wg-solve-request.json``, or a version-2 file -- is refused visibly, with the
remedy: WG installs the add-in it ships, and Fusion has to load it. From then on the
store, not the file, is what WG hands out and records outcomes against. The
ledger helpers below are the store's view, in the shape these routes have
always returned. A command whose gates block is not terminal: it stays
available so the user can acknowledge findings and run it.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any, Callable, Mapping
import uuid

from .identity import utc_now
from .operations import (
    ACCEPTED,
    CLAIMABLE_STATES,
    PREPARE_AND_SOLVE,
    REJECTED,
    TERMINAL_STATES,
    prepare_and_solve_request,
    request_digest,
)

if TYPE_CHECKING:
    from .store import CadLinkStore


# What a WGLink older than delivery version 3 writes: the single slot, and
# version-2 files in the folder below. WG takes them only to refuse them.
SOLVE_REQUEST_FILENAME = ".wg-solve-request.json"
LEGACY_SCHEMA_VERSION = 1
OLDER_SCHEMA_VERSION = 2
_SLOT_SCHEMAS = frozenset({LEGACY_SCHEMA_VERSION})
_FILE_SCHEMAS = frozenset({OLDER_SCHEMA_VERSION, 3})
# One file per command, named <commandId>.json. A producer stages a file under
# a name starting with "." (or not ending in .json) and renames it into place.
SOLVE_REQUESTS_DIRECTORY = ".wg-solve-requests"
SCHEMA_VERSION = 3
OUTDATED_ADDIN_REASON = (
    "This solve request came from a WGLink add-in older than this Waveguide "
    "Generator, which it no longer accepts. Restart Fusion so it loads the WGLink "
    "that WG installed, then use Solve in WG again."
)
# The submission key the browser gives a solve command's job. A job created
# under it is that command's outcome, whatever request it carried.
CAD_SOLVE_SUBMISSION_PREFIX = "cad-solve:"
# A delivery is claimed by renaming it to this prefix in its own folder before
# it is read, so a producer writing the same path afterwards writes a new file
# rather than one the consumer is about to delete.
CLAIM_PREFIX = ".wg-solve-claim-"
# The JSON outcome ledger of earlier versions. The store imports it once and
# renames it; nothing reads it after that.
LEDGER_FILENAME = "solve-commands.json"
IPC_SUBDIRECTORY = Path("ipc") / "wglink"
# How many recent outcomes ``read_ledger`` returns.
LEDGER_LIMIT = 200
# How many unfinished operations one look for the oldest may skip over.
_PENDING_SCAN = 50

_LEDGER_STATES = {"accepted": ACCEPTED, "refused": REJECTED}

logger = logging.getLogger(__name__)
# One consumer per process at a time. Files are still claimed by rename,
# because the producer writing them is another process.
_DELIVERY_LOCK = threading.Lock()


@dataclass(frozen=True)
class PendingSolveCommand:
    """A CAD-authored request to prepare and solve one exact return bundle.

    ``marker_path`` is the file it was read from, or None once it is rebuilt
    from the operation store.
    """

    marker_path: Path | None
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


def _command_from_payload(
    payload: object, path: Path, *, schema_versions: frozenset[int]
) -> PendingSolveCommand | None:
    if not isinstance(payload, Mapping):
        return None
    if (
        payload.get("schemaVersion") not in schema_versions
        or payload.get("target") != "waveguide-generator"
    ):
        return None
    command_id = payload.get("commandId")
    bundle_path = payload.get("bundlePath")
    manifest_sha256 = payload.get("manifestSha256")
    if not all(isinstance(value, str) and value for value in (command_id, bundle_path, manifest_sha256)):
        return None
    # A request may name its operation, and then it must be the command id:
    # otherwise WG cannot tell which identity the producer meant.
    if payload.get("operationId", command_id) != command_id:
        return None
    return PendingSolveCommand(
        marker_path=path,
        command_id=str(command_id),
        return_id=str(payload.get("returnId") or ""),
        bundle_path=str(bundle_path),
        manifest_sha256=str(manifest_sha256),
        requested_at=str(payload.get("requestedAt") or ""),
    )


def _read_payload(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


@dataclass(frozen=True)
class _Delivery:
    path: Path
    claimed: bool
    schema_versions: frozenset[int]
    command: PendingSolveCommand
    age: tuple[str, int, str]


def _files(directory: Path) -> list[Path]:
    try:
        return [path for path in directory.iterdir() if path.is_file()]
    except OSError:
        return []


def _delivery(
    path: Path, *, claimed: bool, schema_versions: frozenset[int]
) -> _Delivery | None:
    command = _command_from_payload(_read_payload(path), path, schema_versions=schema_versions)
    if command is None:
        return None
    try:
        modified = path.stat().st_mtime_ns
    except OSError:
        return None
    return _Delivery(
        path, claimed, schema_versions, command, (command.requested_at, modified, path.name)
    )


def _deliveries(data_dir: Path) -> list[_Delivery]:
    """Every solve command waiting on disk, oldest first.

    That is the per-command files, what an older WGLink wrote (taken only to be
    refused), and any claim an interrupted poll left behind. A file WG cannot
    read as a solve command -- malformed, a newer schema, or a producer's
    staging file -- is left where it is. Age is the requested time, then the
    file's modification time.
    """

    folder = ipc_folder(data_dir)
    requests = folder / SOLVE_REQUESTS_DIRECTORY
    found: list[_Delivery | None] = []
    for directory, versions in ((folder, _SLOT_SCHEMAS), (requests, _FILE_SCHEMAS)):
        for path in _files(directory):
            if path.name.startswith(CLAIM_PREFIX) and path.suffix == ".json":
                found.append(_delivery(path, claimed=True, schema_versions=versions))
    found.append(
        _delivery(folder / SOLVE_REQUEST_FILENAME, claimed=False, schema_versions=_SLOT_SCHEMAS)
    )
    for path in _files(requests):
        if not path.name.startswith(".") and path.suffix == ".json":
            found.append(_delivery(path, claimed=False, schema_versions=_FILE_SCHEMAS))
    return sorted((item for item in found if item is not None), key=lambda item: item.age)


def _claim(path: Path) -> Path | None:
    """Take a delivery by renaming it; None means try again on the next poll.

    The rename fails when the file is already gone (another consumer took it)
    or, on Windows, while its writer still holds it open.
    """

    claim = path.with_name(f"{CLAIM_PREFIX}{uuid.uuid4().hex}.json")
    try:
        os.rename(path, claim)
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.debug("Solve command %s is not claimable yet: %s", path.name, exc)
        return None
    return claim


def _acknowledge(path: Path) -> bool:
    """Delete a consumed delivery. False leaves it for the next poll to recover."""

    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning("Could not delete the consumed solve command %s: %s", path.name, exc)
        return False
    return True


def solve_command_request(command: PendingSolveCommand) -> tuple[dict[str, Any], dict[str, Any]]:
    """The operation target and inputs a solve command names.

    ``requestedAt`` and the file's location are transport, not identity.
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


def _persist(store: CadLinkStore, command: PendingSolveCommand) -> dict[str, Any] | None:
    """Accept a delivered command, or recover the operation it repeats.

    Returns the answer this delivery is owed on its own, or None when the
    operation it names waits to be handed out: the outcome that already stands,
    or a refusal when its id already names a different request that is finished
    or is not a solve.
    """

    target, inputs = solve_command_request(command)
    digest = request_digest(PREPARE_AND_SOLVE, target, inputs)
    row, result = store.accept_operation(
        command.command_id, PREPARE_AND_SOLVE, digest, target, inputs
    )
    refusal = _delivery_conflict(row, digest) if result == "conflict" else None
    if refusal is not None:
        # The stored operation is untouched and its result is not this
        # delivery's answer. The refusal is logged whatever is answered.
        logger.warning("Refused solve command %r: %s", command.command_id, refusal["reason"])
        if row["kind"] == PREPARE_AND_SOLVE and row["state"] not in TERMINAL_STATES:
            # A client takes an answer under a command id as that command's
            # end. While the operation holding the id is an unfinished solve,
            # a refusal answer would strand it; it stays the one handed out.
            return None
        return refusal
    if row["state"] in TERMINAL_STATES:
        return _entry(row)
    return None


def _refuse_outdated(store: CadLinkStore, command: PendingSolveCommand) -> dict[str, Any]:
    """Refuse a command an older WGLink wrote, with the remedy, as its outcome."""

    logger.warning(
        "Refused solve command %r: it came from a WGLink older than delivery version %d.",
        command.command_id,
        SCHEMA_VERSION,
    )
    try:
        return record_outcome(
            store, command.command_id, state="refused", reason=OUTDATED_ADDIN_REASON,
            command=command,
        )
    except SolveOutcomeConflict as exc:
        return exc.existing


def collect_solve_deliveries(
    data_dir: Path,
    store: CadLinkStore,
    *,
    retain: Callable[[str], object] | None = None,
) -> dict[str, Any] | None:
    """Move delivered solve commands into the operation store, oldest first.

    Each file is claimed by rename, read, persisted and only then deleted. A
    newer command written to the same slot meanwhile therefore survives, and a
    poll interrupted after a claim leaves the claim for the next poll to finish.
    ``retain`` keeps the snapshot an operation names in WG's own storage before
    its delivery is acknowledged (CAD-OPERATIONS.md, "Preparation").

    Returns the answer owed to one delivery on its own -- the replay of an
    outcome that already stands, or a refusal of a different request under an
    id whose operation is finished or is not a solve -- in the solve-command
    response shape, or None. It stops
    at that answer, so later files wait for the next poll instead of losing
    theirs.
    """

    with _DELIVERY_LOCK:
        for delivery in _deliveries(data_dir):
            claim = delivery.path if delivery.claimed else _claim(delivery.path)
            if claim is None:
                continue
            # What the rename took is the request, not what was read before.
            payload = _read_payload(claim)
            command = _command_from_payload(
                payload, claim, schema_versions=delivery.schema_versions
            )
            if command is None:
                continue
            outdated = isinstance(payload, Mapping) and payload.get("schemaVersion") != SCHEMA_VERSION
            if outdated and store.get_operation(command.command_id) is None:
                # An older add-in's command WG has never seen: refused, with
                # the remedy. One the store already holds is a repeat delivery
                # and is recovered or refused as any other.
                answer = _refuse_outdated(store, command)
            else:
                answer = _persist(store, command)
                if retain is not None:
                    retain(command.command_id)
            # A delete that fails leaves the claim for the next poll, which
            # recovers the same operation and answers it then.
            if _acknowledge(claim) and answer is not None:
                return {"command": command.payload(), "outcome": answer}
    return None


def oldest_pending_solve_command(store: CadLinkStore) -> PendingSolveCommand | None:
    """The oldest accepted solve command that has no outcome yet.

    Solve requests stay separate and wait in acceptance order: a later request
    never takes an earlier one's place. The command is rebuilt from the
    operation's stored inputs. Its ``requestedAt`` is when WG accepted it,
    because the producer's timestamp is transport and is not stored.
    """

    rows = store.list_operations(
        kind=PREPARE_AND_SOLVE, states=CLAIMABLE_STATES, oldest_first=True, limit=_PENDING_SCAN
    )
    for row in rows:
        if row["legacy"] or not row["inputs_json"]:
            continue
        inputs = json.loads(row["inputs_json"])
        return PendingSolveCommand(
            marker_path=None,
            command_id=str(row["operation_id"]),
            return_id=str(inputs["return_id"]),
            bundle_path=str(inputs["bundle_path"]),
            manifest_sha256=str(inputs["manifest_sha256"]),
            requested_at=str(row["created_at"]),
        )
    return None


def delivered_command(data_dir: Path, command_id: str) -> PendingSolveCommand | None:
    """The oldest request on disk under this command id, left where it is."""

    for delivery in _deliveries(data_dir):
        if delivery.command.command_id == command_id:
            return delivery.command
    return None


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
