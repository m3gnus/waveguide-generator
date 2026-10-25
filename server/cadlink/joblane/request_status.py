"""What became of each Fusion request, for Fusion: the request-status file.

Next to each request's schema-1 receipt (``.wg-solve-acks/<id>.json``, which
only says the delivery was accepted or refused) WG keeps
``<id>.status.json``: the request's state as its one job owner sees it --
preparing, needing the user, queued, solving, solved, could not finish, or
cancelled. The contract (fields, validator, terminal states and every word a
user reads) is WGLink's shared protocol, vendored as
``server.cadlink.wglink_protocol``; docs/architecture/CAD-OPERATIONS.md,
"Request status file", is the WG side of it.

Two halves:

* :func:`project_request_status` is pure: a receipt, the operation row, the
  latest job of the request (``JobStore.latest_cad_job``) and that job's read
  model (``job_cad_state``) give one record. It owns no vocabulary of its own:
  holds and failed preparations come from ``server.cadlink.operations``
  through the job read model.
* :class:`RequestStatusPublisher` keeps the files current from the delivery
  loop. It follows the jobs event stream (``JobStore.replay_events``) and
  re-projects only the requests whose jobs changed; a cursor that fell out of
  the retained events, or a WG start, reconciles every request with a receipt.
  Progress while solving is written at most every :data:`PROGRESS_INTERVAL_S`;
  every change of state is written at once. WG's own failures to finish a
  solve are recorded once for the developers (``record_incident``) when the
  request first reads ``couldNotFinish``; failed preparations were recorded
  where they were diagnosed. The file never carries either.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import json
import logging
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from server.cadlink.identity import utc_now
from server.cadlink.ipc import IPC_SUBDIRECTORY
from server.cadlink.operations import (
    ATTENTION_FAILED,
    PREPARE_AND_SOLVE,
    RECEIVE_SNAPSHOT,
    effective_reason_code,
)
from server.cadlink.wglink_protocol import (
    REQUEST_STATUS_CONTRACT,
    REQUEST_STATUS_SCHEMA_VERSION,
    REQUEST_STATUS_SUFFIX,
    request_status_terminal,
)
from server.cadlink.intake.ingest import SOLVED_DOMAIN_BY_PLANES
from server.cadlink.intake.solve_command import (
    ACK_REFUSED,
    ACK_SCHEMA_VERSION,
    SOLVE_ACKS_DIRECTORY,
    _OPERATION_ID,
    publish_record,
)
from server.jobs.cad_preparation import job_cad_state
from server.platform.incidents import record_incident
from server.solver.imported import imported_domain_planes

if TYPE_CHECKING:
    from server.cadlink.store import CadLinkStore


logger = logging.getLogger(__name__)

#: A request that is solving is rewritten at most this often for progress alone.
PROGRESS_INTERVAL_S = 2.0
#: Every request still under way is re-projected this often, whatever the event
#: stream said: a change no job event announces (an operation row) still lands.
SWEEP_INTERVAL_S = 30.0
#: How many jobs that are not a Fusion request's are remembered as such.
_MAX_OTHER_JOBS = 4096
#: Fields that change on every write and say nothing about the request.
_VOLATILE_FIELDS = ("seq", "updatedAt")


def status_path(directory: Path, request_id: str) -> Path:
    return directory / f"{request_id}{REQUEST_STATUS_SUFFIX}"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _document_name(operation: Mapping[str, Any] | None, cad: Mapping[str, Any] | None) -> str | None:
    snapshot = (cad or {}).get("snapshot")
    name = snapshot.get("document_name") if isinstance(snapshot, Mapping) else None
    if not name and operation is not None:
        try:
            stored = json.loads(operation.get("snapshot_json") or "{}")
        except (TypeError, ValueError):
            stored = {}
        name = stored.get("document_name") if isinstance(stored, Mapping) else None
    return str(name).strip() or None if name else None


def _is_bound(job: Mapping[str, Any]) -> bool:
    """A job made from the request's prepared mesh (not its preparing intent)."""

    config = job.get("config_json")
    return not (isinstance(config, Mapping) and config.get("type") == "cad_intent")


