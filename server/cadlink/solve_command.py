"""A one-shot request from CAD to ingest one return and start a solve.

The intent is deliberately not part of ``wgreturn.json``: that manifest is
immutable geometry evidence, and WG re-reads returns whenever its coordinator
remounts or a listing revision arrives. A flag inside the evidence would be
re-observed and re-solved. A separate request carrying its own command id can
be recorded as spent exactly once, which is what makes the automatic path safe.

Delivery. Fusion writes each command as its own file,
``.wg-solve-requests/<commandId>.json`` -- the WG request inbox. Schema 3 is a
solve (delivery version 3; docs/architecture/CAD-OPERATIONS.md). Schema 4 names
its ``kind``: ``prepare_and_solve`` (Solve) or ``receive_snapshot`` (Send), the
one difference between the two (M1 transfer contract, C2 and C3). A file WG can
identify as a request but not accept -- no or an unknown kind, a broken
``returnId`` rule, a bad id -- is claimed, refused visibly and deleted; one it
cannot identify at all is left alone. Each delivered file is claimed
by renaming it, read, persisted as a ``prepare_and_solve`` operation in the CAD
operation store (``cad_operations`` in ``cadlink.db``), and only then deleted.
What a WGLink older than version 3 writes -- the single slot
``.wg-solve-request.json``, or a version-2 file -- is refused visibly, with the
remedy: WG installs the add-in it ships, and Fusion has to load it. From then on the
store, not the file, is the command: the backend's delivery loop is its one
consumer, and prepares it from there. A WGLink with a live session delivers the
same item over HTTP instead (``server/cadlink/live/deliveries.py``); both paths
accept through ``accept_delivery``, so one item is one operation whichever way,
or both ways, it arrives. The ledger helpers below are the store's
view of terminal outcomes. A command whose gates block is not terminal: it
stays so the user can acknowledge findings and run it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import json
import logging
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Mapping
import uuid

from .identity import utc_now
from .operations import (
    ACCEPTED,
    PREPARE_AND_SOLVE,
    RECEIVE_SNAPSHOT,
    REJECTED,
    TERMINAL_STATES,
    normalize_request,
    prepare_and_solve_request,
    request_digest,
)
from server.platform.private_paths import ensure_private_directory

if TYPE_CHECKING:
    from .store import CadLinkStore


# What a WGLink older than delivery version 3 writes: the single slot, and
# version-2 files in the folder below. WG takes them only to refuse them.
SOLVE_REQUEST_FILENAME = ".wg-solve-request.json"
LEGACY_SCHEMA_VERSION = 1
OLDER_SCHEMA_VERSION = 2
_SLOT_SCHEMAS = frozenset({LEGACY_SCHEMA_VERSION})
_FILE_SCHEMAS = frozenset({OLDER_SCHEMA_VERSION, 3, 4})
#: The schemas WG accepts as current: 3 is a solve, 4 names its kind. Anything
#: else it can identify is an older add-in's, refused with the remedy.
CURRENT_SCHEMAS = frozenset({3, 4})
#: The schema whose requests name their kind (receive_snapshot or prepare_and_solve).
KINDED_SCHEMA_VERSION = 4
_KINDS = frozenset({PREPARE_AND_SOLVE, RECEIVE_SNAPSHOT})
_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
UNREADABLE_CLAIM_REASON = "This file is not a request WG can read: not JSON, or no version WG knows."
#: How many passes a claim WG cannot read (the read itself fails: a file another
#: process holds, a drive that went away) is kept and read again before WG says
#: so. It is still never deleted: it may be a valid request that is held.
UNREADABLE_PASSES = 30
#: A read or delete that meets a file another process holds is retried briefly
#: before the pass moves on (Windows sharing violations are short).
_HELD_ATTEMPTS = 10
_HELD_RETRY_SECONDS = 0.02
#: Request files are tiny (normally under 1 KiB). Bound what is read into
#: memory while leaving ample room for compatible future fields.
MAX_REQUEST_BYTES = 64 * 1024
# Each claim WG could not read, by claim name: the request file it was, and how
# many passes its read has failed.
_unreadable_waits: dict[str, tuple[str, int]] = {}
# Claims already refused (or reported unreadable) whose file is still there, by
# claim name: reported once; afterwards only the delete is retried, quietly.
_refused_claims: set[str] = set()
# Refused claims whose acknowledgement file could not be written yet, by claim
# name: (command id, reason). The delete waits for the acknowledgement.
_pending_refusal_acks: dict[str, tuple[str, str, str | None, str | None]] = {}
# Passes an acknowledgement has failed to be written, by claim name.
_ack_failures: dict[str, int] = {}
_DELIVERY_DIR_FDS = os.name != "nt" and all(
    operation in os.supports_dir_fd for operation in (os.open, os.rename, os.unlink)
)


class _Unreadable:
    """What reading a file returned when the read itself failed."""

    def __init__(self, error: OSError) -> None:
        self.error = error


class _UnsafePayload:
    """A path whose own filesystem metadata makes it unsafe to read."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


def _held(error: OSError) -> bool:
    return not isinstance(error, FileNotFoundError)
