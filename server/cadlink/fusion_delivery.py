"""How WG's requests reach the Fusion add-in, and what WG tells it it reads.

WG asks Fusion for two things: to export the active document back to WG (a
return request) and to open or update a completed export (a handoff). Each
request is its own file, ``.fusion-return-requests/<requestId>.json`` or
``.fusion-handoffs/<requestId>.json``, with ``schemaVersion`` 3. There is no
single-slot marker and no twin. WG and its add-in speak delivery version 3 and
nothing older: WG refuses an add-in whose heartbeat reports less, and installs
the add-in it ships (docs/architecture/CAD-OPERATIONS.md, "Delivery version").

Every request carries a ``deliverySequence``, one higher than any request of
that kind still on disk, which orders the requests without a clock. A publish
may withdraw earlier requests: ones that can never run, and ones a newer
request supersedes. The add-in claims a request by renaming it, so a request
still on disk under its own name has not started, and only such a request is
ever withdrawn.

At every start WG removes what a WG older than version 3 left in the folder --
the single slots, their records, and request files of another schema -- because
no add-in this WG talks to reads them. Then it writes ``wg-capabilities.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Callable, Literal, Mapping

from server.platform.private_paths import ensure_private_directory

from .live import wake as live_wake


IPC_SUBDIRECTORY = Path("ipc") / "wglink"
CAPABILITIES_FILENAME = "wg-capabilities.json"
CAPABILITIES_SCHEMA_VERSION = 1
# The one delivery version WG speaks, in both directions. The add-in reports
# its own in the heartbeat's ``deliveryVersion``; below this, WG refuses it.
DELIVERY_VERSION = 3
# The WG request inbox (M1 transfer contract, C3): 4 reads requests that name
# their kind, a Send as well as a Solve. It moves on its own; the heartbeat's
# delivery version and Fusion-bound request files stay 3, or every add-in
# reporting 3 would be refused and every Fusion-bound file rewritten.
SOLVE_COMMAND_DELIVERY = 4
FUSION_REQUEST_DELIVERY = DELIVERY_VERSION
SCHEMA_VERSION = DELIVERY_VERSION
# WG reads returns that require ``source-identity-v1`` (``wgreturn.py``). The
# add-in declares that feature only when WG advertises this; a WG that does not
# would refuse the bundle as an unknown required feature.
SOURCE_IDENTITY = 1
# WG serves the live session protocol, version 1, at the address in
# ``wg-endpoint.json`` (``server/cadlink/live``). The add-in goes live only when
# WG advertises this; otherwise it keeps to the files above.
LIVE_PROTOCOL = 1
SEQUENCE_FIELD = "deliverySequence"
# What a WG before delivery version 3 wrote beside the request folders.
LEGACY_SLOT_FILENAMES = (".fusion-return-request.json", ".fusion-handoff.json")
LEGACY_RECORD_FILENAME = ".legacy-slot.json"
# WG makes request ids itself (uuid4). Anything else is not a file name WG wrote.
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
# On Windows a replace fails while a reader holds the target open.
_WRITE_ATTEMPTS = 10
_WRITE_RETRY_SECONDS = 0.02
INSERT_HANDOFF_TTL = timedelta(minutes=30)

logger = logging.getLogger(__name__)
# One publisher per process at a time; the add-in is another process.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class FusionRequestChannel:
    """One kind of WG-to-Fusion request and the folder its files go in."""

    directory: str


RETURN_REQUESTS = FusionRequestChannel(".fusion-return-requests")
HANDOFFS = FusionRequestChannel(".fusion-handoffs")
CHANNELS = (RETURN_REQUESTS, HANDOFFS)


@dataclass(frozen=True)
class PublishedRequest:
    """One published request, and the unstarted ones it withdrew."""

    request_id: str
    path: Path
    withdrawn: tuple[str, ...] = ()
    recovered: bool = False


def ipc_folder(data_dir: Path, *, create: bool = False) -> Path:
    # Harden through the path as configured, so a symlinked data root is seen
    # and left alone; hand callers the resolved folder, as before.
    folder = Path(data_dir) / IPC_SUBDIRECTORY
    if create:
        ensure_private_directory(folder, parents=True, data_root=Path(data_dir))
    return folder.resolve()


def capabilities(*, solve_delivery: bool = True) -> dict[str, Any]:
    """What WG advertises. ``solve_delivery`` False: its request consumer is off,
    so it does not claim to read the inbox at all."""

    advertised: dict[str, Any] = {
        "schemaVersion": CAPABILITIES_SCHEMA_VERSION,
        "producer": "waveguide-generator",
        "solveCommandDelivery": SOLVE_COMMAND_DELIVERY,
        "fusionRequestDelivery": FUSION_REQUEST_DELIVERY,
        "sourceIdentity": SOURCE_IDENTITY,
        "liveProtocol": LIVE_PROTOCOL,
    }
    if not solve_delivery:
        del advertised["solveCommandDelivery"]
    return advertised


def addin_delivery_version(heartbeat: Mapping[str, Any]) -> int | None:
    """The delivery version an add-in's heartbeat reports, or None for none."""

    value = heartbeat.get("deliveryVersion")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _write_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    before_replace: Callable[[Mapping[str, Any]], object] | None = None,
    should_replace: Callable[[object], bool] | None = None,
    replace_failed: Callable[[object, BaseException], None] | None = None,
) -> tuple[object, bool]:
    """Replace ``path`` atomically; retry briefly while another process holds it.

    The staging name starts with "." and ends in .tmp. Only PermissionError is
    retried: that is what Windows raises while a reader has the file open.
    """

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name.lstrip('.')}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    keep_for_recovery = False
    receipt: object = None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        receipt = before_replace(payload) if before_replace is not None else None
        if should_replace is not None and not should_replace(receipt):
            return receipt, False
        for attempt in range(_WRITE_ATTEMPTS):
            try:
                os.replace(temporary, path)
                return receipt, True
            except PermissionError:
                if attempt + 1 == _WRITE_ATTEMPTS:
                    raise
                time.sleep(_WRITE_RETRY_SECONDS)
    except Exception as exc:
        if replace_failed is not None:
            try:
                replace_failed(receipt, exc)
            except Exception:
                # The staged document is durable recovery evidence. If the
                # compensating store write also fails, startup finishes this
                # publication instead of stranding a received row with no file.
                keep_for_recovery = True
                logger.warning(
                    "Could not compensate failed Fusion request publication %s.",
                    path.name,
                    exc_info=True,
                )
        raise
    finally:
        if not keep_for_recovery:
            temporary.unlink(missing_ok=True)


