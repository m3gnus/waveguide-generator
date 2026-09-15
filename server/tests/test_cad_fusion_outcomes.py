from __future__ import annotations

import json
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.cadlink.fusion_outcomes import settle_from_heartbeat
from server.cadlink.operations import request_digest
from server.cadlink.store import CadLinkStore


def _store(tmp_path: Path) -> CadLinkStore:
    return CadLinkStore.for_data_dir(tmp_path)


def _ipc(tmp_path: Path) -> Path:
    path = tmp_path / "ipc" / "wglink"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _accept(
    store: CadLinkStore, operation_id: str = "update-1", kind: str = "update_link"
) -> dict:
    if kind == "update_link":
        target = {
            "document_id": "fusion:doc-1",
            "design_id": "wgd_1",
            "instance_id": "instance-1",
            "expected_baseline": {
                "kind": "document_signature_hash",
                "value": "sha256:state-1",
            },
        }
        inputs = {"export_id": "wge_1"}
    elif kind == "insert_link":
        target = {
            "destination": {"kind": "new_document", "value": operation_id},
            "export_id": "wge_1",
        }
        inputs = {}
    else:
        target = {
            "document_id": "fusion:doc-1",
            "design_id": "wgd_1",
            "instance_id": "instance-1",
            "expected_baseline": {
                "kind": "document_signature_hash",
                "value": "sha256:state-1",
            },
        }
        inputs = {}
    row, result = store.accept_operation(
        operation_id, kind, request_digest(kind, target, inputs), target, inputs
    )
    assert result == "created"
    return row


def _heartbeat(
    *,
    links: list[dict] | None = None,
    applying: dict | None = None,
    recent: list[dict] | None = None,
    last: dict | None = None,
    delivery: int = 3,
) -> dict:
    return {
        "deliveryVersion": delivery,
        "document": {
            "id": "fusion:doc-1",
            "links": links or [],
            "applyingOperation": applying,
        },
        "diagnostics": {"recentOutcomes": recent or [], "lastRequest": last},
    }


def test_link_evidence_settles_an_update_as_reconciled_accepted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _accept(store)
    heartbeat = _heartbeat(
        links=[{"operationId": "update-1", "exportId": "wge_1"}]
    )

    assert settle_from_heartbeat(store, heartbeat, _ipc(tmp_path)) == 1
    row = store.get_operation("update-1")
    assert row is not None and row["state"] == "accepted"
    assert json.loads(row["outcome_json"])["evidence"] == {
        "operation_id": "update-1", "export_id": "wge_1"
    }


