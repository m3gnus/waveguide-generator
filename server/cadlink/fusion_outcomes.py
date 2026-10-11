"""Settle WG-produced Fusion operations from version-3 document evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any, Mapping

from server.contracts.canonical import canonical_ascii_json

from .operations import (
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
    FUSION_STAGES,
    UPDATE_LINK,
)
from .store import CadLinkStore
from .wglink_protocol import FUSION_REQUEST_DELIVERY_VERSION


logger = logging.getLogger(__name__)
FUSION_KINDS = frozenset({REQUEST_RETURN, INSERT_LINK, UPDATE_LINK})
_last_request_seen: dict[str, str] = {}


# Additive recovery metadata, ignored by older readers; no user_version change.
RECOVERY_NOTICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS cad_recovery_notices (
  notice_id TEXT PRIMARY KEY,
  document_id TEXT,
  document_name TEXT,
  dismissed_at TEXT
)
"""


def _remember_recovery_document(store: CadLinkStore, row: Mapping[str, Any],
                                heartbeat: Mapping[str, Any]) -> None:
    """An Insert learns its document from the mark before that report is lost."""
    document = _document(heartbeat)
    document_id = _operation_id(document.get("id"))
    expected = recovery_document_id(store, row)
    if (_applying(heartbeat) != row["operation_id"] or not document_id
            or (expected is not None and expected != document_id)):
        return
    with store._lock, store._transaction() as conn:
        conn.execute(RECOVERY_NOTICE_SCHEMA)
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
    with store._lock:
        conn = store._connect()
        conn.execute(RECOVERY_NOTICE_SCHEMA)
        saved = conn.execute("SELECT document_id FROM cad_recovery_notices WHERE notice_id = ?",
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


def _channel(row: Mapping[str, Any]) -> str:
    return "returnRequest" if row.get("kind") == REQUEST_RETURN else "handoff"


def _recent(heartbeat: Mapping[str, Any], row: Mapping[str, Any]) -> str | None:
    operation_id = str(row["operation_id"])
    diagnostics = _object(heartbeat.get("diagnostics")) or {}
    values = diagnostics.get("recentOutcomes")
    if not isinstance(values, list):
        return None
    for value in reversed(values):
        item = _object(value)
        if (
            item is not None
            and item.get("channel") == _channel(row)
            and item.get("requestId") == operation_id
        ):
            return _operation_id(item.get("outcome"))
    return None


def _last(heartbeat: Mapping[str, Any], row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    diagnostics = _object(heartbeat.get("diagnostics")) or {}
    value = _object(diagnostics.get("lastRequest"))
    if value is None or value.get("channel") != _channel(row):
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


def _visible_request(ipc: Path, row: Mapping[str, Any]) -> Path:
    directory = ".fusion-return-requests" if row["kind"] == REQUEST_RETURN else ".fusion-handoffs"
    return ipc / directory / f"{row['operation_id']}.json"


def _delivery_still_pending(ipc: Path, row: Mapping[str, Any]) -> bool:
    visible = _visible_request(ipc, row)
    if visible.exists():
        return True
    directory = visible.parent
    operation_id = str(row["operation_id"])
    return any(directory.glob(f".{operation_id}.json.*.tmp")) or any(
        directory.glob(f".wg-withdraw-{operation_id}.json.*.tmp")
    )


#: Outcomes the add-in reports in the file heartbeat diagnostics.
ADAPTER_OUTCOMES = (
    "applied", "reconciled", "refused", "superseded", "discarded", "recoveryRequired", "failed",
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
    for an outcome that does not apply to the operation's kind.
    """

    operation_id = str(row["operation_id"])
    mutating = row.get("kind") in MUTATING_KINDS

    def said(default: str) -> dict[str, Any]:
        return {"message": message or default}

    if outcome in ("applied", "reconciled"):
        if not mutating:
            if outcome == "reconciled" or evidence is not None:
                raise ValueError("a return request is applied without document evidence")
            return ACCEPTED, None, said("Fusion reported that it completed the return request.")
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
    mutations_only: bool = False,
) -> int:
    """Record evidence and adapter outcomes without inferring a missed result."""

    # Delivery schema 3 is the file protocol, independent of its publisher.
    version = heartbeat.get("deliveryVersion")
    if type(version) is not int or version < FUSION_REQUEST_DELIVERY_VERSION:
        return 0
    _log_last_request(heartbeat, ipc)
    changed = 0
    store.initialize()
    kinds = (INSERT_LINK, UPDATE_LINK) if mutations_only else (INSERT_LINK, UPDATE_LINK, REQUEST_RETURN)
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

        recent = _recent(heartbeat, row)
        if recent in ("superseded", "discarded") or (row["kind"] == REQUEST_RETURN and recent in {"refused", "failed"}):
            value = _record_adapter_outcome(store, row, recent)
        elif recent == "reconciled" and row["kind"] in MUTATING_KINDS and _export_id(row) and document_matches:
            value = _record_adapter_outcome(
                store, row, "reconciled", evidence=(operation_id, str(_export_id(row)))
            )
        elif recent == "recoveryRequired" and row["kind"] in MUTATING_KINDS:
            value = _record_adapter_outcome(store, row, "recoveryRequired")
        else:
            value = None
            if recent not in {None, "wgOutdated", "notTaken"}:
                logger.info("Ignoring unknown Fusion outcome %s for %s.", recent, operation_id)
        if value is not None:
            changed += 1
            continue

        last = _last(heartbeat, row)
        last_outcome = _operation_id(last.get("outcome")) if last is not None else None
        if last_outcome == "refused" or (last_outcome == "failed" and row["kind"] == REQUEST_RETURN):
            value = _record_adapter_outcome(store, row, last_outcome)
            changed += value is not None
            continue
        if last_outcome == "applied" and row["kind"] == REQUEST_RETURN:
            value = _record_adapter_outcome(store, row, "applied")
            changed += value is not None
            continue

        if row["state"] == RECEIVED and not _delivery_still_pending(ipc, row):
            generation = store.claim(operation_id, int(row["attempt_generation"]))
            changed += generation is not None
        elif row["state"] == PROCESSING:
            # A missed single-slot lastRequest is deliberately not guessed.
            logger.debug("Fusion operation %s is still awaiting an outcome.", operation_id)
    if changed:
        store.make_durable()
    return changed


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
        if row["kind"] not in FUSION_KINDS or row.get("stage") not in FUSION_STAGES:
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
        if row["kind"] in MUTATING_KINDS:
            value = _record(store, row, RECOVERY_REQUIRED, outcome={"message": message})
        else:
            value = _record(store, row, REJECTED, reason="adapter_failed", outcome={"message": message})
        changed += value is not None
    if changed:
        store.make_durable()
    return changed


def settle_return_from_file(
    store: CadLinkStore, row: Mapping[str, Any], ipc: Path, *, now: datetime | None = None,
) -> dict[str, Any] | None:
    """Persist exact return outcomes, including an old command-time report."""
    request_id = str(row["operation_id"])
    marker = ipc / ".fusion-status.json"
    try:
        if marker.is_symlink() or marker.stat().st_size > 256 * 1024:
            raise ValueError("Unsafe Fusion status file")
        report = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("Invalid Fusion status")
        updated = datetime.fromisoformat(str(report.get("updatedAt", "")).replace("Z", "+00:00"))
        if (report.get("schemaVersion") != 1 or report.get("cadApplication") != "fusion360"
                or (type(report.get("deliveryVersion")) is not int or report["deliveryVersion"] < FUSION_REQUEST_DELIVERY_VERSION)
                or updated.tzinfo is None or updated > (now or datetime.now(timezone.utc)) + timedelta(seconds=5)):
            raise ValueError("Unsupported Fusion status")
        diagnostics = report.get("diagnostics")
        if not isinstance(diagnostics, dict):
            raise ValueError("Invalid Fusion diagnostics")
        recent = diagnostics.get("recentOutcomes", [])
        candidates = [item for item in recent if isinstance(item, dict)
                      and item.get("channel") == "returnRequest" and item.get("requestId") == request_id] if isinstance(recent, list) else []
        last = diagnostics.get("lastRequest")
        if isinstance(last, dict) and last.get("channel") == "returnRequest" and last.get("correlationId") == request_id:
            candidates.append(last)
        if candidates:
            outcome = candidates[-1].get("outcome")
            if outcome in {"refused", "failed", "superseded", "discarded", "applied"}:
                state, reason, recorded = adapter_outcome(row, outcome)
                changed = store.record_outcome(request_id, int(row["attempt_generation"]), state,
                                               reason=reason, outcome=recorded)
                if changed is not None:
                    store.make_durable()
                return changed
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
        pass  # Missing or invalid evidence never implies refusal or success.
    return None


def settle_abandoned_file_returns(
    store: CadLinkStore, ipc: Path, workspace_root: Path | None, *,
    now: datetime | None = None, only_operation_id: str | None = None,
) -> int:
    """Bound a lost return result after all of its file activity has gone.

    The pinned add-in overwrites lastRequest and deletes a completed claim.
    Never infer success or retry an export. Pending publication, an add-in
    claim, or a correlated export (including its staging directory) prevents
    timeout. Without a configured exchange folder we cannot check that fence.
    """
    try:
        available = workspace_root is not None and workspace_root.is_dir()
    except OSError:
        available = False
    if not available:
        return 0
    assert workspace_root is not None
    checked_at = now or datetime.now(timezone.utc)
    store.initialize()
    with store._lock:
        rows = [dict(row) for row in store._connect().execute(
            "SELECT * FROM cad_operations WHERE kind = ? AND state IN (?, ?) "
            + ("AND operation_id = ? " if only_operation_id else ""),
            (REQUEST_RETURN, PROCESSING, CANCEL_REQUESTED)
            + ((only_operation_id,) if only_operation_id else ()),
        ).fetchall()]
    changed = 0
    for row in rows:
        try:
            claim = json.loads(row.get("claim_json") or "{}")
            updated = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
            if (not isinstance(claim, Mapping) or claim.get("liveSessionId")
                    or updated.tzinfo is None or checked_at - updated < LEGACY_LIVE_CLAIM_MAX_AGE):
                continue
            exact = settle_return_from_file(store, row, ipc, now=checked_at)
            if exact is not None:
                changed += 1
                continue
            operation_id = str(row["operation_id"])
            visible = _visible_request(ipc, row)
            # iterdir propagates permission errors; glob may hide them and
            # mistake an unreadable claim directory for completed work.
            if not ipc.is_dir():
                continue
            paths = list(visible.parent.iterdir()) if visible.parent.exists() else []
            if any(path.name == visible.name
                   or (path.name.endswith(".tmp") and path.name.startswith(
                       (f".{visible.name}.", f".wg-withdraw-{visible.name}.")))
                   or (path.name.startswith(f".wglink-claim-{operation_id}-") and path.suffix == ".json")
                   for path in paths):
                continue
            # WGLink names both the final export and its temporary/reservation
            # paths with the sanitized correlation id, before exporting starts.
            safe_id = re.sub(r"[\\/:\x00-\x1f]", "_", operation_id).strip().rstrip(".") or "Untitled"
            returns = workspace_root / "wgreturn"
            if returns.exists() and any(f"-{safe_id}" in path.name for path in returns.iterdir()):
                continue
        except (OSError, TypeError, ValueError):
            continue  # An unreadable path is not evidence that work has stopped.
        value = _record(store, row, REJECTED, reason="adapter_failed", outcome={
            "message": "The Fusion return request was interrupted and reported no outcome."
        })
        changed += value is not None
    if changed:
        store.make_durable()
    return changed


__all__ = ["ADAPTER_OUTCOMES", "FUSION_KINDS", "LEGACY_LIVE_CLAIM_MAX_AGE",
           "adapter_outcome", "settle_from_heartbeat", "settle_abandoned_live_claims",
           "settle_abandoned_file_returns", "settle_return_from_file"]