def _withdraw_and_record(
    path: Path,
    operation_id: str,
    record: Callable[[str], None],
) -> Literal["removed", "gone", "failed"]:
    """Hide a pending request, record cancellation, then delete it.

    If the durable write fails, restore the runnable file. A missing source
    means the add-in won the claim race and no cancellation is recorded.
    """

    held = path.with_name(
        f".wg-withdraw-{path.name}.{os.getpid()}-{time.monotonic_ns()}.tmp"
    )
    try:
        os.rename(path, held)
    except FileNotFoundError:
        return "gone"
    except OSError as exc:
        logger.warning("Could not hide Fusion request %s for withdrawal: %s", path.name, exc)
        return "failed"
    try:
        record(operation_id)
    except Exception:
        try:
            os.replace(held, path)
        except OSError:
            logger.error(
                "Could not restore Fusion request %s after its cancellation failed.",
                path.name,
                exc_info=True,
            )
        logger.warning(
            "Did not withdraw Fusion request %s because its cancellation was not stored.",
            path.name,
            exc_info=True,
        )
        return "failed"
    held.unlink(missing_ok=True)
    return "removed"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _sequence(payload: Any) -> int | None:
    value = payload.get(SEQUENCE_FIELD) if isinstance(payload, Mapping) else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


def _operation_id(payload: Any) -> str | None:
    value = payload.get("operationId") if isinstance(payload, Mapping) else None
    return value if isinstance(value, str) and _REQUEST_ID.fullmatch(value) else None


def _request_files(directory: Path) -> list[Path]:
    """The requests in a channel's folder: ``*.json`` names not starting with "."."""

    try:
        return [
            path for path in directory.iterdir()
            if path.suffix == ".json" and not path.name.startswith(".") and path.is_file()
        ]
    except OSError:
        return []


def _remove(path: Path) -> Literal["removed", "gone", "failed"]:
    """Delete ``path``; retry briefly while another process holds it (Windows).

    ``gone`` means somebody else took it first -- for a request, the add-in
    claimed it, so it has started.
    """

    for attempt in range(_WRITE_ATTEMPTS):
        try:
            path.unlink()
            return "removed"
        except FileNotFoundError:
            return "gone"
        except PermissionError as exc:
            failure: OSError = exc
            if attempt + 1 == _WRITE_ATTEMPTS:
                break
        except OSError as exc:
            failure = exc
            break
        time.sleep(_WRITE_RETRY_SECONDS)
    logger.warning("Could not remove the Fusion request %s: %s", path.name, failure)
    return "failed"


