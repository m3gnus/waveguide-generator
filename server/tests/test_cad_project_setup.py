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
from server.cadlink.preparation import run_delivery_pass
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


def test_a_stored_snapshot_is_solvable_with_fusion_closed(harness: Harness, tmp_path: Path) -> None:
    from server.cadlink.api import _pending_solve_command

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
    _pending_solve_command(harness.data_dir, harness.workspace.resolve(), harness.store)
    # Fusion is closed: no heartbeat, no exchange folder, and WG restarted.
    shutil.rmtree(harness.workspace)
    assert not (harness.data_dir / "ipc" / "wglink" / ".fusion-status.json").exists()

    summary = harness.prepare("cmd-b")

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