def test_link_evidence_must_match_the_expected_export_and_update_document(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _accept(store)

    for heartbeat, changed in (
        (_heartbeat(links=[{"operationId": "update-1", "exportId": "wge_other"}]), 1),
        ({
            **_heartbeat(links=[{"operationId": "update-1", "exportId": "wge_1"}]),
            "document": {
                "id": "fusion:other",
                "links": [{"operationId": "update-1", "exportId": "wge_1"}],
            },
        }, 0),
    ):
        assert settle_from_heartbeat(store, heartbeat, _ipc(tmp_path)) == changed
        assert store.get_operation("update-1")["state"] == "processing"


def test_a_return_request_is_not_accepted_from_mutation_link_evidence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _accept(store, operation_id="return-1", kind="request_return")

    settle_from_heartbeat(
        store,
        _heartbeat(links=[{"operationId": "return-1", "exportId": "wge_1"}]),
        _ipc(tmp_path),
    )

    assert store.get_operation("return-1")["state"] == "processing"


def test_an_applying_mark_is_recovery_required_after_the_heartbeat_goes_and_wg_restarts(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _accept(store)
    assert settle_from_heartbeat(
        store,
        _heartbeat(applying={"operationId": "update-1"}),
        _ipc(tmp_path),
    ) == 1
    store.close()

    reopened = _store(tmp_path)
    row = reopened.get_operation("update-1")
    assert row is not None and row["state"] == "recovery_required"
    # No heartbeat is no new evidence and cannot downgrade the durable state.
    assert reopened.get_operation("update-1")["state"] == "recovery_required"


def test_a_refused_outcome_is_rejected_adapter_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _accept(store)
    last = {
        "channel": "handoff", "correlationId": "update-1",
        "attemptId": "attempt-1", "outcome": "refused",
    }

    settle_from_heartbeat(store, _heartbeat(last=last), _ipc(tmp_path))
    row = store.get_operation("update-1")
    assert row is not None and (row["state"], row["reason"]) == (
        "rejected", "adapter_refused"
    )


def test_an_outdated_addin_heartbeat_settles_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _accept(store)
    heartbeat = _heartbeat(
        delivery=2, links=[{"operationId": "update-1", "exportId": "wge_1"}]
    )

    assert settle_from_heartbeat(store, heartbeat, _ipc(tmp_path)) == 0
    assert store.get_operation("update-1")["state"] == "received"


def test_reconcile_never_moves_recovery_required_without_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = _accept(store)
    store.record_outcome(
        "update-1", int(row["attempt_generation"]), "recovery_required"
    )

    assert settle_from_heartbeat(
        store, _heartbeat(last={
            "correlationId": "update-1", "attemptId": "a", "outcome": "applied"
        }), _ipc(tmp_path), only_operation_id="update-1"
    ) == 0
    assert store.get_operation("update-1")["state"] == "recovery_required"


def test_last_request_ids_are_logged_once_per_change(tmp_path: Path, caplog) -> None:
    store = _store(tmp_path)
    _accept(store)
    heartbeat = _heartbeat(last={
        "channel": "handoff", "correlationId": "other",
        "attemptId": "attempt-7", "outcome": "failed",
    })

    with caplog.at_level(logging.INFO):
        settle_from_heartbeat(store, heartbeat, _ipc(tmp_path))
        settle_from_heartbeat(store, heartbeat, _ipc(tmp_path))
    matching = [record for record in caplog.records if "attempt-7" in record.getMessage()]
    assert len(matching) == 1


@pytest.mark.parametrize(
    ("outcome", "state", "reason"),
    [
        ("superseded", "cancelled", "superseded"),
        ("discarded", "cancelled", "adapter_not_started"),
        ("reconciled", "accepted", None),
        ("recoveryRequired", "recovery_required", None),
    ],
)
def test_every_recent_fusion_outcome_is_mapped(
    tmp_path: Path, outcome: str, state: str, reason: str | None
) -> None:
    store = _store(tmp_path)
    _accept(store)
    settle_from_heartbeat(
        store,
        _heartbeat(recent=[{
            "channel": "handoff", "requestId": "update-1", "outcome": outcome
        }]),
        _ipc(tmp_path),
    )
    row = store.get_operation("update-1")
    assert row is not None and (row["state"], row["reason"]) == (state, reason)


@pytest.mark.parametrize("outcome", ["wgOutdated", "notTaken", "unknown"])
def test_nonsettling_recent_outcomes_leave_processing(
    tmp_path: Path, outcome: str
) -> None:
    store = _store(tmp_path)
    _accept(store)
    settle_from_heartbeat(
        store,
        _heartbeat(recent=[{
            "channel": "handoff", "requestId": "update-1", "outcome": outcome
        }]),
        _ipc(tmp_path),
    )
    assert store.get_operation("update-1")["state"] == "processing"


def test_recent_outcome_from_another_channel_cannot_settle_a_colliding_id(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _accept(store)
    settle_from_heartbeat(
        store,
        _heartbeat(recent=[{
            "channel": "returnRequest", "requestId": "update-1", "outcome": "superseded"
        }]),
        _ipc(tmp_path),
    )
    assert store.get_operation("update-1")["state"] == "processing"


@pytest.mark.parametrize("outcome", ["applied", "failed", "running", "requested"])
def test_last_request_without_document_evidence_never_accepts_a_mutation(
    tmp_path: Path, outcome: str
) -> None:
    store = _store(tmp_path)
    _accept(store)
    settle_from_heartbeat(
        store,
        _heartbeat(last={
            "channel": "handoff", "correlationId": "update-1",
            "attemptId": "attempt-1", "outcome": outcome,
        }),
        _ipc(tmp_path),
    )
    assert store.get_operation("update-1")["state"] == "processing"


def test_last_request_requires_the_exact_channel_and_correlation_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _accept(store)
    for last in (
        {
            "channel": "returnRequest", "correlationId": "update-1",
            "attemptId": "attempt-1", "outcome": "refused",
        },
        {
            "channel": "handoff", "requestId": "update-1",
            "attemptId": "attempt-2", "outcome": "refused",
        },
    ):
        settle_from_heartbeat(store, _heartbeat(last=last), _ipc(tmp_path))
    assert store.get_operation("update-1")["state"] == "processing"


def test_request_file_gone_marks_the_operation_processing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _accept(store)
    settle_from_heartbeat(store, _heartbeat(), _ipc(tmp_path))
    row = store.get_operation("update-1")
    assert row is not None and row["state"] == "processing"
    assert row["attempt_generation"] == 1


def test_a_staged_delivery_is_not_mistaken_for_a_fusion_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _accept(store)
    directory = _ipc(tmp_path) / ".fusion-handoffs"
    directory.mkdir()
    (directory / ".update-1.json.staged.tmp").write_text("{}", encoding="utf-8")

    assert settle_from_heartbeat(store, _heartbeat(), _ipc(tmp_path)) == 0
    assert store.get_operation("update-1")["state"] == "received"


def test_dismissal_cancels_an_unclaimed_fusion_request(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _accept(store)

    dismissed = store.request_cancel("update-1")

    assert dismissed is not None
    assert (dismissed["state"], dismissed["reason"]) == ("cancelled", None)


def test_dismissal_does_not_guess_an_outcome_after_fusion_claims_the_request(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _accept(store)
    settle_from_heartbeat(store, _heartbeat(), _ipc(tmp_path))

    dismissed = store.request_cancel("update-1")

    assert dismissed is not None
    assert dismissed["state"] == "processing"


def test_applying_never_marks_a_return_as_recovery_required(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _accept(store, operation_id="return-1", kind="request_return")
    settle_from_heartbeat(
        store,
        _heartbeat(applying={"operationId": "return-1"}),
        _ipc(tmp_path),
    )
    assert store.get_operation("return-1")["state"] == "processing"


def test_terminal_backlog_does_not_hide_an_unsettled_operation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _accept(store, operation_id="000-pending")
    for index in range(1001):
        operation_id = f"zzz-terminal-{index:04d}"
        row = _accept(store, operation_id=operation_id, kind="request_return")
        store.record_outcome(
            operation_id, int(row["attempt_generation"]), "accepted"
        )

    settle_from_heartbeat(
        store,
        _heartbeat(links=[{"operationId": "000-pending", "exportId": "wge_1"}]),
        _ipc(tmp_path),
    )

    assert store.get_operation("000-pending")["state"] == "accepted"


def test_reconcile_route_rereads_live_document_evidence(tmp_path: Path) -> None:
    from server.app import create_app
    from server.cadlink.api import post_reconcile_cad_operation

    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir)
    store = app.state.cadlink_store
    row = _accept(store)
    store.record_outcome(
        "update-1", int(row["attempt_generation"]), "recovery_required"
    )
    marker = _ipc(data_dir) / ".fusion-status.json"
    marker.write_text(json.dumps({
        "schemaVersion": 1,
        "cadApplication": "fusion360",
        "deliveryVersion": 3,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "document": {
            "id": "fusion:doc-1",
            "links": [{"operationId": "update-1", "exportId": "wge_1"}],
        },
    }), encoding="utf-8")
    response = asyncio.run(
        post_reconcile_cad_operation(
            "update-1", SimpleNamespace(app=SimpleNamespace(state=app.state))
        )
    )
    assert response.operation.state == "accepted"