def publish_fusion_request(
    data_dir: Path,
    channel: FusionRequestChannel,
    payload: Mapping[str, Any],
    request_id: str,
    *,
    withdraw: Callable[[Mapping[str, Any]], bool] | None = None,
    before_publish: Callable[[Mapping[str, Any]], object] | None = None,
    publish_failed: Callable[[object, BaseException], None] | None = None,
    after_withdraw: Callable[[str], None] | None = None,
) -> PublishedRequest:
    """Publish one request as its own file.

    ``payload`` is the request without ``schemaVersion``, ``requestId``,
    ``operationId`` or ``deliverySequence``. ``withdraw`` names earlier requests
    in this channel to remove first: ones that can never run, or ones this
    request supersedes. Only a request still on disk under its own name is
    withdrawn -- the add-in has not claimed it, so it has not started -- and
    the ids withdrawn are returned.
    """

    if not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("A Fusion request id must be a plain file name.")
    directory = ipc_folder(data_dir, create=True) / channel.directory
    with _LOCK:
        ensure_private_directory(
            Path(data_dir) / IPC_SUBDIRECTORY / channel.directory, data_root=Path(data_dir)
        )
        withdrawn: list[str] = []
        sequences = [0]
        withdrawable: list[tuple[Path, str]] = []
        for path in _request_files(directory):
            existing = _read_json(path)
            if withdraw is not None and isinstance(existing, Mapping) and withdraw(existing):
                withdrawable.append((path, _operation_id(existing) or path.stem))
            sequences.append(_sequence(existing) or 0)
        own = directory / f"{request_id}.json"
        sequence = max(sequences) + 1
        document = {
            **payload,
            "schemaVersion": SCHEMA_VERSION,
            "requestId": request_id,
            "operationId": request_id,
            SEQUENCE_FIELD: sequence,
        }
        _receipt, replaced = _write_json(
            own,
            document,
            before_replace=before_publish,
            should_replace=lambda value: not (
                isinstance(value, tuple) and len(value) >= 2 and value[1] == "recovered"
            ),
            replace_failed=publish_failed,
        )
        if not replaced:
            return PublishedRequest(request_id=request_id, path=own, recovered=True)
        # Publish the replacement first. If recording or writing it fails, an
        # older runnable request is left untouched. While this lock is held the
        # add-in may still claim an older file; "gone" then correctly means it
        # started and must not be classified as withdrawn.
        for path, operation_id in withdrawable:
            if after_withdraw is None:
                outcome = _remove(path)
            else:
                outcome = _withdraw_and_record(path, operation_id, after_withdraw)
            if outcome != "removed":
                continue
            withdrawn.append(operation_id)
    # A live long poll may be waiting for exactly this (a hint; it rescans too).
    live_wake.notify(data_dir)
    # The request id is the operation id: it follows the request through the
    # add-in's heartbeat and outcomes (CAD-OPERATIONS.md, "WG-produced Fusion requests").
    logger.info(
        "Published Fusion request %s in %s (delivery sequence %d).",
        request_id, channel.directory, sequence,
    )
    return PublishedRequest(request_id=request_id, path=own, withdrawn=tuple(withdrawn))


