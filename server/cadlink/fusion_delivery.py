"""How WG's requests reach the Fusion add-in, and what WG tells it it reads.

WG asks Fusion for two things: to export the active document back to WG (a
return request) and to open or update a completed export (a handoff). Each
request is published twice under one request id:

- as its own file, ``.fusion-return-requests/<requestId>.json`` or
  ``.fusion-handoffs/<requestId>.json`` with ``schemaVersion`` 2, which an
  add-in that reads per-request files takes;
- in the legacy single slot, with ``schemaVersion`` 1 and an ``operationId``,
  which an add-in that predates per-request files reads. That add-in ignores
  the extra fields. An add-in that reads per-request files never runs a slot
  that names an ``operationId`` and never deletes it: it is a twin, not a
  second request, and only a legacy reader takes it.

Every request carries a ``deliverySequence``, one higher than any request of
that kind still on disk or recorded. The file is written first, then its twin,
then a WG-private record of the id and sequence now in the slot. WG brings a
lagging record up to the slot before it does anything else, and empties the
slot itself (retiring a twin whose file was taken) only once the record names
that twin. So an empty slot with the recorded request's file still present
means a legacy reader took that request. Its file, and every file whose
sequence is not above it, is discarded without running, so an upgraded add-in
never runs them again. The earlier ones had been replaced in the slot before
that reader looked, which is what always happened to them.

Separately, ``wg-capabilities.json`` tells the add-in which delivery versions
WG reads. The contract is docs/architecture/CAD-OPERATIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Callable, Mapping


IPC_SUBDIRECTORY = Path("ipc") / "wglink"
CAPABILITIES_FILENAME = "wg-capabilities.json"
CAPABILITIES_SCHEMA_VERSION = 1
# WG reads per-command solve files (.wg-solve-requests/<commandId>.json).
SOLVE_COMMAND_DELIVERY = 2
# WG publishes return requests and handoffs as per-request files with a twin.
FUSION_REQUEST_DELIVERY = 2
LEGACY_SCHEMA_VERSION = 1
SCHEMA_VERSION = 2
SEQUENCE_FIELD = "deliverySequence"
# The id and sequence of the twin WG last wrote to a channel's legacy slot. The
# leading "." keeps it out of the requests a reader takes.
SLOT_RECORD_FILENAME = ".legacy-slot.json"
# WG makes request ids itself (uuid4). Anything else is not a file name WG wrote.
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
# On Windows a replace fails while a reader holds the target open.
_WRITE_ATTEMPTS = 10
_WRITE_RETRY_SECONDS = 0.02

logger = logging.getLogger(__name__)
# One publisher per process at a time; the add-in is another process.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class FusionRequestChannel:
    """One kind of WG-to-Fusion request: its legacy slot and its folder."""

    legacy_filename: str
    directory: str


RETURN_REQUESTS = FusionRequestChannel(".fusion-return-request.json", ".fusion-return-requests")
HANDOFFS = FusionRequestChannel(".fusion-handoff.json", ".fusion-handoffs")
CHANNELS = (RETURN_REQUESTS, HANDOFFS)


def ipc_folder(data_dir: Path, *, create: bool = False) -> Path:
    folder = Path(data_dir).resolve() / IPC_SUBDIRECTORY
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def capabilities() -> dict[str, Any]:
    return {
        "schemaVersion": CAPABILITIES_SCHEMA_VERSION,
        "producer": "waveguide-generator",
        "solveCommandDelivery": SOLVE_COMMAND_DELIVERY,
        "fusionRequestDelivery": FUSION_REQUEST_DELIVERY,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Replace ``path`` atomically; retry briefly while another process holds it.

    The staging name starts with "." and ends in .tmp. Only PermissionError is
    retried: that is what Windows raises while a reader has the file open.
    """

    for attempt in range(_WRITE_ATTEMPTS):
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name.lstrip('.')}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt + 1 == _WRITE_ATTEMPTS:
                raise
        finally:
            temporary.unlink(missing_ok=True)
        time.sleep(_WRITE_RETRY_SECONDS)


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


def _remove(path: Path) -> bool:
    """Delete ``path``; retry briefly while another process holds it (Windows)."""

    for attempt in range(_WRITE_ATTEMPTS):
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except PermissionError as exc:
            failure: OSError = exc
            if attempt + 1 == _WRITE_ATTEMPTS:
                break
        except OSError as exc:
            failure = exc
            break
        time.sleep(_WRITE_RETRY_SECONDS)
    logger.warning("Could not remove the Fusion request %s: %s", path.name, failure)
    return False


def _write_record(directory: Path, request_id: str, sequence: int) -> bool:
    try:
        _write_json(
            directory / SLOT_RECORD_FILENAME,
            {"operationId": request_id, SEQUENCE_FIELD: sequence},
        )
    except OSError as exc:
        logger.warning("Could not record the %s slot: %s", directory.name, exc)
        return False
    return True


