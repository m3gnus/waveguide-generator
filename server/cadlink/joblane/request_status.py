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
  through the job read model. Only the failures the user can act on
  (``operations.USER_WORDED_FAILURE_CODES``) carry WG's words; every other
  one reads as WG's own failure, recorded for the developers.
* :class:`RequestStatusPublisher` keeps the files current from the delivery
  loop. It follows the jobs event stream (``JobStore.replay_events``) and
  re-projects only the requests whose jobs changed; a cursor that fell out of
  the retained events, or a WG start, reconciles every request with a receipt.
  Every record it writes is the protocol validator's own reading of it, so
  the file holds nothing a reader would drop. Progress while solving is
  written at most every :data:`PROGRESS_INTERVAL_S`; every change of state at
  once. A failure no diagnosis recorded is recorded for the developers
  (``record_incident``) when the publisher sees the request change into it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Any

from server.cadlink.identity import utc_now
from server.cadlink.intake.ingest import SOLVED_DOMAIN_BY_PLANES
from server.cadlink.intake.solve_command import (
    ACK_REFUSED,
    ACK_SCHEMA_VERSION,
    SOLVE_ACKS_DIRECTORY,
    _OPERATION_ID,
    publish_record,
    request_kind,
)
from server.cadlink.ipc import IPC_SUBDIRECTORY
from server.cadlink.operations import (
    CANCELLED,
    INTERNAL_FAILURE_CODES,
    PREPARE_AND_SOLVE,
    RECEIVE_SNAPSHOT,
    USER_WORDED_FAILURE_CODES,
    effective_reason_code,
)
from server.cadlink.wglink_protocol import (
    REQUEST_STATUS_CONTRACT,
    REQUEST_STATUS_SCHEMA_VERSION,
    REQUEST_STATUS_SEQ_LIMIT,
    REQUEST_STATUS_SUFFIX,
    request_status_terminal,
    user_text_violations,
    validate_request_status,
)
from server.jobs.cad_intent import cad_of, intent_of
from server.jobs.cad_preparation import job_cad_state
from server.platform.incidents import record_incident
from server.solver.imported import imported_domain_planes

if TYPE_CHECKING:
    from server.cadlink.store import CadLinkStore


logger = logging.getLogger(__name__)

#: A request that is solving is rewritten at most this often for progress alone.
PROGRESS_INTERVAL_S = 2.0
#: Every request still under way is re-projected this often, whatever the event
#: stream said: a change no job event announces (an operation row) still lands,
#: and so does a receipt a folder with a coarse modification time hid.
SWEEP_INTERVAL_S = 30.0
#: After this many consecutive failed writes of one request's status, the
#: failure is recorded for the developers (once; about half a minute).
WRITE_FAILURE_INCIDENT_AFTER = 30
#: How many jobs that are not a Fusion request's are remembered as such.
_MAX_OTHER_JOBS = 4096
#: Fields that change on every write and say nothing about the request.
_VOLATILE_FIELDS = ("seq", "updatedAt")


def status_path(directory: Path, request_id: str) -> Path:
    return directory / f"{request_id}{REQUEST_STATUS_SUFFIX}"


def _wall_ms() -> int:
    return int(time.time() * 1000)


def _next_seq(previous: Mapping[str, Any] | None) -> int:
    """The next write's ``seq``: above the last, never below the clock; never stuck.

    At the protocol's limit it stays just below it, which a reader never takes
    for an older write (it ignores only a lower ``seq``).
    """

    seq = max(((previous or {}).get("seq") or 0) + 1, _wall_ms())
    return min(seq, REQUEST_STATUS_SEQ_LIMIT - 1)


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
    return (str(name).strip() or None) if name else None


def _is_bound(job: Mapping[str, Any]) -> bool:
    """A job made from the request's prepared mesh, not its preparing intent."""

    try:
        return intent_of(job) is None
    except ValueError:
        return False  # an intent, if an incomplete one


def _job_ref(job: Mapping[str, Any]) -> dict[str, Any]:
    run = job.get("run_number")
    return {"jobId": str(job["id"]),
            "runNumber": run if isinstance(run, int) and not isinstance(run, bool) and run > 0 else None}


