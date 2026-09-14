"""A solve is prepared from its project's own setup, by the backend alone.

docs/architecture/CAD-OPERATIONS.md, "Project setups" and "Delivery". A solve
Fusion sends for project B is prepared from B's recorded setup with the engine
selected in WG, whatever project the editor has open; the backend collects and
prepares solve commands itself, so the frontend only issues and observes.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any

import pytest

from server.cadlink.identity import design_hash
from server.cadlink.preparation import PreparationInput, prepare_operation, run_delivery_pass
from server.cadlink.project_setup import (
    inventory_sha256,
    snapshot_project,
    widen_polar_to_derivation,
)
from server.cadlink.setup import solve_request_for, validate_setup
from server.design.schema import DesignConfig
from server.design.textcfg import serialize

from test_cad_preparation import Harness, _accept, _manifest, _setup


SOURCES = [{"id": "source-hf", "role": "HF", "required": True}]


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def _project(harness: Harness, coverage: float) -> tuple[str, str]:
    """A saved WG design: its design id and its project lineage."""

    design = DesignConfig.model_validate({"formula": "OSSE", "L": 120, "a": coverage})
    identity = harness.store.save(
        requested=None,
        design_hash=design_hash(design),
        filename=f"horn-{coverage}.cfg",
        snapshot_builder=lambda saved: serialize(design, cadlink=saved),
    )["identity"]
    return identity.design_id, identity.lineage_id


def _project_return(harness: Harness, name: str, design_id: str, lineage_id: str) -> tuple[str, str]:
    """A return Fusion exported from that project's design."""

    step = b"STEP " + name.encode()
    manifest = copy.deepcopy(_manifest(step))
    manifest["instances"][0]["design_id"] = design_id
    manifest["instances"][0]["lineage_id"] = lineage_id
    bundle = harness.workspace / "wgreturn" / f"{name}.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "assembly.step").write_bytes(step)
    body = json.dumps(manifest).encode("utf-8")
    (bundle / "wgreturn.json").write_bytes(body)
    return f"wgreturn/{name}.wgreturn", "sha256:" + hashlib.sha256(body).hexdigest()


def _record_setup(harness: Harness, lineage_id: str, setup: dict[str, Any]) -> dict[str, Any]:
    from server.cadlink.api import ProjectSetupRequest, put_project_setup

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cadlink_store=harness.store)))
    payload = ProjectSetupRequest(lineageId=lineage_id, inventory=SOURCES, setup=setup)
    return asyncio.run(put_project_setup(payload, request))


def _select_engine(harness: Harness, engine: str) -> None:
    from server.cadlink.api import SolverSelectionRequest, put_solver_selection

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cadlink_store=harness.store)))
    asyncio.run(put_solver_selection(SolverSelectionRequest(engine=engine), request))


# -- project setups ------------------------------------------------------------------


def test_a_project_setup_is_recorded_for_its_project_and_sources(harness: Harness) -> None:
    _design_id, lineage = _project(harness, 45.0)

    first = _record_setup(harness, lineage, _setup(rigid=20.0))
    again = _record_setup(harness, lineage, _setup(rigid=20.0))
    changed = _record_setup(harness, lineage, _setup(rigid=12.0))

    assert first["revisionId"] == again["revisionId"] != changed["revisionId"]
    row = harness.store.get_project_setup(lineage, inventory_sha256(SOURCES))
    assert row["revision_id"] == changed["revisionId"]  # the latest is the project's
    assert harness.store.get_project_setup(lineage, inventory_sha256([])) is None


def test_the_project_of_a_snapshot_is_read_not_claimed(harness: Harness) -> None:
    design_id, lineage = _project(harness, 45.0)
    manifest = _manifest(b"STEP")

    manifest["instances"][0]["design_id"] = design_id
    assert snapshot_project(harness.store, manifest) == lineage
    # A CAD-authored document WG has never seen belongs to no project yet.
    authored = {**manifest, "instances": [], "document": {"name": "New", "native_id": "urn:doc-new"}}
    assert snapshot_project(harness.store, authored) is None
    assert harness.store.get_lineage_for_cad_document("urn:doc-new") is None  # nothing claimed
    claimed = harness.store.claim_cad_document_lineage("urn:doc-new", "New")
    assert snapshot_project(harness.store, authored) == claimed