def _cad(job: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = job.get("task_metadata")
    cad = metadata.get("cad") if isinstance(metadata, Mapping) else None
    return cad if isinstance(cad, Mapping) else {}


def _job_ref(job: Mapping[str, Any]) -> dict[str, Any]:
    run = job.get("run_number")
    return {"jobId": str(job["id"]),
            "runNumber": run if isinstance(run, int) and not isinstance(run, bool) and run > 0 else None}


def _solved_mesh(job: Mapping[str, Any], ingest: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """What the solve meshed: its triangle count and the part of the model it is."""

    stats = job.get("mesh_stats")
    triangles = stats.get("triangle_count") if isinstance(stats, Mapping) else None
    if isinstance(triangles, bool) or not isinstance(triangles, int) or triangles <= 0:
        return None
    mesh: dict[str, Any] = {"trianglesSolved": triangles}
    if ingest is None:
        return None
    planes = len(imported_domain_planes(ingest))
    if planes not in SOLVED_DOMAIN_BY_PLANES:
        return None
    mesh["domainKind"], mesh["solvedFraction"] = SOLVED_DOMAIN_BY_PLANES[planes], 0.5 ** planes
    return mesh


def _notice(ingest: Mapping[str, Any] | None) -> str | None:
    """The calm line a solved request adds: the symmetry fallback, or a repaired mesh."""

    findings = (ingest or {}).get("findings")
    if not isinstance(findings, list):
        return None
    kinds = {str(item.get("kind")): item for item in findings if isinstance(item, Mapping)}
    fallback = kinds.get("symmetry-cut-unverified")
    if fallback is not None:
        return "fullModel" if fallback.get("solved_domain") == "full" else "cutModel"
    return "healed" if "healing-performed" in kinds else None


def _cancel_reason(job: Mapping[str, Any]) -> str:
    cad = _cad(job)
    refusal = cad.get("refusal") if isinstance(cad.get("refusal"), Mapping) else {}
    if cad.get("superseded_by") or refusal.get("code") == "superseded":
        return "replaced"
    return "stopped"


def project_request_status(
    receipt: Mapping[str, Any],
    operation: Mapping[str, Any] | None,
    job: Mapping[str, Any] | None,
    cad: Mapping[str, Any] | None,
    *,
    ingest: Mapping[str, Any] | None = None,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The status record of one request, without ``seq`` and ``updatedAt``.

    ``receipt`` is the request's schema-1 receipt; ``operation`` its row;
    ``job`` the latest job of its chain (``JobStore.latest_cad_job``) and
    ``cad`` that job's read model (``job_cad_state``); ``ingest`` the record
    of the mesh a solved job used; ``previous`` the status last written. A
    Solve whose job is gone was dismissed in WG (or its run removed): it keeps
    a finished state it already had, and otherwise reads ``cancelled``.
    """

    request_id = str(receipt.get("commandId") or receipt.get("operationId"))
    kind = receipt.get("kind") or (operation or {}).get("kind") or PREPARE_AND_SOLVE
    record: dict[str, Any] = {
        "contract": REQUEST_STATUS_CONTRACT,
        "schemaVersion": REQUEST_STATUS_SCHEMA_VERSION,
        "requestId": request_id,
        "operationId": request_id,
        "kind": kind,
        "manifestSha256": receipt.get("manifestSha256"),
        "documentName": _document_name(operation, cad),
    }
    if receipt.get("outcome") == ACK_REFUSED:
        return {**record, "state": "refused"}
    if kind == RECEIVE_SNAPSHOT:
        return {**record, "state": "accepted"}
    if job is None:
        if previous is not None and request_status_terminal(previous) and previous.get("state") != "refused":
            kept = {key: value for key, value in previous.items() if key not in _VOLATILE_FIELDS}
            return {**kept, **{key: value for key, value in record.items() if value is not None}}
        return {**record, "state": "cancelled", "cancelReason": "dismissed"}
    record["job"] = _job_ref(job)
    status = str(job.get("status"))
    cad = cad or {}
    if status == "preparing":
        return {**record, "state": "preparing"}
    if status == "queued":
        return {**record, "state": "queued"}
    if status == "running":
        progress = job.get("progress")
        fraction = float(progress) if isinstance(progress, (int, float)) and not isinstance(progress, bool) else 0.0
        return {**record, "state": "solving", "progress": {"fraction": min(max(fraction, 0.0), 1.0)}}
    if status == "complete":
        result: dict[str, Any] = dict(record["job"])
        mesh = _solved_mesh(job, ingest)
        if mesh is not None:
            result["mesh"] = mesh
        solved = {**record, "state": "solved", "result": result}
        notice = _notice(ingest)
        return {**solved, "notice": notice} if notice else solved
    if status == "cancelled":
        return {**record, "state": "cancelled", "cancelReason": _cancel_reason(job)}
    if cad.get("held"):
        refusal = _cad(job).get("refusal") if isinstance(_cad(job).get("refusal"), Mapping) else {}
        code = effective_reason_code(refusal.get("code"), refusal.get("message")) or None
        options = [{"id": str(option.get("id")), "label": str(option.get("label"))}
                   for option in cad.get("options") or [] if isinstance(option, Mapping)]
        return {**record, "state": "needsYou",
                "prompt": {"code": code, "nextStep": cad.get("next_step"), "options": options,
                           "answerIn": "wg"}}
    # Anything else stopped short of a result: WG's own failure (recorded for
    # the developers, worded by the protocol) or one the user can act on (WG's
    # own words for it, from the read model).
    phase = "solving" if _is_bound(job) else "preparing"
    has_refusal = isinstance(_cad(job).get("refusal"), Mapping)
    internal = not has_refusal or cad.get("attention") == ATTENTION_FAILED
    failure: dict[str, Any] = {"recorded": internal}
    if not internal:
        failure.update(message=cad.get("message"), nextStep=cad.get("next_step"))
    return {**record, "state": "couldNotFinish", "phase": phase, "failure": failure}


def _needs_incident(job: Mapping[str, Any] | None) -> bool:
    """A job that failed with no diagnosis of its own: the solve itself failed."""

    return (job is not None and str(job.get("status")) == "error"
            and not isinstance(_cad(job).get("refusal"), Mapping))


def _comparable(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {key: value for key, value in record.items() if key not in _VOLATILE_FIELDS}


class RequestStatusPublisher:
    """Keeps every request's status file current. One per application.

    :meth:`tick` is called from the delivery loop, off the event loop, one call
    at a time. ``clock`` is monotonic seconds (injectable for tests).
    """

    def __init__(
        self,
        data_dir: Path,
        store: CadLinkStore,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.directory = Path(data_dir) / IPC_SUBDIRECTORY / SOLVE_ACKS_DIRECTORY
        self.store = store
        self._clock = clock
        self._cursor: int | None = None
        self._folder_stamp: int | None = None
        self._last_sweep: float | None = None
        self._receipts: dict[str, dict[str, Any]] = {}
        self._records: dict[str, dict[str, Any]] = {}
        self._job_requests: dict[str, str] = {}
        self._other_jobs: set[str] = set()
        self._written_at: dict[str, float] = {}
        self._deferred: set[str] = set()
        self._dirty: set[str] = set()

    # -- what to look at -------------------------------------------------------

    def _scan(self) -> None:
        """Learn new receipts and forget pruned ones, from the folder's names."""

        try:
            names = {entry.name for entry in os.scandir(self.directory) if entry.is_file()}
        except OSError:
            names = set()
        receipts = {
            name.removesuffix(".json") for name in names
            if name.endswith(".json") and not name.endswith(REQUEST_STATUS_SUFFIX)
            and not name.startswith(".") and _OPERATION_ID.fullmatch(name.removesuffix(".json"))
        }
        for request_id in set(self._receipts) - receipts:
            self._forget(request_id)
        for request_id in receipts - set(self._receipts):
            receipt = _read_json(self.directory / f"{request_id}.json")
            if receipt is None or receipt.get("schemaVersion") != ACK_SCHEMA_VERSION:
                continue
            self._receipts[request_id] = receipt
            previous = _read_json(status_path(self.directory, request_id))
            if previous is not None and previous.get("contract") == REQUEST_STATUS_CONTRACT:
                self._records[request_id] = previous
            self._dirty.add(request_id)
        # A receipt rewritten in place (a redelivery) is re-read when its job changes.

    def _forget(self, request_id: str) -> None:
        self._receipts.pop(request_id, None)
        self._records.pop(request_id, None)
        self._written_at.pop(request_id, None)
        self._deferred.discard(request_id)
        self._dirty.discard(request_id)
        for job_id in [job for job, request in self._job_requests.items() if request == request_id]:
            del self._job_requests[job_id]

    def _folder_changed(self) -> bool:
        try:
            stamp = os.stat(self.directory).st_mtime_ns
        except OSError:
            stamp = None
        changed = stamp != self._folder_stamp or self._folder_stamp is None
        self._folder_stamp = stamp
        return changed

    def _follow_events(self, job_store: Any) -> None:
        """Mark the requests whose jobs changed since the last tick."""

        if self._cursor is None:
            self._cursor = job_store.current_event_cursor()
            self._dirty.update(self._receipts)  # a start: reconcile everything
            return
        events = job_store.replay_events(self._cursor)
        if events is None:
            # The cursor fell out of the retained events: reconcile everything.
            self._cursor = job_store.current_event_cursor()
            self._dirty.update(self._receipts)
            return
        changed: set[str] = set()
        for event in events:
            self._cursor = max(self._cursor, int(event.get("cursor") or 0))
            if event.get("jobId"):
                changed.add(str(event["jobId"]))
        for job_id in changed:
            if job_id in self._other_jobs:
                continue
            request_id = self._job_requests.get(job_id)
            if request_id is None:
                row = job_store.get_job_row(job_id)
                if row is None:
                    continue
                metadata = row.get("task_metadata")
                cad = metadata.get("cad") if isinstance(metadata, Mapping) else None
                config = row.get("config_json")
                request_id = (cad or {}).get("operation_id") if isinstance(cad, Mapping) else None
                if not request_id and isinstance(config, Mapping) and config.get("type") == "cad_intent":
                    request_id = config.get("operation_id")
                if not isinstance(request_id, str) or request_id not in self._receipts:
                    # Not a Fusion request's job (a solve started in WG, say):
                    # its progress events are not looked up again. Projecting a
                    # request maps its jobs explicitly, whatever this says.
                    if len(self._other_jobs) >= _MAX_OTHER_JOBS:
                        self._other_jobs.clear()
                    self._other_jobs.add(job_id)
                    continue
                self._job_requests[job_id] = request_id
            self._dirty.add(request_id)

    # -- one request -----------------------------------------------------------

    def _ingest_record(self, job: Mapping[str, Any], cad: Mapping[str, Any] | None) -> dict[str, Any] | None:
        config = job.get("config_json")
        geometry = config.get("geometry") if isinstance(config, Mapping) else None
        ingest_id = geometry.get("ingest_id") if isinstance(geometry, Mapping) else None
        if not ingest_id:
            ingest_id = ((cad or {}).get("preparation") or {}).get("ingest_id")
        if not ingest_id:
            return None
        row = self.store.get_ingest(str(ingest_id))
        try:
            record = json.loads(row["record_json"]) if row else None
        except (TypeError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    def _project(self, request_id: str, job_store: Any) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
        receipt = self._receipts[request_id]
        operation = self.store.get_operation(request_id)
        job = None
        if job_store is not None and receipt.get("kind", PREPARE_AND_SOLVE) == PREPARE_AND_SOLVE \
                and receipt.get("outcome") != ACK_REFUSED:
            job = job_store.latest_cad_job(
                request_id, receipt.get("jobId") or (operation or {}).get("job_id"),
            )
        cad = job_cad_state(job) if job is not None else None
        ingest = self._ingest_record(job, cad) if job is not None and job.get("status") == "complete" else None
        if job is not None:
            self._job_requests[str(job["id"])] = request_id
            self._other_jobs.discard(str(job["id"]))
        record = project_request_status(
            receipt, operation, job, cad, ingest=ingest, previous=self._records.get(request_id),
        )
        return record, job

    def _publish(self, request_id: str, job_store: Any, now: float) -> bool:
        if job_store is None and self._receipts[request_id].get("kind", PREPARE_AND_SOLVE) == PREPARE_AND_SOLVE \
                and self._receipts[request_id].get("outcome") != ACK_REFUSED:
            return False  # nothing to say about a Solve before the jobs runtime is up
        record, job = self._project(request_id, job_store)
        previous = self._records.get(request_id)
        if _comparable(record) == _comparable(previous):
            self._deferred.discard(request_id)
            return False
        if (previous is not None and previous.get("state") == record["state"] == "solving"
                and now - self._written_at.get(request_id, float("-inf")) < PROGRESS_INTERVAL_S):
            self._deferred.add(request_id)
            return False
        seq = previous.get("seq") if previous is not None else 0
        written = {**record, "seq": (seq if isinstance(seq, int) and not isinstance(seq, bool) else 0) + 1,
                   "updatedAt": utc_now()}
        try:
            publish_record(status_path(self.directory, request_id), written)
        except OSError as exc:
            logger.debug("Could not write the status of request %s: %s", request_id, exc)
            self._dirty.add(request_id)  # tried again on the next tick
            return False
        self._deferred.discard(request_id)
        self._records[request_id] = written
        self._written_at[request_id] = now
        if written["state"] == "couldNotFinish" and (previous or {}).get("state") != "couldNotFinish" \
                and _needs_incident(job):
            self._record_solve_failure(request_id, job, written)
        return True

    @staticmethod
    def _record_solve_failure(request_id: str, job: Mapping[str, Any], record: Mapping[str, Any]) -> None:
        """A solve of a Fusion request ended in an error: WG's own failure, for the developers."""

        record_incident(
            "cad-solve-failure", "cad-solve",
            ids={"job_id": job.get("id"), "operation_id": request_id, "request_id": request_id,
                 "run_number": job.get("run_number"), "manifest_sha256": record.get("manifestSha256")},
            evidence={"stage": job.get("stage"), "stage_message": job.get("stage_message"),
                      "error": job.get("error_message"), "bound": _is_bound(job)},
            redaction_names=[str(record["documentName"])] if record.get("documentName") else [],
        )

    # -- the loop's step -------------------------------------------------------

    def tick(self, job_store: Any) -> int:
        """One step: learn, follow, publish. Returns how many files were written."""

        now = self._clock()
        sweep = self._last_sweep is None or now - self._last_sweep >= SWEEP_INTERVAL_S
        if sweep or self._folder_changed():
            self._scan()
        if job_store is not None:
            self._follow_events(job_store)
        if sweep:
            self._last_sweep = now
            self._dirty.update(
                request_id for request_id in self._receipts
                if request_id not in self._records or not request_status_terminal(self._records[request_id])
            )
        due = {request_id for request_id in self._deferred
               if now - self._written_at.get(request_id, float("-inf")) >= PROGRESS_INTERVAL_S}
        pending, self._dirty = self._dirty | due, set()
        written = 0
        for request_id in sorted(pending):
            if request_id not in self._receipts:
                continue
            try:
                written += self._publish(request_id, job_store, now)
            except Exception:  # noqa: BLE001 - one request never stops the others
                logger.debug("Could not project the status of request %s.", request_id, exc_info=True)
                self._dirty.add(request_id)
        return written

    def tracked(self) -> Iterable[str]:
        return tuple(self._receipts)