# One file per command, named <commandId>.json. A producer stages a file under
# a name starting with "." (or not ending in .json) and renames it into place.
SOLVE_REQUESTS_DIRECTORY = ".wg-solve-requests"
SCHEMA_VERSION = 3
OUTDATED_ADDIN_REASON = (
    "This solve request came from a WGLink add-in older than this Waveguide "
    "Generator, which it no longer accepts. Restart Fusion so it loads the WGLink "
    "that WG installed, then use Solve in WG again."
)
# The submission key a solve command's job is submitted under: by the backend's
# preparation (``preparation.submission_key``), and before the backend owned
# solves, by the browser. A job created under it is that command's outcome,
# whatever request it carried.
CAD_SOLVE_SUBMISSION_PREFIX = "cad-solve:"
# A delivery is claimed by renaming it to this prefix in its own folder before
# it is read, so a producer writing the same path afterwards writes a new file
# rather than one the consumer is about to delete.
CLAIM_PREFIX = ".wg-solve-claim-"
# What retaining a delivery's snapshot found (CAD-OPERATIONS.md, "Consuming a
# delivery"). A delivery is acknowledged once its snapshot is retained, or once
# its return can never be retained as it is named, which preparation then
# refuses. A return that cannot be read now keeps its claim for the next pass,
# for at most RETENTION_PASSES passes; then the delivery is acknowledged anyway
# and the operation waits for its return, so no claim is held for ever.
RETAINED = "retained"
RETAIN_INVALID = "invalid"
RETAIN_TRANSIENT = "transient"
# About half a minute at the delivery loop's cadence.
RETENTION_PASSES = 30
# The outcome of each delivered request is published as <commandId>.json in this
# folder, next to the request inbox, before the request file is deleted, so the
# producer can tell "WG accepted it" from "WG refused it" once its file is gone
# (CAD-OPERATIONS.md, "Request acknowledgement file").
SOLVE_ACKS_DIRECTORY = ".wg-solve-acks"
ACK_SCHEMA_VERSION = 1
ACK_ACCEPTED = "accepted"
ACK_REFUSED = "refused"
# An acknowledgement is kept this long after it was written, then pruned; the
# folder never holds more than ACK_MAX_FILES (oldest go first).
ACK_RETENTION_SECONDS = 24 * 3600
ACK_MAX_FILES = 500
# An acknowledgement that cannot be written keeps its claim for this many passes
# (about half a minute at the loop's cadence). Then WG gives it up, once and
# loudly, and consumes the request: the operation was made durable first, and a
# producer that finds no acknowledgement says so rather than waiting. A claim
# whose operation could not be made durable is never given up.
ACK_FAILURE_PASSES = 30
# Pruning runs at most this often, from the delivery pass.
ACK_PRUNE_INTERVAL_SECONDS = 60.0
# The JSON outcome ledger of earlier versions. The store imports it once and
# renames it; nothing reads it after that.
LEDGER_FILENAME = "solve-commands.json"
IPC_SUBDIRECTORY = Path("ipc") / "wglink"
# How many recent outcomes ``read_ledger`` returns.
LEDGER_LIMIT = 200

_LEDGER_STATES = {"accepted": ACCEPTED, "refused": REJECTED}

logger = logging.getLogger(__name__)
# One consumer per process at a time. Files are still claimed by rename,
# because the producer writing them is another process.
_DELIVERY_LOCK = threading.Lock()
# Each kept claim, by claim name: the operation it names, and how many passes
# it has waited for that operation's return.
_retention_waits: dict[str, tuple[str, int]] = {}
# How long a delivery over HTTP whose return cannot be read is answered 503 and
# held, from its first such answer (CADLINK-LIVE-PROTOCOL.md section 8). The
# file claim's RETENTION_PASSES is the same bound counted in passes.
LIVE_TRANSIENT_BOUND_S = 30.0
# How long past its bound a transient deadline is remembered once nothing holds
# it any more. A retry within that time is acknowledged at once; the entry is
# then dropped, so memory stays bounded, and a retry later still starts a new
# bound.
LIVE_DEADLINE_MEMORY_S = 300.0
# The operations deliveries over HTTP hold, kept apart from the file claims
# above: operation id -> whether a delivery is in flight, and the deadline of
# its first transient answer. Guarded by its own lock, which readers take
# without _DELIVERY_LOCK, so a slow retention never blocks a delivery pass.
_LIVE_WAITS_LOCK = threading.Lock()
_live_waits: dict[str, _LiveWait] = {}
_now = time.monotonic


@dataclass(frozen=True)
class PendingSolveCommand:
    """A CAD-authored request to prepare and solve one exact return bundle.

    ``marker_path`` is the file it was read from.
    """

    marker_path: Path | None
    command_id: str
    return_id: str
    bundle_path: str
    manifest_sha256: str
    requested_at: str
    #: ``prepare_and_solve`` for every schema before 4; schema 4 names it.
    kind: str = PREPARE_AND_SOLVE

    def payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "commandId": self.command_id,
            "returnId": self.return_id,
            "bundlePath": self.bundle_path,
            "manifestSha256": self.manifest_sha256,
            "requestedAt": self.requested_at,
        }


@dataclass
class _LiveWait:
    #: A delivery of the operation is being accepted or retained now.
    in_flight: bool
    #: LIVE_TRANSIENT_BOUND_S after its first transient answer; None before one.
    deadline: float | None

    def held(self, now: float) -> bool:
        return self.in_flight or (self.deadline is not None and now < self.deadline)