# -- preparing from the project's setup ----------------------------------------------


def test_a_solve_for_project_b_is_prepared_from_b_setup(harness: Harness) -> None:
    _a_design, a_lineage = _project(harness, 45.0)
    b_design, b_lineage = _project(harness, 60.0)
    _record_setup(harness, a_lineage, _setup(rigid=20.0))
    _record_setup(harness, b_lineage, _setup(rigid=9.0))
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    _accept(harness.store, "cmd-b", bundle_path, manifest)

    summary = harness.prepare("cmd-b")  # no setup named: the project's own

    assert summary["state"] == "accepted"
    assert harness.submitted[-1].geometry.mesh.rigid_size_mm == 9.0
    revision = harness.store.get_setup_revision(summary["setupRevisionId"])
    assert json.loads(revision["setup_json"])["geometry"]["mesh"]["rigid_size_mm"] == 9.0


def test_the_engine_is_the_one_selected_in_wg(harness: Harness) -> None:
    b_design, b_lineage = _project(harness, 60.0)
    _record_setup(harness, b_lineage, _setup(engine="bempp"))
    _select_engine(harness, "metal")
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    _accept(harness.store, "cmd-b", bundle_path, manifest)

    summary = harness.prepare("cmd-b")

    assert harness.submitted[-1].options.engine == "metal"
    # The operation names the exact setup it was solved with, engine included.
    revision = json.loads(harness.store.get_setup_revision(summary["setupRevisionId"])["setup_json"])
    assert revision["options"]["engine"] == "metal"


def test_a_model_with_no_recorded_setup_waits_for_its_settings(harness: Harness) -> None:
    b_design, b_lineage = _project(harness, 60.0)
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    _accept(harness.store, "cmd-b", bundle_path, manifest)

    summary = harness.prepare("cmd-b")

    assert (summary["state"], summary["reason"]) == ("needs_user_input", "setup_required")
    assert harness.submitted == []
    # It names the document whose settings are wanted, which the UI cannot know.
    assert "Tritonia speaker" in summary["message"]


def _deliver(harness: Harness, command_id: str, bundle_path: str, manifest: str) -> Path:
    """A solve command as Fusion delivers it."""

    requests = harness.data_dir / "ipc" / "wglink" / ".wg-solve-requests"
    requests.mkdir(parents=True, exist_ok=True)
    (requests / f"{command_id}.json").write_text(json.dumps({
        "schemaVersion": 3, "target": "waveguide-generator", "commandId": command_id,
        "returnId": "wgr_1", "bundlePath": bundle_path, "manifestSha256": manifest,
        "requestedAt": "2026-09-13T01:00:00Z",
    }))
    return requests