def _record_the_slot(folder: Path, channel: FusionRequestChannel) -> bool:
    """Make the record name the twin in the slot. True when it does, or there is none.

    The record lags the slot only when its write failed or WG stopped between
    the twin and the record. Until it is repaired, WG must not empty the slot:
    an empty slot under a lagging record reads as a legacy reader having taken
    the older request the record names.
    """

    slot = _read_json(folder / channel.legacy_filename)
    twin_id = _operation_id(slot)
    sequence = _sequence(slot)
    if twin_id is None or sequence is None:
        return True
    directory = folder / channel.directory
    record = _read_json(directory / SLOT_RECORD_FILENAME)
    recorded = _sequence(record)
    if _operation_id(record) == twin_id and recorded == sequence:
        return True
    if recorded is not None and recorded > sequence:
        # WG never records a twin before writing it; leave this state alone.
        return False
    try:
        directory.mkdir(exist_ok=True)
    except OSError:
        return False
    return _write_record(directory, twin_id, sequence)


def _discard_what_a_legacy_reader_ran(folder: Path, channel: FusionRequestChannel) -> list[str]:
    """Remove the files of requests a legacy reader already ran or never saw.

    Only when the record names a request, the slot is empty and that request's
    file is still there. That file goes, with every file whose sequence is not
    above it. A later request -- one WG may be writing now -- and a file
    without a sequence are kept.
    """

    directory = folder / channel.directory
    record = _read_json(directory / SLOT_RECORD_FILENAME)
    twin_id = _operation_id(record)
    last = _sequence(record)
    if twin_id is None or last is None:
        return []
    if os.path.lexists(folder / channel.legacy_filename):
        return []
    twin = directory / f"{twin_id}.json"
    if not twin.is_file():
        return []
    discarded: list[str] = []
    for path in _request_files(directory):
        sequence = _sequence(_read_json(path))
        taken = path == twin or (sequence is not None and sequence <= last)
        if taken and _remove(path):
            discarded.append(path.stem)
    if discarded:
        logger.info(
            "Discarded %d %s request(s) an add-in without per-request files already took or "
            "never saw.",
            len(discarded),
            channel.directory,
        )
    return discarded


def _retire_taken_twin(folder: Path, channel: FusionRequestChannel) -> bool:
    """Remove a twin whose file a per-request reader has already taken.

    Such a reader never deletes the slot. The caller has made the record name
    this twin first, and WG is the slot's only writer.
    """

    slot = folder / channel.legacy_filename
    twin_id = _operation_id(_read_json(slot))
    if twin_id is None or os.path.lexists(folder / channel.directory / f"{twin_id}.json"):
        return False
    return _remove(slot)


def publish_fusion_request(
    data_dir: Path,
    channel: FusionRequestChannel,
    payload: Mapping[str, Any],
    request_id: str,
    *,
    withdraw: Callable[[Mapping[str, Any]], bool] | None = None,
) -> Path:
    """Publish one request as its own file and as its legacy twin.

    ``payload`` is the request without ``schemaVersion``, ``requestId``,
    ``operationId`` or ``deliverySequence``. ``withdraw`` names earlier
    requests in this channel that can never run any more; their files are
    removed first. Returns the legacy slot's path. If the twin cannot be
    written, the request's file is removed again and the error propagates.
    """

    if not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("A Fusion request id must be a plain file name.")
    folder = ipc_folder(data_dir, create=True)
    directory = folder / channel.directory
    slot = folder / channel.legacy_filename
    with _LOCK:
        directory.mkdir(exist_ok=True)
        _record_the_slot(folder, channel)
        _discard_what_a_legacy_reader_ran(folder, channel)
        published = [0]
        for path in _request_files(directory):
            existing = _read_json(path)
            if withdraw is not None and isinstance(existing, Mapping) and withdraw(existing):
                _remove(path)
            published.append(_sequence(existing) or 0)
        published.append(_sequence(_read_json(directory / SLOT_RECORD_FILENAME)) or 0)
        published.append(_sequence(_read_json(slot)) or 0)
        sequence = max(published) + 1
        body = {
            **payload,
            "requestId": request_id,
            "operationId": request_id,
            SEQUENCE_FIELD: sequence,
        }
        own = directory / f"{request_id}.json"
        _write_json(own, {**body, "schemaVersion": SCHEMA_VERSION})
        try:
            _write_json(slot, {**body, "schemaVersion": LEGACY_SCHEMA_VERSION})
        except BaseException:
            _remove(own)
            raise
        # The request is delivered. Without the record, this request's file
        # can outlive a legacy reader taking its twin; the next publish or
        # start repairs the record from the slot.
        _write_record(directory, request_id, sequence)
    return slot


def advertise_fusion_delivery(data_dir: Path) -> None:
    """Startup: tidy each channel, then write the capability file.

    Neither failure stops WG. Without the file an add-in keeps writing the
    legacy solve slot, which WG still reads.
    """

    try:
        folder = ipc_folder(data_dir, create=True)
        with _LOCK:
            for channel in CHANNELS:
                recorded = _record_the_slot(folder, channel)
                _discard_what_a_legacy_reader_ran(folder, channel)
                if recorded:
                    _retire_taken_twin(folder, channel)
        _write_json(folder / CAPABILITIES_FILENAME, capabilities())
    except OSError as exc:
        logger.warning("Could not advertise WG's Fusion delivery versions: %s", exc)


__all__ = [
    "CAPABILITIES_FILENAME",
    "CHANNELS",
    "FUSION_REQUEST_DELIVERY",
    "FusionRequestChannel",
    "HANDOFFS",
    "RETURN_REQUESTS",
    "SEQUENCE_FIELD",
    "SLOT_RECORD_FILENAME",
    "SOLVE_COMMAND_DELIVERY",
    "advertise_fusion_delivery",
    "capabilities",
    "publish_fusion_request",
]