@dataclass(frozen=True)
class DeliveredItem:
    """One WG-bound delivery, by file or over HTTP: only what identifies it.

    ``requestedAt``, the file name, tokens and headers are transport and never
    reach it, so the same item by either path has the same digest.
    """

    operation_id: str
    kind: str
    bundle_path: str
    manifest_sha256: str
    #: Solve deliveries only.
    return_id: str | None = None

    @classmethod
    def from_command(cls, command: PendingSolveCommand) -> DeliveredItem:
        return cls(
            operation_id=command.command_id,
            kind=command.kind,
            bundle_path=command.bundle_path,
            manifest_sha256=command.manifest_sha256,
            return_id=command.return_id if command.kind == PREPARE_AND_SOLVE else None,
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> DeliveredItem:
        """The item a live delivery body names (already validated by the route)."""

        return cls(
            operation_id=str(payload["operationId"]),
            kind=str(payload["kind"]),
            bundle_path=str(payload["bundlePath"]),
            manifest_sha256=str(payload["manifestSha256"]),
            return_id=(
                str(payload.get("returnId") or "") if payload["kind"] == PREPARE_AND_SOLVE else None
            ),
        )

    def request(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.kind == PREPARE_AND_SOLVE:
            return prepare_and_solve_request(
                return_id=self.return_id or "",
                bundle_path=self.bundle_path,
                manifest_sha256=self.manifest_sha256,
            )
        if self.kind == RECEIVE_SNAPSHOT:
            return normalize_request(
                RECEIVE_SNAPSHOT,
                {},
                {"bundle_path": self.bundle_path, "manifest_sha256": self.manifest_sha256},
            )
        raise ValueError(f"{self.kind!r} is not delivered to WG")


@dataclass(frozen=True)
class DeliveryAnswer:
    """What accepting one delivery did.

    ``result`` is the store's: ``created``, ``recovered`` or ``conflict``. A
    conflict leaves the stored operation untouched. ``retention`` is what
    ``retain`` reported for the stored operation, or None without one. ``row``
    is the operation as it stands after retention.
    """

    row: dict[str, Any]
    result: str
    digest: str
    retention: object | None


def accept_delivery(
    store: CadLinkStore,
    item: DeliveredItem,
    *,
    retain: Callable[[str], object] | None,
) -> DeliveryAnswer:
    """Accept a delivered item, or recover the operation it repeats, then retain.

    The one acceptance of both transports: the v3 file pass and the live
    route. Same id and digest recovers; a different digest or kind is
    ``conflict``, decided here from the store's result and nothing else.
    ``retain`` runs for the operation stored under the id whatever the result,
    as the file pass always did. Delivery paths call it under ``_DELIVERY_LOCK``.
    """

    target, inputs = item.request()
    digest = request_digest(item.kind, target, inputs)
    row, result = store.accept_operation(item.operation_id, item.kind, digest, target, inputs)
    if result != "conflict" and store.data_root is not None:
        # Only this acceptance boundary knows it came through the add-in's
        # inbox/live transport. Ingests and ordinary operations do not.
        # The operation is already stored: a record that cannot be
        # written must not fail the delivery. The next one writes it.
        from server.cadlink.usage import record_usage

        try:
            record_usage(store.data_root, "addin-delivery")
        except OSError as exc:
            logger.warning("Could not record CAD Link use for WGLink activation: %s", exc)
    if retain is None:
        return DeliveryAnswer(row=row, result=result, digest=digest, retention=None)
    retention = retain(item.operation_id)
    current = store.get_operation(item.operation_id)
    return DeliveryAnswer(
        row=current if current is not None else row, result=result, digest=digest,
        retention=retention,
    )


def live_held_operation_ids() -> frozenset[str]:
    """The operations a delivery over HTTP holds now.

    Held: in flight, or answered transient less than ``LIVE_TRANSIENT_BOUND_S``
    ago. A deadline that has passed no longer holds, but it is remembered (for
    ``LIVE_DEADLINE_MEMORY_S``) so the retry is acknowledged at the bound rather
    than given a new one. Takes only the live waits' lock, never
    ``_DELIVERY_LOCK``. A reader lists rows first and calls this after: every
    hold is recorded before its operation commits, so a listed row still being
    delivered is in the answer.
    """

    now = _now()
    with _LIVE_WAITS_LOCK:
        for operation_id, wait in list(_live_waits.items()):
            if (
                not wait.in_flight
                and wait.deadline is not None
                and wait.deadline + LIVE_DEADLINE_MEMORY_S <= now
            ):
                del _live_waits[operation_id]
        return frozenset(
            operation_id for operation_id, wait in _live_waits.items() if wait.held(now)
        )


LIVE_ACCEPTED = "accepted"
LIVE_CONFLICT = "conflict"
LIVE_TRANSIENT = "transient"


@dataclass(frozen=True)
class LiveDelivery:
    """The live route's answer: accepted (200), conflict (409) or transient (503)."""

    status: str
    answer: DeliveryAnswer


def deliver_live(
    store: CadLinkStore,
    item: DeliveredItem,
    *,
    retain: Callable[[str], object],
) -> LiveDelivery:
    """One delivery over HTTP, held against the delivery pass while in flight.

    Under ``_DELIVERY_LOCK`` the operation is held before it is accepted, then
    its snapshot retained. How the hold ends:

    - accepted (200): released, with any transient deadline;
    - a return that cannot be read now (503): held until
      ``LIVE_TRANSIENT_BOUND_S`` after the first such answer. A retry keeps
      that deadline; one at or after it is acknowledged (200) and released;
    - a conflict (409), a busy store or any other exception: the hold goes
      back to what it was before this delivery, so a conflicting or failed
      delivery never releases an earlier delivery's transient hold.
    """

    operation_id = item.operation_id
    with _DELIVERY_LOCK:
        with _LIVE_WAITS_LOCK:
            previous = _live_waits.get(operation_id)
            bound = previous.deadline if previous is not None else None
            _live_waits[operation_id] = _LiveWait(in_flight=True, deadline=bound)
        after: _LiveWait | None = previous
        try:
            answer = accept_delivery(store, item, retain=retain)
            if answer.result == "conflict":
                return LiveDelivery(LIVE_CONFLICT, answer)
            if answer.retention == RETAIN_TRANSIENT:
                now = _now()
                if bound is None or now < bound:
                    after = _LiveWait(
                        in_flight=False,
                        deadline=bound if bound is not None else now + LIVE_TRANSIENT_BOUND_S,
                    )
                    return LiveDelivery(LIVE_TRANSIENT, answer)
                logger.warning(
                    "The return of CAD operation %r could not be read in %.0f s. Its live "
                    "delivery is acknowledged, and the operation waits for its return.",
                    operation_id,
                    LIVE_TRANSIENT_BOUND_S,
                )
            after = None
            return LiveDelivery(LIVE_ACCEPTED, answer)
        finally:
            with _LIVE_WAITS_LOCK:
                if after is not None:
                    _live_waits[operation_id] = _LiveWait(
                        in_flight=False, deadline=after.deadline
                    )
                else:
                    _live_waits.pop(operation_id, None)


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
    # Harden through the path as configured, so a symlinked data root is seen
    # and left alone; hand callers the resolved folder, as before.
    folder = Path(data_dir) / IPC_SUBDIRECTORY
    if create:
        ensure_private_directory(folder, parents=True, data_root=Path(data_dir))
    return folder.resolve()


def legacy_ledger_path(data_dir: Path) -> Path:
    """Where earlier versions kept terminal solve-command outcomes."""

    return ipc_folder(Path(data_dir)) / LEDGER_FILENAME


class InvalidRequest(ValueError):
    """A file WG identifies as a request to it, which it cannot accept.

    Identified means a JSON object with ``target: waveguide-generator`` and a
    ``schemaVersion`` WG recognises. Such a file is claimed, refused visibly
    and deleted, never left to be re-read by every pass.
    """


def _command_from_payload(
    payload: object, path: Path, *, schema_versions: frozenset[int]
) -> PendingSolveCommand | None:
    """The request a file holds; None when it is not identifiably one.

    Raises :class:`InvalidRequest` for an identified request that fails
    validation.
    """

    if not isinstance(payload, Mapping):
        return None
    schema = payload.get("schemaVersion")
    # Only an integer is compared: a list or object here is not a request WG can
    # identify, and must never stop the pass (``in`` on it raises).
    if (
        not isinstance(schema, int)
        or isinstance(schema, bool)
        or schema not in schema_versions
        or payload.get("target") != "waveguide-generator"
    ):
        return None
    command_id = payload.get("commandId")
    bundle_path = payload.get("bundlePath")
    manifest_sha256 = payload.get("manifestSha256")
    if not all(isinstance(value, str) and value for value in (command_id, bundle_path, manifest_sha256)):
        raise InvalidRequest("It does not name its commandId, bundlePath and manifestSha256.")
    # A request may name its operation, and then it must be the command id:
    # otherwise WG cannot tell which identity the producer meant.
    if payload.get("operationId", command_id) != command_id:
        raise InvalidRequest("Its operationId is not its commandId.")
    kind = PREPARE_AND_SOLVE
    return_id = str(payload.get("returnId") or "")
    if schema != KINDED_SCHEMA_VERSION and "kind" in payload and payload["kind"] != PREPARE_AND_SOLVE:
        # Schema 3 and earlier mean a solve. A file that says it is something
        # else is not read as one.
        raise InvalidRequest(
            f"A schema-{schema} request is a solve, and this one names another kind."
        )
    if schema == KINDED_SCHEMA_VERSION:
        kind = payload.get("kind")
        if not isinstance(kind, str) or kind not in _KINDS:
            raise InvalidRequest(
                "It does not name its kind (receive_snapshot or prepare_and_solve)."
                if kind is None
                else f"Its kind {kind!r} is not one WG receives."
            )
        if not _OPERATION_ID.fullmatch(str(command_id)):
            raise InvalidRequest("Its commandId is not a plain operation id.")
        if kind == PREPARE_AND_SOLVE:
            if not isinstance(payload.get("returnId"), str):
                raise InvalidRequest("A solve request names its returnId.")
            return_id = str(payload["returnId"])
        elif "returnId" in payload:
            raise InvalidRequest("A snapshot request names no returnId.")
    return PendingSolveCommand(
        marker_path=path,
        command_id=str(command_id),
        return_id=return_id,
        bundle_path=str(bundle_path),
        manifest_sha256=str(manifest_sha256),
        requested_at=str(payload.get("requestedAt") or ""),
        kind=str(kind),
    )


def _read_payload(path: Path, *, dir_fd: int | None = None) -> object:
    """The JSON a file holds; None when its bytes are not JSON; :class:`_Unreadable`
    when the read itself failed, after a brief retry of a file that is held.

    The two failures mean different things: bytes that are not a request can be
    refused, a file WG could not read may be a valid request it must keep.
    """

    for attempt in range(_HELD_ATTEMPTS):
        descriptor: int | None = None
        try:
            metadata = (
                os.stat(path.name, dir_fd=dir_fd, follow_symlinks=False)
                if dir_fd is not None
                else path.lstat()
            )
            is_reparse_point = bool(
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            if stat.S_ISLNK(metadata.st_mode) or is_reparse_point:
                return _UnsafePayload("WG refuses a request path that is a symbolic link.")
            if not stat.S_ISREG(metadata.st_mode):
                return _UnsafePayload("WG refuses a request path that is not a regular file.")

            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
            nofollow = getattr(os, "O_NOFOLLOW", 0)
            if os.name != "nt":
                flags |= nofollow
            else:
                flags |= getattr(os, "O_BINARY", 0)
            try:
                descriptor = (
                    os.open(path.name, flags, dir_fd=dir_fd)
                    if dir_fd is not None
                    else os.open(path, flags)
                )
            except OSError as exc:
                if nofollow and exc.errno == errno.ELOOP:
                    return _UnsafePayload(
                        "WG refuses a request path that is a symbolic link."
                    )
                raise

            opened = os.fstat(descriptor)
            if os.name == "nt" and (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                return _UnsafePayload(
                    "WG refuses a request path that changed while it was opened."
                )
            if not stat.S_ISREG(opened.st_mode):
                return _UnsafePayload("WG refuses a request path that is not a regular file.")
            if opened.st_size > MAX_REQUEST_BYTES:
                return _UnsafePayload(
                    f"WG refuses a request file larger than {MAX_REQUEST_BYTES} bytes."
                )

            chunks: list[bytes] = []
            remaining = MAX_REQUEST_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload_bytes = b"".join(chunks)
            if len(payload_bytes) > MAX_REQUEST_BYTES:
                return _UnsafePayload(
                    f"WG refuses a request file larger than {MAX_REQUEST_BYTES} bytes."
                )
            text = payload_bytes.decode("utf-8")
        except OSError as exc:
            if not _held(exc) or attempt == _HELD_ATTEMPTS - 1:
                return _Unreadable(exc)
            time.sleep(_HELD_RETRY_SECONDS)
            continue
        except ValueError:
            return None  # read, and not text
        finally:
            if descriptor is not None:
                os.close(descriptor)
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None
    return None  # pragma: no cover - the loop always returns


@dataclass(frozen=True)
class _Delivery:
    path: Path
    claimed: bool
    schema_versions: frozenset[int]
    #: None for a file that is to be refused (``invalid`` says why).
    command: PendingSolveCommand | None
    age: tuple[str, int, str]
    invalid: str | None = None
    #: A pinned directory for every filesystem operation in this pass (POSIX).
    dir_fd: int | None = None


def _files(directory: Path, *, dir_fd: int | None = None) -> list[Path]:
    try:
        # Unsafe leaf types must reach ``_read_payload`` for visible refusal.
        # Real directories are not request files and remain untouched.
        if dir_fd is not None:
            return [
                directory / name
                for name in os.listdir(dir_fd)
                if not stat.S_ISDIR(
                    os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
                )
            ]
        return [path for path in directory.iterdir() if path.is_symlink() or not path.is_dir()]
    except OSError:
        return []


def _delivery(
    path: Path,
    *,
    claimed: bool,
    schema_versions: frozenset[int],
    dir_fd: int | None = None,
) -> _Delivery | None:
    payload = _read_payload(path, dir_fd=dir_fd)
    invalid: str | None = None
    if isinstance(payload, _UnsafePayload):
        invalid = payload.reason
        payload = None
    if isinstance(payload, _Unreadable):
        # Not read: nothing is known about it. An unclaimed file is tried again
        # next pass; a claim is kept, and the pass reads it again.
        if not claimed:
            return None
        try:
            modified = (
                os.stat(path.name, dir_fd=dir_fd).st_mtime_ns
                if dir_fd is not None
                else path.stat().st_mtime_ns
            )
        except OSError:
            return None
        return _Delivery(
            path, claimed, schema_versions, None, ("", modified, path.name), None, dir_fd
        )
    if invalid is None:
        try:
            command = _command_from_payload(payload, path, schema_versions=schema_versions)
        except InvalidRequest as exc:
            command, invalid = None, str(exc)
    else:
        command = None
    if command is None and invalid is None:
        # Not identifiably a request: left alone -- unless it is a claim, which
        # only this consumer makes, and which must not be re-read for ever.
        if not claimed:
            return None
        invalid = UNREADABLE_CLAIM_REASON
    try:
        modified = (
            os.stat(path.name, dir_fd=dir_fd, follow_symlinks=False).st_mtime_ns
            if dir_fd is not None
            else path.lstat().st_mtime_ns
        )
    except OSError:
        return None
    requested_at = (
        command.requested_at
        if command is not None
        else str(payload.get("requestedAt") or "") if isinstance(payload, Mapping) else ""
    )
    return _Delivery(
        path,
        claimed,
        schema_versions,
        command,
        (requested_at, modified, path.name),
        invalid,
        dir_fd,
    )


def inbox_refusal(payload: object, file_name: str, reason: str) -> dict[str, Any]:
    """A refusal of a taken file that has no operation row of its own."""

    command_id = payload.get("commandId") if isinstance(payload, Mapping) else None
    return {
        "operationId": command_id if isinstance(command_id, str) and command_id else None,
        "file": file_name,
        "reason": reason,
        "at": utc_now(),
    }


def _delivery_directories(data_dir: Path) -> tuple[Path, Path]:
    folder = ipc_folder(data_dir, create=True)
    requests = folder / SOLVE_REQUESTS_DIRECTORY
    ensure_private_directory(
        Path(data_dir) / IPC_SUBDIRECTORY / SOLVE_REQUESTS_DIRECTORY,
        data_root=Path(data_dir),
    )
    return folder, requests


@contextmanager
def _pinned_delivery_directories(
    data_dir: Path,
):
    """Pin each delivery directory for one complete collection pass.

    Configured data roots may intentionally be symlinks, so resolve the chosen
    directories first and apply ``O_NOFOLLOW`` only to each resolved leaf. On
    Windows, where these relative operations are unavailable, leaf identity is
    still checked by :func:`_read_payload`; parent replacement remains inside
    the established same-user filesystem trust boundary.
    """

    directories = _delivery_directories(data_dir)
    descriptors: dict[Path, int | None] = dict.fromkeys(directories)
    opened: list[int] = []
    try:
        if _DELIVERY_DIR_FDS:
            flags = (
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
            )
            for directory in directories:
                descriptor = os.open(directory.resolve(strict=True), flags)
                descriptors[directory] = descriptor
                opened.append(descriptor)
        yield directories, descriptors
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _deliveries(
    data_dir: Path,
    *,
    dir_fds: Mapping[Path, int | None] | None = None,
    directories: tuple[Path, Path] | None = None,
) -> list[_Delivery]:
    """Every solve command waiting on disk, oldest first.

    That is the per-command files, what an older WGLink wrote (taken only to be
    refused), and any claim an interrupted poll left behind. A file WG cannot
    read as a solve command -- malformed, a newer schema, or a producer's
    staging file -- is left where it is. Age is the requested time, then the
    file's modification time.
    """

    folder, requests = directories or _delivery_directories(data_dir)
    found: list[_Delivery | None] = []
    for directory, versions in ((folder, _SLOT_SCHEMAS), (requests, _FILE_SCHEMAS)):
        dir_fd = dir_fds.get(directory) if dir_fds is not None else None
        for path in _files(directory, dir_fd=dir_fd):
            if path.name.startswith(CLAIM_PREFIX) and path.suffix == ".json":
                found.append(
                    _delivery(
                        path, claimed=True, schema_versions=versions, dir_fd=dir_fd
                    )
                )
    found.append(
        _delivery(
            folder / SOLVE_REQUEST_FILENAME,
            claimed=False,
            schema_versions=_SLOT_SCHEMAS,
            dir_fd=dir_fds.get(folder) if dir_fds is not None else None,
        )
    )
    requests_fd = dir_fds.get(requests) if dir_fds is not None else None
    for path in _files(requests, dir_fd=requests_fd):
        if not path.name.startswith(".") and path.suffix == ".json":
            found.append(
                _delivery(
                    path,
                    claimed=False,
                    schema_versions=_FILE_SCHEMAS,
                    dir_fd=requests_fd,
                )
            )
    return sorted((item for item in found if item is not None), key=lambda item: item.age)


def _claim(path: Path, *, dir_fd: int | None = None) -> Path | None:
    """Take a delivery by renaming it; None means try again on the next poll.

    The rename fails when the file is already gone (another consumer took it)
    or, on Windows, while its writer still holds it open.
    """

    claim = path.with_name(f"{CLAIM_PREFIX}{uuid.uuid4().hex}.json")
    try:
        if dir_fd is None:
            os.rename(path, claim)
        else:
            os.rename(path.name, claim.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.debug("Solve command %s is not claimable yet: %s", path.name, exc)
        return None
    return claim


def _acknowledge(
    path: Path, *, quiet: bool = False, dir_fd: int | None = None
) -> bool:
    """Delete a consumed delivery. False leaves it for the next poll to recover.

    A file another process holds is retried briefly first.
    """

    for attempt in range(_HELD_ATTEMPTS):
        try:
            if dir_fd is None:
                path.unlink()
            else:
                os.unlink(path.name, dir_fd=dir_fd)
        except FileNotFoundError:
            return True
        except OSError as exc:
            if attempt < _HELD_ATTEMPTS - 1:
                time.sleep(_HELD_RETRY_SECONDS)
                continue
            (logger.debug if quiet else logger.warning)(
                "Could not delete the consumed solve command %s: %s", path.name, exc
            )
            return False
        return True
    return False  # pragma: no cover - the loop always returns


def _ack_directory(data_dir: Path) -> Path:
    folder = ipc_folder(data_dir, create=True)
    ensure_private_directory(
        Path(data_dir) / IPC_SUBDIRECTORY / SOLVE_ACKS_DIRECTORY,
        data_root=Path(data_dir),
    )
    return folder / SOLVE_ACKS_DIRECTORY


def write_acknowledgement(
    data_dir: Path,
    command_id: str,
    outcome: str,
    *,
    reason: str | None = None,
    job_id: str | None = None,
    digest: str | None = None,
    manifest_sha256: str | None = None,
    kind: str | None = None,
    only_if_missing: bool = False,
) -> bool:
    """Publish the outcome of a delivered request, atomically. False: could not be written.

    The file is staged under a name starting with "." and moved into place with
    ``os.replace``, so a reader sees the whole old file or the whole new one. A
    replace that meets a reader holding the target (Windows raises
    ``PermissionError``) is retried briefly. A command id that is not a plain
    operation id names no safe file and is not acknowledged (True: nothing owed).
    """

    if not _OPERATION_ID.fullmatch(command_id):
        # Nothing to publish under: not a failure, and never a reason to keep
        # the request.
        return True
    payload = {
        "schemaVersion": ACK_SCHEMA_VERSION,
        "commandId": command_id,
        "operationId": command_id,
        "outcome": outcome,
        "reason": reason,
        "jobId": job_id,
        "digest": digest,
        "manifestSha256": manifest_sha256,
        "kind": kind,
        "at": utc_now(),
    }
    try:
        directory = _ack_directory(data_dir)
        target = directory / f"{command_id}.json"
        if only_if_missing and target.exists():
            return True
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{command_id}.", suffix=".tmp", dir=directory
        )
    except OSError as exc:
        logger.warning("Could not stage the acknowledgement of solve command %s: %s", command_id, exc)
        return False
    temporary = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(_HELD_ATTEMPTS):
            try:
                os.replace(temporary, target)
                replaced = True
                _sync_directory(directory)
                return True
            except PermissionError:
                if attempt + 1 == _HELD_ATTEMPTS:
                    raise
                time.sleep(_HELD_RETRY_SECONDS)
    except OSError as exc:
        logger.warning("Could not write the acknowledgement of solve command %s: %s", command_id, exc)
        return False
    finally:
        if not replaced:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.debug("Could not remove the staged acknowledgement %s.", temporary.name)
    return False  # pragma: no cover - the loop always returns or raises


def _sync_directory(directory: Path) -> None:
    """Flush a folder's entries to disk (POSIX), so a rename outlives a power cut."""

    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _ack_written(claim_name: str, written: bool | None) -> bool:
    """Whether a claim may go on to be deleted after its acknowledgement was tried.

    A failed write keeps the claim for ``ACK_FAILURE_PASSES`` passes; then it is
    given up once, with an error, and the request is consumed without one.
    ``None`` means the operation could not be made durable: the claim is kept,
    however often, because consuming it would risk losing the request.
    """

    if written is None:
        return False
    if written:
        _ack_failures.pop(claim_name, None)
        return True
    failed = _ack_failures.get(claim_name, 0) + 1
    if failed >= ACK_FAILURE_PASSES:
        _ack_failures.pop(claim_name, None)
        logger.error(
            "Could not publish the acknowledgement for %s in %d passes; giving it up. "
            "The request is consumed, and its producer will find no acknowledgement.",
            claim_name, failed,
        )
        return True
    _ack_failures[claim_name] = failed
    return False


def prune_acknowledgements(
    data_dir: Path,
    *,
    now: float | None = None,
    retention_seconds: float = ACK_RETENTION_SECONDS,
    max_files: int = ACK_MAX_FILES,
) -> int:
    """Delete acknowledgements past their retention, then the oldest above the cap.

    Returns how many files were removed. A file that cannot be deleted now
    (held by a reader on Windows) is left for the next prune.
    """

    directory = Path(data_dir) / IPC_SUBDIRECTORY / SOLVE_ACKS_DIRECTORY
    now = time.time() if now is None else now
    try:
        found = []
        for entry in directory.iterdir():
            try:
                if entry.is_file() and not entry.is_symlink():
                    found.append((entry.stat().st_mtime, entry))
            except OSError:
                continue
    except OSError:
        return 0
    found.sort(key=lambda item: item[0])
    keep_from = max(0, len(found) - max_files)
    removed = 0
    for index, (modified, entry) in enumerate(found):
        if index < keep_from or now - modified > retention_seconds:
            try:
                entry.unlink()
                removed += 1
            except OSError as exc:
                logger.debug("Could not prune the acknowledgement %s: %s", entry.name, exc)
    return removed


_last_ack_prune = 0.0


def _prune_acknowledgements_due(data_dir: Path) -> None:
    global _last_ack_prune
    moment = _now()
    if moment - _last_ack_prune < ACK_PRUNE_INTERVAL_SECONDS and _last_ack_prune:
        return
    _last_ack_prune = moment
    try:
        prune_acknowledgements(data_dir)
    except Exception:  # noqa: BLE001 - housekeeping never fails a delivery
        logger.debug("Could not prune the solve acknowledgements.", exc_info=True)


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


def _file_answer(command: PendingSolveCommand, accepted: DeliveryAnswer) -> dict[str, Any] | None:
    """What a v3 file delivery is owed on its own, from its acceptance.

    The answer this delivery is owed on its own, or None when the operation it
    names waits to be handed out: the outcome that already stands, or a
    refusal when its id already names a different request that is finished or
    is not a solve. A refusal is logged whatever is answered.
    """

    row, result, digest = accepted.row, accepted.result, accepted.digest
    if result == "created":
        # Its own outcome, however it settled, is news, not a replay: a
        # snapshot settles at acceptance, and must not hold later files back.
        return None
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


def _keep_for_retention(claim: Path, command: PendingSolveCommand) -> bool:
    """Whether a claim whose return cannot be read now waits for another pass."""

    waited = _retention_waits.get(claim.name, (command.command_id, 0))[1] + 1
    if waited < RETENTION_PASSES:
        _retention_waits[claim.name] = (command.command_id, waited)
        if waited == 1:
            logger.info(
                "The return of solve command %r cannot be read yet; its delivery is kept "
                "for the next pass.",
                command.command_id,
            )
        return True
    logger.warning(
        "The return of solve command %r could not be read in %d passes. Its delivery is "
        "acknowledged, and the operation waits for its return.",
        command.command_id,
        waited,
    )
    return False


def _echo(payload: object) -> tuple[str | None, str | None]:
    """The manifest hash and kind a request names, for its acknowledgement."""

    if not isinstance(payload, Mapping):
        return None, None
    manifest, kind = payload.get("manifestSha256"), payload.get("kind")
    return (
        manifest if isinstance(manifest, str) else None,
        kind if isinstance(kind, str) else None,
    )


def _row_manifest(row: Mapping[str, Any]) -> str | None:
    try:
        inputs = json.loads(row.get("inputs_json") or "{}")
    except (TypeError, ValueError):
        return None
    manifest = inputs.get("manifest_sha256") if isinstance(inputs, Mapping) else None
    return manifest if isinstance(manifest, str) else None


def _publish_acknowledgement(
    data_dir: Path,
    store: CadLinkStore,
    command: PendingSolveCommand,
) -> bool | None:
    """Publish what became of a delivered request. False: not written yet.
    None: the operation could not be made durable, so nothing may be consumed.

    The operation's own row is the acknowledgement, so a redelivery replays it,
    and a different request under an id the store holds -- a conflict -- speaks
    for the operation, never against it. The row is forced durable first: an
    acknowledgement must not outlive an acceptance a power cut can undo.
    """

    row = store.get_operation(command.command_id)
    if row is None:
        return True
    try:
        store.make_durable()
    except Exception:  # noqa: BLE001 - an acknowledgement is never owed on a row that may vanish
        logger.warning(
            "Could not make the acceptance of solve command %s durable.",
            command.command_id, exc_info=True,
        )
        return None
    # Only a rejected row is a refusal; received, processing and accepted rows
    # are operations WG holds.
    refused_outcome = row["state"] == REJECTED
    return write_acknowledgement(
        data_dir,
        command.command_id,
        ACK_REFUSED if refused_outcome else ACK_ACCEPTED,
        reason=_entry(row)["reason"] if refused_outcome else None,
        job_id=row["job_id"],
        digest=row.get("request_digest"),
        manifest_sha256=_row_manifest(row) or command.manifest_sha256,
        kind=row["kind"],
    )


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
    held: set[str] | None = None,
    accept_solve: Callable[[str], object] | None = None,
    publish: Callable[[Mapping[str, Any]], None] | None = None,
    refuse: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    """Move delivered solve commands into the operation store, oldest first.

    Each file is claimed by rename, read, persisted and only then deleted. A
    newer command written to the same slot meanwhile therefore survives, and a
    poll interrupted after a claim leaves the claim for the next poll to finish.
    ``retain`` keeps the snapshot an operation names in WG's own storage before
    its delivery is acknowledged (CAD-OPERATIONS.md, "Consuming a delivery").
    When it reports ``RETAIN_TRANSIENT`` the claim is kept for the next pass,
    up to ``RETENTION_PASSES``, and the pass goes on to the files behind it.
    ``held`` receives the id of every operation whose claim is still waiting,
    whether or not this pass reaches that claim, so nothing prepares it from a
    return WG does not hold yet.

    Returns the answer owed to one delivery on its own -- the replay of an
    outcome that already stands, or a refusal of a different request under an
    id whose operation is finished or is not a solve -- as ``{"command",
    "outcome"}``, or None. Nothing hands it to a client: the refusal is logged,
    and the operation that holds the id is what the loop prepares. It stops at
    that answer, so later files wait for the next pass.

    ``publish`` receives every operation row a file created, recovered or
    refused (M1 transfer contract, C7 gap (i)), so the page hears of it on the
    jobs channel. ``refuse`` receives each refusal that has no row of its own
    -- an identified request WG cannot accept, or a stale claim -- as
    :func:`inbox_refusal` builds it, and so does a conflict, whose row is
    someone else's.
    """

    def tell(row: Mapping[str, Any] | None) -> None:
        if publish is not None and row is not None:
            try:
                publish(row)
            except Exception:  # noqa: BLE001 - a notification never fails the delivery
                logger.debug("Could not publish a delivered operation.", exc_info=True)

    def refused(refusal: Mapping[str, Any]) -> None:
        if refuse is not None:
            try:
                refuse(refusal)
            except Exception:  # noqa: BLE001 - as above
                logger.debug("Could not report a refused delivery.", exc_info=True)

    _prune_acknowledgements_due(data_dir)
    with _DELIVERY_LOCK, _pinned_delivery_directories(data_dir) as (
        directories,
        dir_fds,
    ):
        deliveries = _deliveries(data_dir, dir_fds=dir_fds, directories=directories)
        # A claim that is gone -- acknowledged, or taken by another consumer --
        # waits for nothing any more.
        present = {delivery.path.name for delivery in deliveries if delivery.claimed}
        for name in set(_retention_waits) - present:
            del _retention_waits[name]
        for name in set(_unreadable_waits) - present:
            del _unreadable_waits[name]
        _refused_claims.intersection_update(present)
        for name in set(_pending_refusal_acks) - present:
            del _pending_refusal_acks[name]
        for name in set(_ack_failures) - present:
            del _ack_failures[name]
        if held is not None:
            # A pass can stop at an answer before it reaches a waiting claim;
            # that claim's operation is held all the same.
            held.update(operation_id for operation_id, _waited in _retention_waits.values())
        for delivery in deliveries:
            if delivery.claimed and delivery.path.name in _refused_claims:
                # Refused (or reported unreadable) already, and its file is
                # still here: only the delete is retried, and quietly. A
                # refusal whose acknowledgement is still owed writes it first.
                owed = _pending_refusal_acks.get(delivery.path.name)
                if owed is not None:
                    if not _ack_written(delivery.path.name, write_acknowledgement(
                        data_dir, owed[0], ACK_REFUSED, reason=owed[1],
                        manifest_sha256=owed[2], kind=owed[3],
                    )):
                        continue
                    del _pending_refusal_acks[delivery.path.name]
                _acknowledge(delivery.path, quiet=True, dir_fd=delivery.dir_fd)
                continue
            claim = (
                delivery.path
                if delivery.claimed
                else _claim(delivery.path, dir_fd=delivery.dir_fd)
            )
            if claim is None:
                continue
            # What the rename took is the request, not what was read before.
            payload = _read_payload(claim, dir_fd=delivery.dir_fd)
            if isinstance(payload, _UnsafePayload):
                invalid = payload.reason
                payload = None
            else:
                invalid = None
            if isinstance(payload, _Unreadable):
                # The read failed: this may be a valid request another process
                # holds. It is kept and read again next pass -- never refused
                # and deleted for it -- and said once it has stayed unreadable.
                name, waited = _unreadable_waits.get(claim.name, (delivery.path.name, 0))
                waited += 1
                _unreadable_waits[claim.name] = (name, waited)
                if waited == 1:
                    logger.info(
                        "Could not read the CAD Link request file %s (%s); it is kept and read again.",
                        name, payload.error,
                    )
                if waited == UNREADABLE_PASSES:
                    refusal = inbox_refusal(
                        None, name,
                        f"WG could not read this request file for {waited} passes ({payload.error}). "
                        "It is kept and read again; if it stays, close whatever holds it.",
                    )
                    logger.warning("CAD Link request file %s: %s", name, refusal["reason"])
                    refused(refusal)
                continue
            _unreadable_waits.pop(claim.name, None)
            if invalid is None:
                try:
                    command = _command_from_payload(
                        payload, claim, schema_versions=delivery.schema_versions
                    )
                    invalid = None if command is not None else UNREADABLE_CLAIM_REASON
                except InvalidRequest as exc:
                    command, invalid = None, str(exc)
            else:
                command = None
            if command is None:
                # Taken, refused and removed: never parked, never re-read.
                refusal = inbox_refusal(payload, delivery.path.name, invalid or UNREADABLE_CLAIM_REASON)
                logger.warning(
                    "Refused the CAD Link request file %s (%s): %s",
                    delivery.path.name, refusal["operationId"] or "no id", refusal["reason"],
                )
                refused(refusal)
                _retention_waits.pop(claim.name, None)
                # The refusal is published before the request is deleted, when
                # the request names an id to publish it under.
                # An id the store already holds is an operation with its own
                # acknowledgement: a refusal never speaks for it.
                refused_id = refusal["operationId"]
                if refused_id is not None and store.get_operation(refused_id) is None:
                    echoed = _echo(payload)
                    if not _ack_written(claim.name, write_acknowledgement(
                        data_dir, refused_id, ACK_REFUSED, reason=refusal["reason"],
                        manifest_sha256=echoed[0], kind=echoed[1],
                    )):
                        _pending_refusal_acks[claim.name] = (
                            refused_id, refusal["reason"], echoed[0], echoed[1],
                        )
                        _refused_claims.add(claim.name)
                        continue
                if not _acknowledge(claim, dir_fd=delivery.dir_fd):
                    # Held: reported once, the delete retried quietly each pass.
                    _refused_claims.add(claim.name)
                continue
            outdated = (
                isinstance(payload, Mapping)
                and payload.get("schemaVersion") not in CURRENT_SCHEMAS
            )
            if outdated and store.get_operation(command.command_id) is None:
                # An older add-in's command WG has never seen: refused, with
                # the remedy. One the store already holds is a repeat delivery
                # and is recovered or refused as any other.
                answer = _refuse_outdated(store, command)
                tell(store.get_operation(command.command_id))
                conflict_reason = None
            else:
                accepted = accept_delivery(
                    store, DeliveredItem.from_command(command), retain=retain
                )
                tell(accepted.row)
                conflict_reason = None
                if accepted.result == "conflict":
                    conflict = _delivery_conflict(accepted.row, accepted.digest)
                    conflict_reason = (
                        conflict["reason"] if conflict is not None
                        else "This id already names a request that is still running; this copy was not taken."
                    )
                    if claim.name not in _ack_failures:
                        # Reported once, not again while only its acknowledgement waits.
                        refused(inbox_refusal(payload, delivery.path.name, conflict_reason))
                answer = _file_answer(command, accepted)
                if (
                    accepted.retention == RETAIN_TRANSIENT
                    and _keep_for_retention(claim, command)
                ):
                    if held is not None:
                        held.add(command.command_id)
                    continue
            if _retention_waits.pop(claim.name, None) is not None and held is not None:
                # It waits no more: retained, never retainable, or at the bound.
                held.discard(command.command_id)
            if accept_solve is not None:
                accept_solve(command.command_id)
            # The outcome is published before the request is deleted. If it
            # cannot be written the claim stays, and the next poll recovers the
            # same operation and publishes then.
            if not _ack_written(
                claim.name, _publish_acknowledgement(data_dir, store, command)
            ):
                continue
            # A delete that fails leaves the claim for the next poll, which
            # recovers the same operation and answers it then.
            if _acknowledge(claim, dir_fd=delivery.dir_fd) and answer is not None:
                return {"command": command.payload(), "outcome": answer}
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