def _solved_mesh(job: Mapping[str, Any], ingest: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """What the solve meshed: its triangle count and the part of the model it is."""

    stats = job.get("mesh_stats")
    triangles = stats.get("triangle_count") if isinstance(stats, Mapping) else None
    if ingest is None or isinstance(triangles, bool) or not isinstance(triangles, int) or triangles <= 0:
        return None
    planes = len(imported_domain_planes(ingest))
    if planes not in SOLVED_DOMAIN_BY_PLANES:
        return None
    return {"trianglesSolved": triangles, "domainKind": SOLVED_DOMAIN_BY_PLANES[planes],
            "solvedFraction": 0.5 ** planes}


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
    cad = cad_of(job)
    refusal = cad.get("refusal") if isinstance(cad.get("refusal"), Mapping) else {}
    if cad.get("superseded_by") or refusal.get("code") == "superseded":
        return "replaced"
    return "stopped"


def _failure(job: Mapping[str, Any], cad: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """A failure's record, and the incident owed for it (None: none, or one already exists).

    ``recorded`` is true only when the failure has a developer record: WG's
    own preparation failures were recorded where they were diagnosed; any
    other failure the user cannot act on is recorded through the returned
    incident. Failures the user can act on carry WG's own words, unless
    those read as internal detail -- then they are a recorded failure too.
    """

    refusal = cad_of(job).get("refusal")
    if not isinstance(refusal, Mapping):
        kind = "cad-solve-failure" if _is_bound(job) else "cad-preparation-failure"
        return {"recorded": True}, {"kind": kind, "code": None}
    code = effective_reason_code(refusal.get("code"), refusal.get("message"))
    if code in INTERNAL_FAILURE_CODES:
        return {"recorded": True}, None
    if code in USER_WORDED_FAILURE_CODES:
        message, next_step = cad.get("message"), cad.get("next_step")
        words = " ".join(str(part) for part in (message, next_step) if part)
        # The document's own name ("Gate horn") is never internal detail.
        name = (cad.get("snapshot") or {}).get("document_name") if isinstance(cad.get("snapshot"), Mapping) else None
        if message and not user_text_violations(words, names=[name]):
            return {"recorded": False, "message": message, "nextStep": next_step}, None
    return {"recorded": True}, {"kind": "cad-unclassified-failure", "code": code}


def project_request_status(
    receipt: Mapping[str, Any],
    operation: Mapping[str, Any] | None,
    job: Mapping[str, Any] | None,
    cad: Mapping[str, Any] | None,
    *,
    ingest: Mapping[str, Any] | None = None,
    previous: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The status record of one request (without ``seq``/``updatedAt``), and any incident owed.

    ``receipt`` is the request's schema-1 receipt; ``operation`` its row;
    ``job`` the latest job of its chain (``JobStore.latest_cad_job``) and
    ``cad`` that job's read model (``job_cad_state``); ``ingest`` the record
    of the mesh a solved job used; ``previous`` the status last written, as
    the validator read it. A Solve whose job is gone was dismissed in WG (or
    its run removed): it keeps a finished state it already had, without the
    run it no longer has, and otherwise reads ``cancelled``.
    """

    request_id = str(receipt.get("commandId") or receipt.get("operationId"))
    record: dict[str, Any] = {
        "contract": REQUEST_STATUS_CONTRACT,
        "schemaVersion": REQUEST_STATUS_SCHEMA_VERSION,
        "requestId": request_id,
        "operationId": request_id,
        "kind": request_kind(receipt.get("kind"), (operation or {}).get("kind")),
        "manifestSha256": receipt.get("manifestSha256"),
        "documentName": _document_name(operation, cad),
    }
    if receipt.get("outcome") == ACK_REFUSED:
        if (operation or {}).get("state") == CANCELLED:
            # Cancelled before admission: the receipt says refused, the user stopped it.
            return {**record, "state": "cancelled", "cancelReason": "stopped"}, None
        return {**record, "state": "refused"}, None
    if record["kind"] == RECEIVE_SNAPSHOT:
        return {**record, "state": "accepted"}, None
    if job is None:
        if previous is not None and request_status_terminal(previous) and previous.get("state") != "refused":
            kept = {key: value for key, value in previous.items()
                    if key not in (*_VOLATILE_FIELDS, "job", "result")}
            return {**kept, **{key: value for key, value in record.items() if value is not None}}, None
        return {**record, "state": "cancelled", "cancelReason": "dismissed"}, None
    record["job"] = _job_ref(job)
    status = str(job.get("status"))
    cad = cad or {}
    if status == "preparing":
        return {**record, "state": "preparing"}, None
    if status == "queued":
        return {**record, "state": "queued"}, None
    if status == "running":
        progress = job.get("progress")
        fraction = float(progress) if isinstance(progress, (int, float)) and not isinstance(progress, bool) else 0.0
        return {**record, "state": "solving", "progress": {"fraction": min(max(fraction, 0.0), 1.0)}}, None
    if status == "complete":
        result: dict[str, Any] = dict(record["job"])
        mesh = _solved_mesh(job, ingest)
        if mesh is not None:
            result["mesh"] = mesh
        solved = {**record, "state": "solved", "result": result}
        notice = _notice(ingest)
        return ({**solved, "notice": notice} if notice else solved), None
    if status == "cancelled":
        return {**record, "state": "cancelled", "cancelReason": _cancel_reason(job)}, None
    if cad.get("held"):
        refusal = cad_of(job).get("refusal")
        refusal = refusal if isinstance(refusal, Mapping) else {}
        code = effective_reason_code(refusal.get("code"), refusal.get("message")) or None
        options = [{"id": str(option.get("id")), "label": str(option.get("label"))}
                   for option in cad.get("options") or [] if isinstance(option, Mapping)]
        return {**record, "state": "needsYou",
                "prompt": {"code": code, "nextStep": cad.get("next_step"), "options": options,
                           "answerIn": "wg"}}, None
    failure, incident = _failure(job, cad)
    phase = "solving" if _is_bound(job) else "preparing"
    return {**record, "state": "couldNotFinish", "phase": phase, "failure": failure}, incident


def _comparable(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {key: value for key, value in record.items() if key not in _VOLATILE_FIELDS}


class RequestStatusPublisher:
    """Keeps every request's status file current. One per application.

    :meth:`tick` is called from the delivery loop, off the event loop, one call
    at a time. :meth:`stop` ends it before the stores close. ``clock`` is
    monotonic seconds (injectable for tests).
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
        self._scanned_once = False
        #: Requests learned while this process runs (not at its first scan):
        #: their whole life is observed, so a failure in it owes an incident.
        self._observed_from_start: set[str] = set()
        self._receipts: dict[str, dict[str, Any]] = {}
        self._receipt_stamps: dict[str, int] = {}
        self._records: dict[str, dict[str, Any]] = {}
        self._job_requests: dict[str, str] = {}
        self._other_jobs: set[str] = set()
        self._written_at: dict[str, float] = {}
        self._write_failures: dict[str, int] = {}
        self._deferred: set[str] = set()
        self._dirty: set[str] = set()
        self._stopped = threading.Event()
        self._busy = threading.Lock()

    # -- lifetime --------------------------------------------------------------

    def stop(self, timeout: float = 10.0) -> bool:
        """Stop publishing and wait for a tick under way. False: it is still running."""

        self._stopped.set()
        acquired = self._busy.acquire(timeout=timeout)
        if acquired:
            self._busy.release()
        return acquired

    # -- what to look at -------------------------------------------------------

    def _scan(self) -> None:
        """Learn new and rewritten receipts and forget pruned ones, from the folder."""

        stamps: dict[str, int] = {}
        try:
            with os.scandir(self.directory) as entries:
                for entry in entries:
                    name = entry.name
                    if name.startswith(".") or not name.endswith(".json") or name.endswith(REQUEST_STATUS_SUFFIX):
                        continue
                    request_id = name.removesuffix(".json")
                    if not _OPERATION_ID.fullmatch(request_id):
                        continue
                    try:
                        if entry.is_file(follow_symlinks=False):
                            stamps[request_id] = entry.stat(follow_symlinks=False).st_mtime_ns
                    except OSError:
                        continue
        except OSError:
            pass
        for request_id in set(self._receipts) - set(stamps):
            self._forget(request_id)
        for request_id, stamp in stamps.items():
            if self._receipt_stamps.get(request_id) == stamp and request_id in self._receipts:
                continue
            receipt = _read_json(self.directory / f"{request_id}.json")
            if receipt is None or receipt.get("schemaVersion") != ACK_SCHEMA_VERSION:
                continue
            if request_id not in self._receipts:
                previous = validate_request_status(
                    _read_json(status_path(self.directory, request_id)), request_id=request_id,
                )
                if previous is not None:
                    self._records[request_id] = previous
                if self._scanned_once:
                    self._observed_from_start.add(request_id)
            self._receipts[request_id] = receipt
            self._receipt_stamps[request_id] = stamp
            self._dirty.add(request_id)
        self._scanned_once = True

    def _forget(self, request_id: str) -> None:
        for table in (self._receipts, self._receipt_stamps, self._records, self._written_at,
                      self._write_failures):
            table.pop(request_id, None)
        for pending in (self._deferred, self._dirty, self._observed_from_start):
            pending.discard(request_id)
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
                request_id = (job_cad_state(row) or {}).get("operation_id")
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

    def _publish(self, request_id: str, job_store: Any, now: float) -> bool:
        receipt = self._receipts[request_id]
        operation = self.store.get_operation(request_id)
        solve = request_kind(receipt.get("kind"), (operation or {}).get("kind")) == PREPARE_AND_SOLVE and receipt.get("outcome") != ACK_REFUSED
        if solve and job_store is None:
            return False  # nothing to say about a Solve before the jobs runtime is up
        job = job_store.latest_cad_job(
            request_id, receipt.get("jobId") or (operation or {}).get("job_id"),
        ) if solve else None
        cad = job_cad_state(job) if job is not None else None
        ingest = self._ingest_record(job, cad) if job is not None and job.get("status") == "complete" else None
        if job is not None:
            self._job_requests[str(job["id"])] = request_id
            self._other_jobs.discard(str(job["id"]))
        previous = self._records.get(request_id)
        projected, incident = project_request_status(
            receipt, operation, job, cad, ingest=ingest, previous=previous,
        )
        candidate = validate_request_status(
            {**projected, "seq": _next_seq(previous), "updatedAt": utc_now()}, request_id=request_id,
        )
        if candidate is None:  # pragma: no cover - a projection the reader rejects is a bug
            logger.error("The status projected for request %s does not validate; not written.", request_id)
            return False
        if _comparable(candidate) == _comparable(previous):
            self._deferred.discard(request_id)
            return False
        if (previous is not None and previous.get("state") == candidate["state"] == "solving"
                and now - self._written_at.get(request_id, float("-inf")) < PROGRESS_INTERVAL_S):
            self._deferred.add(request_id)
            return False
        try:
            publish_record(status_path(self.directory, request_id), candidate)
        except OSError as exc:
            self._write_failed(request_id, exc)
            return False
        self._write_failures.pop(request_id, None)
        self._deferred.discard(request_id)
        self._records[request_id] = candidate
        self._written_at[request_id] = now
        observed = (
            (previous is not None and (
                previous.get("state") != "couldNotFinish"
                # Try again: a new job of the chain failed too, before a tick saw it run.
                or (previous.get("job") or {}).get("jobId") != (candidate.get("job") or {}).get("jobId")))
            or (previous is None and request_id in self._observed_from_start))
        if incident is not None and candidate["state"] == "couldNotFinish" and observed:
            self._record_failure(request_id, job, candidate, incident)
        return True

    def _write_failed(self, request_id: str, exc: OSError) -> None:
        """A status that could not be written: said once, recorded if it keeps failing."""

        failures = self._write_failures.get(request_id, 0) + 1
        self._write_failures[request_id] = failures
        self._dirty.add(request_id)  # tried again on the next tick
        if failures == 1:
            logger.warning("Could not write the status of request %s: %s", request_id, exc)
        if failures == WRITE_FAILURE_INCIDENT_AFTER:
            record_incident(
                "cad-status-write-failure", "cad-request-status",
                ids={"request_id": request_id, "operation_id": request_id},
                exception=exc, evidence={"consecutive_failures": failures},
            )

    @staticmethod
    def _record_failure(
        request_id: str, job: Mapping[str, Any] | None, record: Mapping[str, Any], incident: Mapping[str, Any],
    ) -> None:
        """A Fusion request ended in a failure no diagnosis recorded: for the developers."""

        job = job or {}
        refusal = cad_of(job).get("refusal") if job else None
        record_incident(
            str(incident["kind"]), "cad-solve" if record.get("phase") == "solving" else "cad-preparation",
            ids={"job_id": job.get("id"), "operation_id": request_id, "request_id": request_id,
                 "run_number": job.get("run_number"), "manifest_sha256": record.get("manifestSha256")},
            evidence={"code": incident.get("code"), "stage": job.get("stage"),
                      "stage_message": job.get("stage_message"), "error": job.get("error_message"),
                      "refusal": dict(refusal) if isinstance(refusal, Mapping) else None,
                      "bound": _is_bound(job) if job else None},
            redaction_names=[str(record["documentName"])] if record.get("documentName") else [],
        )

    # -- the loop's step -------------------------------------------------------

    def tick(self, job_store: Any) -> int:
        """One step: learn, follow, publish. Returns how many files were written."""

        if self._stopped.is_set() or not self._busy.acquire(blocking=False):
            return 0
        try:
            return self._tick(job_store)
        finally:
            self._busy.release()

    def _tick(self, job_store: Any) -> int:
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
                if not request_status_terminal(self._records.get(request_id))
            )
        due = {request_id for request_id in self._deferred
               if now - self._written_at.get(request_id, float("-inf")) >= PROGRESS_INTERVAL_S}
        pending, self._dirty = self._dirty | due, set()
        written = 0
        for request_id in sorted(pending):
            if self._stopped.is_set():
                self._dirty.update(pending)  # unpublished; nothing more runs
                break
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
