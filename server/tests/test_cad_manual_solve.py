"""A manual CAD-mode Solve is a backend-owned durable operation."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.cadlink import api
from server.cadlink.ingest import retain_snapshot
from server.cadlink.operations import PREPARE_AND_SOLVE

from test_cad_preparation import Harness, _revision, _setup, _write_return


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def _ingest(harness: Harness, *, name: str = "speaker.wgreturn") -> tuple[str, Path]:
    relative, _manifest = _write_return(harness.workspace, name)
    retained = retain_snapshot(harness.workspace / relative, harness.data_dir)

    def record(ingest_id: str, created_at: str) -> str:
        return json.dumps(
            {
                "ingest_id": ingest_id,
                "created_at": created_at,
                "return_id": "wgr_manual",
                "manifest_sha256": retained["manifest_sha256"],
                "artifact_sha256": retained["artifact_sha256"],
                "document": {"name": "Speaker"},
                "project": None,
            }
        )

    row = harness.store.allocate_ingest(
        manifest_sha256=retained["manifest_sha256"],
        artifact_sha256=retained["artifact_sha256"],
        record_builder=record,
    )
    return str(row["ingest_id"]), Path(retained["retained_path"])


def _request(harness: Harness) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                cadlink_store=harness.store,
                data_dir=str(harness.data_dir),
                update_restart=None,
            )
        )
    )


def _create(harness: Harness, ingest_id: str, operation_id: str = "manual-1"):
    return asyncio.run(
        api.post_cad_operation(
            api.ManualSolveOperationRequest(operationId=operation_id, ingestId=ingest_id),
            _request(harness),
        )
    )


def test_a_manual_solve_prepares_from_the_retained_copy_with_the_folder_gone(
    harness: Harness,
) -> None:
    ingest_id, retained = _ingest(harness)
    created = _create(harness, ingest_id)
    shutil.rmtree(harness.workspace)
    replayed = _create(harness, ingest_id)

    solved = harness.prepare(
        "manual-1", setup_revision_id=_revision(harness.store, _setup(engine="metal"))
    )

    assert created["operation"]["state"] == "received"
    assert harness.store.get_operation("manual-1")["snapshot_json"] is not None
    assert replayed == created
    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1")
    assert Path(harness.ingest.calls[0]["bundle_path"]) == retained


def test_the_same_operation_id_recovers_and_a_different_ingest_conflicts(
    harness: Harness,
) -> None:
    first, _retained = _ingest(harness, name="first.wgreturn")
    second, _other = _ingest(harness, name="second.wgreturn")

    original = _create(harness, first)
    recovered = _create(harness, first)
    conflict = _create(harness, second)

    assert recovered == original
    assert conflict.status_code == 409
    assert json.loads(conflict.body)["error"]["code"] == "operation_conflict"
    row = harness.store.get_operation("manual-1")
    assert row is not None and first in json.loads(row["inputs_json"])["bundle_path"]


def test_an_exact_replay_recovers_after_the_retained_cas_is_removed(harness: Harness) -> None:
    ingest_id, retained = _ingest(harness)
    created = _create(harness, ingest_id)
    shutil.rmtree(retained)

    replayed = _create(harness, ingest_id)

    assert replayed == created


def test_it_submits_under_cad_solve_key_with_the_selected_engine(harness: Harness) -> None:
    ingest_id, _retained = _ingest(harness)
    _create(harness, ingest_id)

    summary = harness.prepare(
        "manual-1", setup_revision_id=_revision(harness.store, _setup(engine="beat-cpu"))
    )

    assert summary["state"] == "accepted"
    assert harness.submitted[0].client_request_id == "cad-solve:manual-1"
    assert harness.submitted[0].options.engine == "beat-cpu"


def test_an_ingest_without_a_retained_copy_is_refused_409(harness: Harness) -> None:
    ingest_id, retained = _ingest(harness)
    shutil.rmtree(retained)

    response = _create(harness, ingest_id)

    assert response.status_code == 409
    assert json.loads(response.body)["error"]["code"] == "snapshot_not_retained"
    assert harness.store.list_operations(kind=PREPARE_AND_SOLVE) == []


def test_an_unknown_ingest_is_404(harness: Harness) -> None:
    refused = _create(harness, "wgi_missing")
    assert refused.status_code == 404
    assert json.loads(refused.body)["error"]["code"] == "unknown_ingest"


def test_an_update_restart_latch_creates_no_manual_operation(harness: Harness) -> None:
    from server.updates.restart import RestartApproval

    ingest_id, _retained = _ingest(harness)
    approval = RestartApproval()
    approval.approve("0.3.4")
    request = _request(harness)
    request.app.state.update_restart = approval

    response = asyncio.run(
        api.post_cad_operation(
            api.ManualSolveOperationRequest(operationId="manual-1", ingestId=ingest_id),
            request,
        )
    )

    assert response.status_code == 409
    assert json.loads(response.body)["error"]["code"] == "update_restart_pending"
    assert harness.store.list_operations() == []


def test_an_exact_replay_recovers_while_an_update_restart_is_pending(harness: Harness) -> None:
    from server.updates.restart import RestartApproval

    ingest_id, _retained = _ingest(harness)
    created = _create(harness, ingest_id)
    approval = RestartApproval()
    approval.approve("0.3.4")
    request = _request(harness)
    request.app.state.update_restart = approval

    replayed = asyncio.run(
        api.post_cad_operation(
            api.ManualSolveOperationRequest(operationId="manual-1", ingestId=ingest_id),
            request,
        )
    )

    assert replayed == created
