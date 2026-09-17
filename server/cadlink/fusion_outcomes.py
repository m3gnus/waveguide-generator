"""Settle WG-produced Fusion operations from a live version-3 heartbeat."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping

from .fusion_delivery import DELIVERY_VERSION, HANDOFFS, RETURN_REQUESTS, addin_delivery_version
from .operations import (
    ACCEPTED,
    CANCELLED,
    INSERT_LINK,
    MUTATING_KINDS,
    PROCESSING,
    RECEIVED,
    RECOVERY_REQUIRED,
    REJECTED,
    REQUEST_RETURN,
    STAGE_EXECUTING,
    STATES,
    TERMINAL_STATES,
    UPDATE_LINK,
)
from .store import CadLinkStore


logger = logging.getLogger(__name__)
FUSION_KINDS = frozenset({REQUEST_RETURN, INSERT_LINK, UPDATE_LINK})
_last_request_seen: dict[str, str] = {}


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


def _document(heartbeat: Mapping[str, Any]) -> Mapping[str, Any]:
    return _object(heartbeat.get("document")) or {}


def _evidence(
    heartbeat: Mapping[str, Any], row: Mapping[str, Any]
) -> tuple[str, str] | None:
    if row.get("kind") not in MUTATING_KINDS:
        return None
    operation_id = str(row["operation_id"])
    expected_export_id = _export_id(row)
    if expected_export_id is None:
        return None
    document = _document(heartbeat)
    if row.get("kind") == UPDATE_LINK:
        expected_document_id = _operation_id(_target(row).get("document_id"))
        if expected_document_id is None or document.get("id") != expected_document_id:
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
    fingerprint = json.dumps(dict(value), sort_keys=True, separators=(",", ":"))
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
    directory = RETURN_REQUESTS.directory if row["kind"] == REQUEST_RETURN else HANDOFFS.directory
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


#: Outcomes the add-in reports for a Fusion request, by either transport: the
#: heartbeat's ``recentOutcomes``/``lastRequest`` and a live completion
#: (docs/reference/CADLINK-LIVE-PROTOCOL.md, section 7).
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

    Returns ``(state, reason, outcome)``. One mapping for the heartbeat and
    for a live completion. A mutation is accepted only as reconciled, with
    ``evidence`` equal to its own operation and export ids; ``failed`` leaves
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
) -> int:
    """Record evidence and adapter outcomes without inferring a missed result."""

    if (addin_delivery_version(heartbeat) or 0) < DELIVERY_VERSION:
        return 0
    _log_last_request(heartbeat, ipc)
    changed = 0
    rows = [
        row
        for row in store.list_operations(states=STATES - TERMINAL_STATES, limit=1000)
        if row.get("kind") in FUSION_KINDS
        and (only_operation_id is None or row.get("operation_id") == only_operation_id)
        and row.get("state") not in TERMINAL_STATES
    ]
    for initial in rows:
        row = initial
        operation_id = str(row["operation_id"])
        evidence = _evidence(heartbeat, row)
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
            # Only exact document evidence (handled above) or the user's
            # dismissal may settle this state.
            continue
        if _applying(heartbeat) == operation_id and row["kind"] in MUTATING_KINDS:
            value = _record(
                store,
                row,
                RECOVERY_REQUIRED,
                outcome={"message": "Fusion began applying this operation but reported no completion evidence."},
            )
            changed += value is not None
            continue

        recent = _recent(heartbeat, row)
        if recent in ("superseded", "discarded"):
            value = _record_adapter_outcome(store, row, recent)
        elif recent == "reconciled" and row["kind"] in MUTATING_KINDS and _export_id(row):
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
        if last_outcome == "refused":
            value = _record_adapter_outcome(store, row, "refused")
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
    return changed


__all__ = ["ADAPTER_OUTCOMES", "FUSION_KINDS", "adapter_outcome", "settle_from_heartbeat"]