def _pass(harness: Harness, running: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """One pass of the backend's delivery loop, waiting for what it started."""

    async def one_pass() -> list[str]:
        started: list[asyncio.Future[Any]] = []
        ids = await run_delivery_pass(
            harness.context(),
            spawn=lambda _operation_id, coroutine: started.append(asyncio.ensure_future(coroutine)),
            running=running,
        )
        await asyncio.gather(*started)
        return ids

    return asyncio.run(one_pass())


def test_a_stored_snapshot_is_solvable_with_fusion_closed(harness: Harness) -> None:
    b_design, b_lineage = _project(harness, 60.0)
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    _deliver(harness, "cmd-b", bundle_path, manifest)
    assert _pass(harness) == ["cmd-b"]  # retained, then waits: no settings yet
    assert harness.row("cmd-b")["reason"] == "setup_required"
    # Fusion is closed: no heartbeat and no exchange folder.
    shutil.rmtree(harness.workspace)
    assert not (harness.data_dir / "ipc" / "wglink" / ".fusion-status.json").exists()
    _record_setup(harness, b_lineage, _setup())

    summary = harness.prepare("cmd-b")  # the user's Solve now

    assert (summary["state"], summary["jobId"]) == ("accepted", "job-1")

# -- the request the backend composes ------------------------------------------------


def test_the_polar_grid_is_widened_to_what_the_ingestion_derived() -> None:
    from server.jobs.runtime import ImportedSolveRefusal, _validate_imported_polar_grid

    setup = validate_setup({
        **_setup(),
        "options": {
            "frequencies_hz": [1000.0],
            "polar_config": {"angle_range": [0, 90, 19], "enabled_axes": ["vertical"]},
        },
    })
    request = solve_request_for(
        setup, ingest_id="wgi_" + "1" * 26, manifest_sha256="sha256:" + "1" * 64,
        artifact_sha256="sha256:" + "2" * 64, acknowledged_findings=[],
    )
    record = {"polar_grid_derivation": {"axes": {
        "horizontal": {"minimum_deg": -180, "maximum_deg": 180},
        "vertical": {"minimum_deg": 0, "maximum_deg": 180},
        "diagonal": {"minimum_deg": 0, "maximum_deg": 180},
    }}}
    with pytest.raises(ImportedSolveRefusal):
        _validate_imported_polar_grid(None, request, record)

    widened = widen_polar_to_derivation(request, record["polar_grid_derivation"])

    _validate_imported_polar_grid(None, widened, record)
    polar = widened.options.polar_config
    assert polar.enabled_axes == ["horizontal", "vertical"]
    assert polar.angle_range[0] == -180 and polar.angle_range[1] == 180


# -- the backend is the consumer -----------------------------------------------------


def test_the_backend_collects_and_prepares_solve_commands_itself(harness: Harness) -> None:
    b_design, b_lineage = _project(harness, 60.0)
    _record_setup(harness, b_lineage, _setup())
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    requests = harness.data_dir / "ipc" / "wglink" / ".wg-solve-requests"
    requests.mkdir(parents=True)
    (requests / "cmd-b.json").write_text(json.dumps({
        "schemaVersion": 3, "target": "waveguide-generator", "commandId": "cmd-b",
        "returnId": "wgr_1", "bundlePath": bundle_path, "manifestSha256": manifest,
        "requestedAt": "2026-09-13T01:00:00Z",
    }))

    async def one_pass() -> list[str]:
        started: list[asyncio.Task[Any]] = []
        ids = await run_delivery_pass(
            harness.context(),
            spawn=lambda _operation_id, coroutine: started.append(asyncio.ensure_future(coroutine)),
        )
        await asyncio.gather(*started)
        return ids

    assert asyncio.run(one_pass()) == ["cmd-b"]
    assert harness.row("cmd-b")["state"] == "accepted"
    assert list(requests.iterdir()) == []
    # Nothing is prepared twice, and a waiting operation is not retried unasked.
    assert asyncio.run(one_pass()) == []
    assert len(harness.submitted) == 1


def test_the_loop_never_retries_an_operation_waiting_for_the_user(harness: Harness) -> None:
    b_design, b_lineage = _project(harness, 60.0)
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    _deliver(harness, "cmd-b", bundle_path, manifest)

    assert _pass(harness) == ["cmd-b"]
    assert harness.row("cmd-b")["reason"] == "setup_required"
    assert _pass(harness) == []  # it waits for the user, not for the next second
    assert harness.ingest.calls == []


def test_the_loop_skips_what_it_already_started(harness: Harness) -> None:
    b_design, b_lineage = _project(harness, 60.0)
    _record_setup(harness, b_lineage, _setup())
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    _deliver(harness, "cmd-b", bundle_path, manifest)

    assert _pass(harness, running={"cmd-b"}) == []
    assert harness.row("cmd-b")["state"] == "received"


def test_the_loop_never_takes_over_the_users_own_attempt(harness: Harness) -> None:
    b_design, b_lineage = _project(harness, 60.0)
    _record_setup(harness, b_lineage, _setup())
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    _accept(harness.store, "cmd-b", bundle_path, manifest)
    listed = int(harness.row("cmd-b")["attempt_generation"])
    # The user's Solve now claims it between the loop's listing and its start.
    users = harness.store.claim("cmd-b", listed)

    summary = asyncio.run(prepare_operation(
        harness.context(), "cmd-b", PreparationInput(), expected_generation=listed
    ))

    assert summary["attemptGeneration"] == users
    assert harness.ingest.calls == [] and harness.submitted == []


def test_without_a_wglink_folder_nothing_is_collected(harness: Harness) -> None:
    b_design, b_lineage = _project(harness, 60.0)
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    requests = _deliver(harness, "cmd-b", bundle_path, manifest)
    shutil.rmtree(harness.workspace)

    assert _pass(harness) == []
    # Left where it is, for when its snapshot can be retained.
    assert [path.name for path in requests.iterdir()] == ["cmd-b.json"]
    assert harness.store.get_operation("cmd-b") is None


def test_a_return_with_several_designs_belongs_to_its_solver_anchor_project(
    harness: Harness,
) -> None:
    a_design, _a_lineage = _project(harness, 45.0)
    b_design, b_lineage = _project(harness, 60.0)
    manifest = copy.deepcopy(_manifest(b"STEP"))
    first = manifest["instances"][0]
    first["design_id"] = a_design
    manifest["instances"].append({**copy.deepcopy(first), "instance_id": "instance-2", "design_id": b_design})
    manifest["coordinate_system"]["solver_anchor_instance_id"] = "instance-2"

    assert snapshot_project(harness.store, manifest) == b_lineage


def test_the_request_the_backend_composes_is_widened_before_it_is_bound(harness: Harness) -> None:
    b_design, b_lineage = _project(harness, 60.0)
    _record_setup(harness, b_lineage, {
        **_setup(),
        "options": {
            "frequencies_hz": [1000.0],
            "polar_config": {"angle_range": [0, 90, 19], "enabled_axes": ["vertical"]},
        },
    })
    harness.ingest.polar_grid_derivation = {"axes": {
        "horizontal": {"minimum_deg": -180, "maximum_deg": 180},
        "vertical": {"minimum_deg": 0, "maximum_deg": 180},
        "diagonal": {"minimum_deg": 0, "maximum_deg": 180},
    }}
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    _accept(harness.store, "cmd-b", bundle_path, manifest)

    assert harness.prepare("cmd-b")["state"] == "accepted"

    assert harness.submitted[-1].options.polar_config.enabled_axes == ["horizontal", "vertical"]
    bound = json.loads(harness.row("cmd-b")["request_json"])
    assert bound["options"]["polar_config"]["angle_range"][:2] == [-180.0, 180.0]


def test_the_routes_take_only_well_formed_settings(harness: Harness) -> None:
    from fastapi import HTTPException
    from pydantic import ValidationError

    from server.cadlink.api import ProjectSetupRequest, SolverSelectionRequest

    for inventory in ([], [{"id": "source-hf", "role": "HF", "required": "false"}]):
        with pytest.raises(ValidationError):
            ProjectSetupRequest.model_validate(
                {"lineageId": "wgl_1", "inventory": inventory, "setup": _setup()}
            )
    with pytest.raises(ValidationError):
        SolverSelectionRequest.model_validate({"engine": "   "})
    assert SolverSelectionRequest.model_validate({"engine": " Metal "}).engine == "metal"
    with pytest.raises(HTTPException) as refused:
        _record_setup(harness, "wgl_1", {"geometry": {}})
    assert refused.value.status_code == 422


def test_the_delivery_loop_runs_only_when_enabled_and_stops_at_shutdown(
    harness: Harness, monkeypatch
) -> None:
    from server.cadlink.api import _abandon_preparations_on_shutdown, _deliver_solve_commands

    app = SimpleNamespace(state=SimpleNamespace(
        cadlink_store=harness.store, data_dir=str(harness.data_dir), jobs_runtime=None,
        cad_workspace=None, update_restart=None,
    ))

    async def lifecycle() -> Any:
        await _deliver_solve_commands(app)()
        started = getattr(app.state, "cad_delivery_task", None)
        await _abandon_preparations_on_shutdown(app)()
        return started

    assert asyncio.run(lifecycle()) is None  # the suite turns it off
    monkeypatch.setenv("WG2_CAD_DELIVERY", "1")
    task = asyncio.run(lifecycle())
    assert task is not None and task.cancelled()


def test_a_waiver_on_project_a_does_not_carry_to_project_b(harness: Harness) -> None:
    a_design, a_lineage = _project(harness, 45.0)
    b_design, b_lineage = _project(harness, 60.0)
    _record_setup(harness, a_lineage, _setup())
    _record_setup(harness, b_lineage, _setup())
    harness.ingest.findings = [{"id": "healing-1", "kind": "healing-performed", "blocking": True}]
    for name, design, lineage in (("a", a_design, a_lineage), ("b", b_design, b_lineage)):
        bundle_path, manifest = _project_return(harness, name, design, lineage)
        _accept(harness.store, f"cmd-{name}", bundle_path, manifest)
    first_a = harness.prepare("cmd-a")
    assert first_a["reason"] == "findings_need_review"
    waived = {"approve_preparation_id": first_a["preparationId"], "approve_finding_ids": ("healing-1",)}

    assert harness.prepare("cmd-a", **waived)["state"] == "accepted"
    # The same finding on B is B's own to review, even with A's waiver sent along.
    b = harness.prepare("cmd-b", **waived)
    assert (b["state"], b["reason"]) == ("needs_user_input", "findings_need_review")


def test_a_manifest_role_is_matched_as_the_panel_names_it(harness: Harness) -> None:
    b_design, b_lineage = _project(harness, 60.0)
    _record_setup(harness, b_lineage, _setup())  # "HF", as the returns listing canonicalises it
    step = b"STEP lower"
    manifest = copy.deepcopy(_manifest(step))
    manifest["instances"][0]["design_id"] = b_design
    manifest["sources"][0]["role"] = "hf"
    bundle = harness.workspace / "wgreturn" / "lower.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "assembly.step").write_bytes(step)
    body = json.dumps(manifest).encode("utf-8")
    (bundle / "wgreturn.json").write_bytes(body)
    _accept(
        harness.store, "cmd-b", "wgreturn/lower.wgreturn",
        "sha256:" + hashlib.sha256(body).hexdigest(),
    )

    assert harness.prepare("cmd-b")["state"] == "accepted"


def test_an_operation_names_its_document_and_project(harness: Harness) -> None:
    from server.cadlink.preparation import operation_summary

    b_design, b_lineage = _project(harness, 60.0)
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    _deliver(harness, "cmd-b", bundle_path, manifest)

    _pass(harness)

    assert operation_summary(harness.row("cmd-b"))["snapshot"] == {
        "manifestSha256": manifest,
        "documentName": "Tritonia speaker",
        "projectLineageId": b_lineage,
    }


def test_the_loop_prepares_an_operation_once_its_return_is_retained(harness: Harness) -> None:
    b_design, b_lineage = _project(harness, 60.0)
    _record_setup(harness, b_lineage, _setup())
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    bundle = harness.workspace / bundle_path
    hidden = harness.workspace / "still-syncing"
    bundle.rename(hidden)  # the command arrived before its return could be read
    requests = _deliver(harness, "cmd-b", bundle_path, manifest)

    # Its delivery is kept, and nothing is prepared from a return WG does not hold.
    assert _pass(harness) == []
    assert harness.row("cmd-b")["state"] == "received"
    assert [path.name for path in requests.iterdir() if path.name.startswith(".wg-solve-claim-")]
    hidden.rename(bundle)

    assert _pass(harness) == ["cmd-b"]

    row = harness.row("cmd-b")
    assert (row["state"], row["job_id"]) == ("accepted", "job-1")
    assert list(requests.iterdir()) == []


# -- the delivery loop outlives a failing pass ---------------------------------------


def _run_delivery_loop(harness: Harness, monkeypatch, done: Any, timeout: float = 10.0) -> None:
    """Run the app's delivery loop, as mounted, until ``done()`` or the timeout."""

    from dataclasses import replace

    from server.cadlink import api

    monkeypatch.setenv("WG2_CAD_DELIVERY", "1")
    monkeypatch.setattr(api, "_DELIVERY_INTERVAL_S", 0.001)
    # The loop's preparations mesh with the harness's stand-in, not the mesher.
    monkeypatch.setattr(
        api,
        "_preparation_context",
        lambda _state, *, workspace_root=None: replace(harness.context(), workspace_root=workspace_root),
    )
    app = SimpleNamespace(state=SimpleNamespace(
        cadlink_store=harness.store, data_dir=str(harness.data_dir), jobs_runtime=None,
        cad_workspace=SimpleNamespace(selected_path=lambda: harness.workspace),
        update_restart=None,
    ))

    async def lifecycle() -> None:
        await api._deliver_solve_commands(app)()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not done() and loop.time() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.gather(*(getattr(app.state, "cad_preparations", None) or ()), return_exceptions=True)
        await api._abandon_preparations_on_shutdown(app)()

    asyncio.run(lifecycle())


def _solved(harness: Harness, operation_id: str) -> Any:
    def done() -> bool:
        row = harness.store.get_operation(operation_id)
        return row is not None and row["state"] == "accepted"

    return done


def _failing(times: int, error: BaseException, then: Any) -> Any:
    """``then``, after raising ``error`` the first ``times`` calls."""

    calls = {"n": 0}

    def call(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] <= times:
            raise error
        return then(*args, **kwargs)

    return call


@pytest.mark.parametrize("failure", ["wglink-folder", "store"])
def test_a_delivery_pass_that_fails_once_still_delivers_and_prepares(
    harness: Harness, monkeypatch, caplog, failure: str
) -> None:
    import logging
    import sqlite3

    from server.cadlink import api
    from server.cadlink.store import CadLinkStore

    b_design, b_lineage = _project(harness, 60.0)
    _record_setup(harness, b_lineage, _setup())
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    requests = _deliver(harness, "cmd-b", bundle_path, manifest)
    if failure == "wglink-folder":
        monkeypatch.setattr(
            api, "_selected_workspace_root",
            _failing(1, OSError("the WGLink folder's drive is not mounted"), api._selected_workspace_root),
        )
    else:
        # After the claim, before the operation is stored: the next pass finishes the claim.
        monkeypatch.setattr(
            CadLinkStore, "accept_operation",
            _failing(1, sqlite3.OperationalError("database is locked"), CadLinkStore.accept_operation),
        )

    with caplog.at_level(logging.WARNING, logger="server.cadlink.api"):
        _run_delivery_loop(harness, monkeypatch, _solved(harness, "cmd-b"))

    row = harness.row("cmd-b")
    assert (row["state"], row["job_id"], row["reason"]) == ("accepted", "job-1", None)
    assert len(harness.submitted) == 1
    assert list(requests.iterdir()) == []
    failures = [r for r in caplog.records if r.getMessage() == "Delivering CAD solve commands failed."]
    assert len(failures) == 1


def test_a_persisting_delivery_failure_is_logged_once_not_every_pass(
    harness: Harness, monkeypatch, caplog
) -> None:
    import logging

    from server.cadlink import api

    b_design, b_lineage = _project(harness, 60.0)
    _record_setup(harness, b_lineage, _setup())
    bundle_path, manifest = _project_return(harness, "b", b_design, b_lineage)
    _deliver(harness, "cmd-b", bundle_path, manifest)
    monkeypatch.setattr(
        api, "_selected_workspace_root",
        _failing(6, OSError("the WGLink folder's drive is not mounted"), api._selected_workspace_root),
    )

    with caplog.at_level(logging.WARNING, logger="server.cadlink.api"):
        _run_delivery_loop(harness, monkeypatch, _solved(harness, "cmd-b"))

    failures = [r for r in caplog.records if r.getMessage() == "Delivering CAD solve commands failed."]
    assert len(failures) == 1
    assert harness.row("cmd-b")["state"] == "accepted"