def expire_unstarted_insert_handoffs(
    data_dir: Path,
    *,
    record_expired: Callable[[str], None],
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Withdraw unclaimed insert requests older than their delivery TTL."""

    checked_at = now or datetime.now(timezone.utc)
    directory = ipc_folder(data_dir) / HANDOFFS.directory
    expired: list[str] = []
    with _LOCK:
        for path in _request_files(directory):
            payload = _read_json(path)
            if not isinstance(payload, Mapping) or payload.get("expectedInstanceId"):
                continue
            try:
                requested_at = datetime.fromisoformat(
                    str(payload.get("requestedAt") or "").replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if requested_at.tzinfo is None or checked_at - requested_at <= INSERT_HANDOFF_TTL:
                continue
            operation_id = _operation_id(payload) or path.stem
            if _withdraw_and_record(path, operation_id, record_expired) != "removed":
                continue
            expired.append(operation_id)
            logger.info(
                "Expired unstarted Fusion insert %s after %s minutes.",
                operation_id,
                int(INSERT_HANDOFF_TTL.total_seconds() // 60),
            )
    return tuple(expired)


def recover_staged_fusion_requests(
    data_dir: Path,
    *,
    lookup_operation: Callable[[str], Mapping[str, Any] | None],
) -> tuple[str, ...]:
    """Finish files staged before a backend interruption.

    The JSON is durable before its operation is accepted. A received row plus
    this hidden file proves the interruption happened between acceptance and
    publication. Existing visible requests and add-in claims always win.

    A live claim hides a request under the same pattern,
    ``.<requestId>.json.live-<claimId>.tmp``, before it claims the operation
    in the store. Interrupted before that claim, the row is still ``received``
    and the request is restored; interrupted after it, the row is not, and the
    hidden file is deleted -- as is one whose operation was cancelled meanwhile
    (docs/reference/CADLINK-LIVE-PROTOCOL.md, section 7).
    """

    recovered: list[str] = []
    folder = ipc_folder(data_dir)
    with _LOCK:
        for channel in CHANNELS:
            directory = folder / channel.directory
            try:
                staged = [
                    path
                    for path in directory.iterdir()
                    if path.name.startswith(".") and path.name.endswith(".tmp")
                ]
            except OSError:
                continue
            for temporary in staged:
                payload = _read_json(temporary)
                operation_id = _operation_id(payload)
                if (
                    not isinstance(payload, Mapping)
                    or payload.get("schemaVersion") != SCHEMA_VERSION
                    or operation_id is None
                ):
                    continue
                row = lookup_operation(operation_id)
                own = directory / f"{operation_id}.json"
                claimed = any(directory.glob(f".wglink-claim-{operation_id}-*.json"))
                if (
                    own.exists()
                    or claimed
                    or row is None
                    or row.get("state") != "received"
                ):
                    temporary.unlink(missing_ok=True)
                    continue
                os.replace(temporary, own)
                recovered.append(operation_id)
                logger.info("Recovered staged Fusion request %s at startup.", operation_id)
    if recovered:
        live_wake.notify(data_dir)
    return tuple(recovered)


def _remove_older_delivery(folder: Path) -> list[str]:
    """Remove what a WG before delivery version 3 left for its add-in.

    The single slots, their records, and request files of another schema. No
    add-in this WG talks to runs any of them. The add-in's own claims
    (hidden files) are its to settle and are left alone.
    """

    removed: list[str] = []
    for name in LEGACY_SLOT_FILENAMES:
        if _remove(folder / name) == "removed":
            removed.append(name)
    for channel in CHANNELS:
        directory = folder / channel.directory
        if _remove(directory / LEGACY_RECORD_FILENAME) == "removed":
            removed.append(f"{channel.directory}/{LEGACY_RECORD_FILENAME}")
        for path in _request_files(directory):
            payload = _read_json(path)
            if isinstance(payload, Mapping) and payload.get("schemaVersion") == SCHEMA_VERSION:
                continue
            if _remove(path) == "removed":
                removed.append(f"{channel.directory}/{path.name}")
    return removed


def advertise_fusion_delivery(data_dir: Path, *, solve_delivery: bool = True) -> None:
    """Startup: remove an older WG's requests, then write the capability file.

    Neither failure stops WG. Without the file the add-in asks for WG to be
    updated and writes no solve command.
    """

    try:
        folder = ipc_folder(data_dir, create=True)
        with _LOCK:
            removed = _remove_older_delivery(folder)
        if removed:
            logger.info(
                "Removed %d Fusion request file(s) an older WG left, which no current "
                "add-in runs: %s",
                len(removed),
                ", ".join(sorted(removed)),
            )
        _write_json(folder / CAPABILITIES_FILENAME, capabilities(solve_delivery=solve_delivery))
    except OSError as exc:
        logger.warning("Could not advertise WG's Fusion delivery version: %s", exc)


__all__ = [
    "CAPABILITIES_FILENAME",
    "CHANNELS",
    "DELIVERY_VERSION",
    "FUSION_REQUEST_DELIVERY",
    "FusionRequestChannel",
    "HANDOFFS",
    "INSERT_HANDOFF_TTL",
    "LEGACY_RECORD_FILENAME",
    "LEGACY_SLOT_FILENAMES",
    "LIVE_PROTOCOL",
    "PublishedRequest",
    "RETURN_REQUESTS",
    "SCHEMA_VERSION",
    "SEQUENCE_FIELD",
    "SOLVE_COMMAND_DELIVERY",
    "SOURCE_IDENTITY",
    "addin_delivery_version",
    "advertise_fusion_delivery",
    "capabilities",
    "expire_unstarted_insert_handoffs",
    "publish_fusion_request",
    "recover_staged_fusion_requests",
]
