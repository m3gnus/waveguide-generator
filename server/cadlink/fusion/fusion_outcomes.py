"""Settle WG-produced Fusion operations from version-3 document evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from server.contracts.canonical import canonical_ascii_json

from server.cadlink.operations import (
    ACCEPTED,
    CANCELLED,
    CANCEL_REQUESTED,
    INSERT_LINK,
    MUTATING_KINDS,
    PROCESSING,
    RECEIVED,
    RECOVERY_REQUIRED,
    REJECTED,
    REQUEST_RETURN,
    STAGE_EXECUTING,
    TERMINAL_STATES,
    FUSION_STAGES,
    UPDATE_LINK,
)
from server.cadlink.store import CadLinkStore
from server.contracts.wglink_protocol import FUSION_REQUEST_DELIVERY_VERSION


logger = logging.getLogger(__name__)
# Pull from WG (request_return) is retired; its old rows are still read and shown.
FUSION_KINDS = frozenset({REQUEST_RETURN, INSERT_LINK, UPDATE_LINK})
_last_request_seen: dict[str, str] = {}


# Additive recovery metadata, ignored by older readers; no user_version change.
def _remember_recovery_document(store: CadLinkStore, row: Mapping[str, Any],
                                heartbeat: Mapping[str, Any]) -> None:
    """An Insert learns its document from the mark before that report is lost."""
    document = _document(heartbeat)
    document_id = _operation_id(document.get("id"))
    expected = recovery_document_id(store, row)
    if (_applying(heartbeat) != row["operation_id"] or not document_id
            or (expected is not None and expected != document_id)):
        return
    store.initialize()
    with store._lock, store._transaction() as conn:
        conn.execute("INSERT INTO cad_recovery_notices(notice_id, document_id, document_name) "
                     "VALUES (?, ?, ?) ON CONFLICT(notice_id) DO UPDATE SET "
                     "document_id = COALESCE(cad_recovery_notices.document_id, excluded.document_id), "
                     "document_name = COALESCE(excluded.document_name, cad_recovery_notices.document_name)",
                     (row["operation_id"], document_id, _operation_id(document.get("name"))))


def _object(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _operation_id(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _record(
    store: CadLinkStore,
    row: Mapping[str, Any],
    state: str,
    *,
    reason: str | None = None,
    outcome: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    return store.record_outcome(
        str(row["operation_id"]),
        int(row["attempt_generation"]),
        state,
        reason=reason,
        outcome=outcome,
    )


def _export_id(row: Mapping[str, Any]) -> str | None:
    try:
        target = json.loads(str(row.get("target_json") or "{}"))
        inputs = json.loads(str(row.get("inputs_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    value = inputs.get("export_id") or target.get("export_id")
    return value if isinstance(value, str) and value else None


def _target(row: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        value = json.loads(str(row.get("target_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def recovery_document_id(store: CadLinkStore, row: Mapping[str, Any]) -> str | None:
    """The declared destination, or the document a new Insert first marked."""
    target = _target(row)
    if row.get("kind") == UPDATE_LINK:
        return _operation_id(target.get("document_id"))
    destination = _object(target.get("destination")) or {}
    if destination.get("kind") == "document":
        return _operation_id(destination.get("value"))
    store.initialize()
    with store._lock:
        saved = store._connect().execute("SELECT document_id FROM cad_recovery_notices WHERE notice_id = ?",
                             (row["operation_id"],)).fetchone()
    return _operation_id(saved["document_id"]) if saved is not None else None


def _document(heartbeat: Mapping[str, Any]) -> Mapping[str, Any]:
    return _object(heartbeat.get("document")) or {}


def _evidence(
    store: CadLinkStore, heartbeat: Mapping[str, Any], row: Mapping[str, Any]
) -> tuple[str, str] | None:
    if row.get("kind") not in MUTATING_KINDS:
        return None
    operation_id = str(row["operation_id"])
    expected_export_id = _export_id(row)
    if expected_export_id is None:
        return None
    document = _document(heartbeat)
    expected_document_id = recovery_document_id(store, row)
    if (expected_document_id is not None and document.get("id") != expected_document_id
            or row.get("kind") == UPDATE_LINK and expected_document_id is None):
        return None
    links = document.get("links")
    if not isinstance(links, list):
        return None
    for value in links:
        link = _object(value)
        if link is None or link.get("operationId") != operation_id:
            continue
        export_id = _operation_id(link.get("exportId"))
        if export_id == expected_export_id:
            return operation_id, export_id
    return None


def _applying(heartbeat: Mapping[str, Any]) -> str | None:
    value = _object(_document(heartbeat).get("applyingOperation"))
    return _operation_id(value.get("operationId")) if value is not None else None


#: The heartbeat channel of every request WG still makes (all handoffs).
_HANDOFF_CHANNEL = "handoff"


def _recent_record(heartbeat: Mapping[str, Any], row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    operation_id = str(row["operation_id"])
    diagnostics = _object(heartbeat.get("diagnostics")) or {}
    values = diagnostics.get("recentOutcomes")
    if not isinstance(values, list):
        return None
    for value in reversed(values):
        item = _object(value)
        if (
            item is not None
            and item.get("channel") == _HANDOFF_CHANNEL
            and item.get("requestId") == operation_id
        ):
            return item
    return None


def _last(heartbeat: Mapping[str, Any], row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    diagnostics = _object(heartbeat.get("diagnostics")) or {}
    value = _object(diagnostics.get("lastRequest"))
    if value is None or value.get("channel") != _HANDOFF_CHANNEL:
        return None
    return value if value.get("correlationId") == row.get("operation_id") else None


def _log_last_request(heartbeat: Mapping[str, Any], ipc: Path) -> None:
    diagnostics = _object(heartbeat.get("diagnostics")) or {}
    value = _object(diagnostics.get("lastRequest"))
    if value is None:
        return
    fingerprint = canonical_ascii_json(dict(value))
    key = str(ipc.resolve())
    if _last_request_seen.get(key) == fingerprint:
        return
    _last_request_seen[key] = fingerprint
    logger.info(
        "Fusion last request correlation=%s attempt=%s channel=%s outcome=%s.",
        value.get("correlationId"),
        value.get("attemptId"),
        value.get("channel"),
        value.get("outcome"),
    )


#: Outcomes the add-in reports in the file heartbeat diagnostics.
ADAPTER_OUTCOMES = (
    "applied", "reconciled", "expired", "refused", "superseded", "discarded", "recoveryRequired", "failed", "alreadyCurrent",
)
_APPLIED_BY_EVIDENCE = "Fusion document evidence confirms this operation applied."
_RECONCILED = "Fusion reconciled this operation from its document evidence."


def adapter_outcome(
    row: Mapping[str, Any],
    outcome: str,
    *,
    evidence: tuple[str, str] | None = None,
    message: str | None = None,
) -> tuple[str, str | None, dict[str, Any]]:
    """What the store records when the add-in reports ``outcome`` for ``row``.

    Returns ``(state, reason, outcome)``. A mutation is accepted only as
    reconciled, with ``evidence`` equal to its own operation and export ids; ``failed`` leaves
    a mutation that was executing ``recovery_required``. Raises ``ValueError``
    for an outcome that does not apply to the operation's kind. Every caller
    passes a mutation today; the non-mutating checks stay as guards, so a
    future caller cannot accept a request from document evidence.
    """

    operation_id = str(row["operation_id"])
    mutating = row.get("kind") in MUTATING_KINDS

    def said(default: str) -> dict[str, Any]:
        return {"message": message or default}

    if outcome in ("applied", "reconciled"):
        if not mutating:
            raise ValueError("only a document mutation is accepted from document evidence")
        export_id = _export_id(row)
        if export_id is None or evidence != (operation_id, export_id):
            raise ValueError("a document mutation is accepted only with its own operation and export ids")
        return ACCEPTED, None, {
            **said(_APPLIED_BY_EVIDENCE if outcome == "applied" else _RECONCILED),
            "reconciled": True,
            "evidence": {"operation_id": operation_id, "export_id": export_id},
        }
    if outcome == "refused":
        return REJECTED, "adapter_refused", said("Fusion refused this request.")
    if outcome == "alreadyCurrent":
        return CANCELLED, "adapter_not_started", said("The selected Fusion link already has this export; no update was needed.")
    if outcome == "expired":
        return CANCELLED, "expired", said("The unstarted Fusion insert expired. Send the model from WG again.")
    if outcome == "superseded":
        return CANCELLED, "superseded", said("Fusion superseded this request before it started.")
    if outcome == "discarded":
        return CANCELLED, "adapter_not_started", said(
            "Fusion discarded an interrupted claim that never started."
        )
    if outcome == "recoveryRequired":
        if not mutating:
            raise ValueError("only a document mutation can need recovery")
        return RECOVERY_REQUIRED, None, said(
            "Fusion reported that this document mutation needs recovery."
        )
    if outcome == "failed":
        if mutating and row.get("stage") == STAGE_EXECUTING:
            return RECOVERY_REQUIRED, None, said(
                "Fusion reported a failure while it was applying this document mutation."
            )
        return REJECTED, "adapter_failed", said("Fusion reported that this request failed.")
    raise ValueError(f"unknown adapter outcome {outcome!r}")


def _record_adapter_outcome(
    store: CadLinkStore, row: Mapping[str, Any], outcome: str, **kwargs: Any
) -> dict[str, Any] | None:
    state, reason, recorded = adapter_outcome(row, outcome, **kwargs)
    return _record(store, row, state, reason=reason, outcome=recorded)


def settle_from_heartbeat(
    store: CadLinkStore,
    heartbeat: Mapping[str, Any],
    ipc: Path,
    *,
    only_operation_id: str | None = None,
) -> int:
    """Record evidence and adapter outcomes without inferring a missed result."""

    # Delivery schema 3 is the file protocol, independent of its publisher.
    version = heartbeat.get("deliveryVersion")
    if type(version) is not int or version < FUSION_REQUEST_DELIVERY_VERSION:
        return 0
    _log_last_request(heartbeat, ipc)
    changed = 0
    store.initialize()
    kinds = (INSERT_LINK, UPDATE_LINK)
    with store._lock:
        # Recovery must remain reachable beyond a bounded recent listing.
        rows = [dict(row) for row in store._connect().execute(
            f"SELECT * FROM cad_operations WHERE kind IN ({', '.join('?' for _ in kinds)}) "
            "AND state NOT IN (?, ?, ?) "
            + ("AND operation_id = ? " if only_operation_id is not None else "")
            + "ORDER BY rowid",
            (*kinds, ACCEPTED, REJECTED, CANCELLED)
            + ((only_operation_id,) if only_operation_id is not None else ()),
        ).fetchall()]
    for initial in rows:
        row = initial
        operation_id = str(row["operation_id"])
        if row.get("kind") in MUTATING_KINDS:
            _remember_recovery_document(store, row, heartbeat)
        evidence = _evidence(store, heartbeat, row)
        if evidence is not None:
            value = _record(
                store,
                row,
                ACCEPTED,
                outcome={
                    "message": _APPLIED_BY_EVIDENCE,
                    "reconciled": True,
                    "evidence": {"operation_id": evidence[0], "export_id": evidence[1]},
                },
            )
            changed += value is not None
            continue
        if row["state"] == RECOVERY_REQUIRED:
            # Only exact document evidence (handled above) settles recovery.
            # Notice dismissal hides feedback without claiming a repair.
            continue
        expected_document_id = recovery_document_id(store, row) if row["kind"] in MUTATING_KINDS else None
        document_matches = expected_document_id is None or _document(heartbeat).get("id") == expected_document_id
        if _applying(heartbeat) == operation_id and row["kind"] in MUTATING_KINDS and document_matches:
            value = _record(
                store,
                row,
                RECOVERY_REQUIRED,
                outcome={"message": "Fusion began applying this operation but reported no completion evidence."},
            )
            changed += value is not None
            continue

        recent_record = _recent_record(heartbeat, row) or {}
        recent = _operation_id(recent_record.get("outcome"))
        message = _operation_id(recent_record.get("message"))
        if recent in ("superseded", "discarded", "refused", "expired", "alreadyCurrent", "failed"):
            value = _record_adapter_outcome(store, row, recent, message=message)
        elif (recent in ("applied", "reconciled") and row["kind"] in MUTATING_KINDS
              and recent_record.get("exportId") == _export_id(row)
              and _operation_id(recent_record.get("documentId"))
              and (expected_document_id is None or recent_record["documentId"] == expected_document_id)):
            value = _record_adapter_outcome(store, row, recent,
                        evidence=(operation_id, str(_export_id(row))), message=message)
        elif recent == "reconciled" and row["kind"] in MUTATING_KINDS and _export_id(row) and document_matches:
            value = _record_adapter_outcome(
                store, row, "reconciled", evidence=(operation_id, str(_export_id(row)))
            )
        elif recent == "recoveryRequired" and row["kind"] in MUTATING_KINDS:
            value = _record_adapter_outcome(store, row, "recoveryRequired", message=message)
        else:
            value = None
            if recent not in {None, "claimed", "wgOutdated", "notTaken"}:
                logger.info("Ignoring unknown Fusion outcome %s for %s.", recent, operation_id)
        if value is not None:
            changed += 1
            continue

        last = _last(heartbeat, row)
        last_outcome = _operation_id(last.get("outcome")) if last is not None else None
        if last_outcome in {"refused", "expired", "alreadyCurrent"}:
            value = _record_adapter_outcome(store, row, last_outcome,
                                            message=_operation_id(last.get("message")))
            changed += value is not None
            continue

        if row["state"] == RECEIVED and (recent == "claimed" or last_outcome == "running"):
            generation = store.claim(operation_id, int(row["attempt_generation"]))
            changed += generation is not None
        elif row["state"] == PROCESSING:
            # A missed single-slot lastRequest is deliberately not guessed.
            logger.debug("Fusion operation %s is still awaiting an outcome.", operation_id)
    if changed:
        store.make_durable()
    return changed


def settle_reported_outcomes(store: CadLinkStore, data_dir: Path, *,
                             now: datetime | None = None) -> int:
    """Record current evidence or retained command receipts, even after 20 seconds."""
    from server.cadlink.fusion.fusion_status import (
        heartbeat_problem, last_fusion_heartbeat, wg_request_pickup,
    )
    from server.cadlink.ipc import ipc_folder
    report = last_fusion_heartbeat(data_dir, now)
    if report is None:
        return 0
    if heartbeat_problem(report, now) is not None:
        if wg_request_pickup(report) is None:
            return 0  # older add-ins never promised retained per-request receipts
        diagnostics = _object(report.get("diagnostics")) or {}
        records = diagnostics.get("recentOutcomes")
        records = records if isinstance(records, list) else []
        # An old claim does not prove the adapter is still running. Only
        # terminal receipts survive the live observation's freshness window.
        report = {"deliveryVersion": report.get("deliveryVersion"),
                  "diagnostics": {"recentOutcomes": [item for item in records
                      if isinstance(item, Mapping) and item.get("outcome") in ADAPTER_OUTCOMES]}}
    return settle_from_heartbeat(store, report, ipc_folder(data_dir))


# Give an old add-in's startup journal reconciliation a bounded grace period.
LEGACY_LIVE_CLAIM_MAX_AGE = timedelta(minutes=30)


def settle_abandoned_live_claims(
    store: CadLinkStore, *, now: datetime | None = None,
    only_operation_id: str | None = None,
) -> int:
    """Stop waiting for expired HTTP claims, without repeating a CAD mutation.

    Run after file evidence settlement: an exact completion wins before timeout.
    The persisted claim distinguishes legacy HTTP attempts from file requests.
    Updated time includes the last recorded stage progress; restarts never reset
    the bound. Mutation safety does not infer that an unreported attempt stopped
    before touching Fusion, even if its last stage was adapter-received.
    """
    checked_at = now or datetime.now(timezone.utc)
    store.initialize()
    with store._lock:
        rows = [dict(row) for row in store._connect().execute(
            "SELECT * FROM cad_operations WHERE state IN (?, ?) AND claim_json IS NOT NULL "
            + ("AND operation_id = ? " if only_operation_id else ""),
            (PROCESSING, CANCEL_REQUESTED) + ((only_operation_id,) if only_operation_id else ()),
        ).fetchall()]
    changed = 0
    for row in rows:
        # Pull from WG rows are settled at startup (retire_return_requests).
        if row["kind"] not in MUTATING_KINDS or row.get("stage") not in FUSION_STAGES:
            continue
        try:
            claim = json.loads(row["claim_json"])
            updated = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if (not isinstance(claim, Mapping) or not claim.get("liveSessionId")
                or updated.tzinfo is None or checked_at - updated < LEGACY_LIVE_CLAIM_MAX_AGE):
            continue
        message = "The legacy Fusion live request was interrupted and reported no outcome."
        value = _record(store, row, RECOVERY_REQUIRED, outcome={"message": message})
        changed += value is not None
    if changed:
        store.make_durable()
    return changed


#: What an unfinished Pull from WG left by an earlier WG is settled with.
RETIRED_RETURN_MESSAGE = (
    "Pull from WG was retired. Use Send to WG in Fusion to bring the current model into WG."
)


def retire_return_requests(store: CadLinkStore) -> int:
    """Settle every unfinished Pull from WG an earlier WG left, as cancelled.

    WG no longer asks Fusion to send its model back (``request_return``); the
    user sends it with Send to WG in Fusion. Rows an earlier WG left waiting
    would otherwise wait forever, so startup cancels them with the remedy. A
    row that cannot be settled is logged and left as it is, and a registry
    that cannot be read or written (busy, read-only, an I/O error) is logged
    and skipped: it never stops WG. The next start tries again.
    """
    changed = 0
    try:
        store.initialize()
        with store._lock:
            rows = [dict(row) for row in store._connect().execute(
                "SELECT * FROM cad_operations WHERE kind = ? AND state NOT IN (?, ?, ?) ORDER BY rowid",
                (REQUEST_RETURN, *sorted(TERMINAL_STATES)),
            ).fetchall()]
        for row in rows:
            try:
                value = _record(store, row, CANCELLED, outcome={"message": RETIRED_RETURN_MESSAGE})
            except (TypeError, ValueError, sqlite3.Error) as exc:
                logger.warning("Could not settle retired Fusion return request %s: %s",
                               row.get("operation_id"), exc)
                continue
            changed += value is not None
        if changed:
            store.make_durable()
            logger.info("Cancelled %d unfinished Pull from WG request(s); Pull from WG is retired.", changed)
    except sqlite3.Error as exc:
        logger.warning("Could not settle retired Fusion return requests: %s", exc)
    return changed


__all__ = ["ADAPTER_OUTCOMES", "FUSION_KINDS", "LEGACY_LIVE_CLAIM_MAX_AGE", "RETIRED_RETURN_MESSAGE",
           "adapter_outcome", "retire_return_requests", "settle_from_heartbeat",
           "settle_abandoned_live_claims"]
