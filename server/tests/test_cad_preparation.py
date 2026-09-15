"""Backend-owned CAD solves: setup revisions, fenced preparation, recovery.

docs/architecture/CAD-OPERATIONS.md, "Setup revisions" and "Preparation". The
operation owns a solve: its retained snapshot, its setup revision, the
preparation an attempt makes, its approvals and its bound request. Meshing is
replaced by a stand-in that commits a real ingestion record through the store,
under the attempt's fence, exactly as ``ingest_bundle`` does.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from server.cadlink import ingest as ingest_module
from server.cadlink.isolation import ChildRefusal
from server.cadlink.operations import (
    PREPARE_AND_SOLVE,
    prepare_and_solve_request,
    request_digest,
)
from server.cadlink.preparation import (
    PreparationContext,
    PreparationInput,
    operation_summary,
    prepare_operation,
    recover_operations,
    retain_operation_snapshot,
)
from server.cadlink.setup import setup_content, setup_digest, validate_setup
from server.cadlink.solve_command import collect_solve_deliveries
from server.cadlink.store import CadLinkStore
from server.cadlink.wgreturn import WgReturnError, read_wgreturn


def _manifest(step: bytes, document: bytes | None = None) -> dict[str, Any]:
    """A complete, valid return manifest (as in test_cadlink_wgreturn.py)."""

    instance = "instance-1"
    manifest = {
        "wgreturn_version": "1.0",
        "required_features": ["checksummed-files-v1", "assembly-frame-v1", "instance-records-v1"],
        "return": {"id": "wgr_01J5A8QK3M9T2XVBH0RD7NWE6C", "created_at": "2026-08-12T09:14:03Z"},
        "generator": {"adapter": "hornlab-fusion-addin/WGLink", "adapter_version": "1.0.0", "cad_app": "fusion360", "cad_version": "2704.1.53"},
        "document": {"name": "Tritonia speaker", "native_id": None},
        "coordinate_system": {"length_unit": "mm", "handedness": "right", "matrix_convention": "row-major-local-to-parent", "solver_anchor_instance_id": instance},
        "assembly": {"file": "assembly.step", "n_bodies_expected": 1, "bbox_mm": [[-172, -427, -185.23], [172, 152, 94.77]]},
        "files": {"assembly.step": {"sha256": "sha256:" + hashlib.sha256(step).hexdigest(), "size_bytes": len(step), "media_type": "model/step", "purpose": "exterior-assembly"}},
        "scope": {"selection": "root", "included": [{"object_id": "speaker", "name": "speaker", "body_kind": "surface", "visible": True, "external_reference": "local", "wglink_instance_id": instance}], "skipped": [], "fem_air_volumes": [], "status": "clean"},
        "instances": [{
            "instance_id": instance, "design_id": "wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M", "lineage_id": "wgl_01J4Y2WZQK8Z3TFD3E7V9XKQ4M", "edit_version": 19,
            "design_hash": "sha256:" + "1" * 64, "export_id": "wge_01J4Y2ZD000000000000000000", "export_sequence": 7,
            "formula": "osse", "config": {"root": {"formula": "OSSE"}},
            "geometry_hash": "sha256:" + "2" * 64, "origin_bundle_id": "wgb_01J4Y2ZF000000000000000000", "build_mode": "enclosure", "parameter_prefix": "wg_tritonia_", "occurrence_path": "Speaker/WGLink",
            "assembly_from_link": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "chirality": "original",
            "body_evidence": {"local_body_state": "unmodified", "baseline_fingerprint": {"is_solid": True, "volume_mm3": 12, "bbox_mm": [0, 0, 0, 1, 1, 1]}, "observed_fingerprint": {"is_solid": True, "volume_mm3": 12, "bbox_mm": [0, 0, 0, 1, 1, 1]}, "observed_at": "2026-08-12T09:14:03Z"},
            "source_contract": {"role": "HF", "throat_z_mm": 0, "throat_plane_link": {"origin_mm": [0, 0, 0], "normal": [0, 0, 1]}, "axis_link": {"origin_mm": [0, 0, 0], "direction": [0, 0, 1]}, "throat_diameter_mm": 25.4, "expected_disc_area_mm2": 506.707},
        }],
        "sources": [{"id": "source-hf", "role": "HF", "instance_id": instance, "required": True, "default_drive_channel_id": "drive-hf", "patch_policy": "single-connected", "expected_connected_components": 1, "selectors": {"linked_throat": {"instance_id": instance}, "appearance_labels": ["HF"]}, "observed": {"face_count": 1, "total_area_mm2": 506.696, "per_face_area_mm2": [506.696], "bodies": ["speaker"]}, "suggested_resolution_mm": 4}],
        "acoustics": None,
    }
    if document is not None:
        # The captured Fusion document the add-in puts beside the geometry.
        manifest["files"]["model.f3d"] = {
            "sha256": "sha256:" + hashlib.sha256(document).hexdigest(),
            "size_bytes": len(document),
            "media_type": "application/vnd.autodesk.fusion360",
            "purpose": "cad-document",
        }
    return manifest


def _write_return(
    workspace: Path,
    name: str = "speaker.wgreturn",
    *,
    step: bytes = b"STEP",
    document: bytes | None = None,
) -> tuple[str, str]:
    bundle = workspace / "wgreturn" / name
    bundle.mkdir(parents=True)
    (bundle / "assembly.step").write_bytes(step)
    if document is not None:
        (bundle / "model.f3d").write_bytes(document)
    body = json.dumps(_manifest(step, document)).encode("utf-8")
    (bundle / "wgreturn.json").write_bytes(body)
    return f"wgreturn/{name}", "sha256:" + hashlib.sha256(body).hexdigest()


def _accept(store: CadLinkStore, command_id: str, bundle_path: str, manifest: str) -> None:
    target, inputs = prepare_and_solve_request(
        return_id="wgr_1", bundle_path=bundle_path, manifest_sha256=manifest
    )
    store.accept_operation(
        command_id, PREPARE_AND_SOLVE, request_digest(PREPARE_AND_SOLVE, target, inputs), target, inputs
    )


def _setup(rigid: float = 20.0, **options: Any) -> dict[str, Any]:
    return {
        "geometry": {
            "drive_channels": [{"id": "drive-hf", "source_ids": ["source-hf"]}],
            "mesh": {"rigid_size_mm": rigid, "transition_mm": 30.0, "source_size_mm": {"source-hf": 8.0}},
        },
        "options": {"frequencies_hz": [500.0, 1000.0, 2000.0], **options},
    }


def _revision(store: CadLinkStore, setup: dict[str, Any]) -> str:
    parsed = validate_setup(setup)
    return str(store.create_setup_revision(setup_content(parsed), setup_digest(parsed))["revision_id"])


class FakeIngest:
    """``ingest_bundle`` without the mesher: it commits a real record, fenced."""

    def __init__(self, *, findings: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.findings = findings or []
        self.during: Callable[[], object] | None = None
        self.error: BaseException | None = None
        self.polar_grid_derivation: dict[str, Any] | None = None

    def __call__(
        self, bundle_path, mesh, skipped, store, data_dir, *, prep_options, commit_guard,
        retained_copy=False,
    ):
        self.calls.append(
            {"bundle_path": str(bundle_path), "mesh": dict(mesh), "retained_copy": retained_copy}
        )
        # Stage 1 exactly as ingest_bundle reads it.
        bundle = ingest_module.read_snapshot(bundle_path, retained=retained_copy)
        if self.during is not None:
            self.during()
        if self.error is not None:
            raise self.error

        def record(ingest_id: str, now: str) -> str:
            payload = {
                "ingest_id": ingest_id,
                "manifest_sha256": bundle.manifest_sha256,
                "artifact_sha256": bundle.artifact_sha256,
                "findings": self.findings,
                "created_at": now,
                "document": {"return_state_hash": "sha256:" + "5" * 64},
            }
            if self.polar_grid_derivation is not None:
                payload["polar_grid_derivation"] = self.polar_grid_derivation
            payload["report_sha256"] = "sha256:" + hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()
            return json.dumps(payload)

        row = store.allocate_ingest(
            manifest_sha256=bundle.manifest_sha256,
            artifact_sha256=bundle.artifact_sha256,
            record_builder=record,
            commit_guard=commit_guard,
        )
        return json.loads(row["record_json"])


class Refused(ValueError):
    """Stands in for the jobs system refusing a request it cannot take."""

    reason_code = "imported_engine_unsupported"


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path / "data"
        self.workspace = tmp_path / "workspace"
        self.store = CadLinkStore(tmp_path / "cadlink.db")
        self.ingest = FakeIngest()
        self.jobs: dict[str, str] = {}
        self.submitted: list[Any] = []
        self.submit_error: BaseException | None = None
        self.published: list[dict[str, Any]] = []
        self.blocked: str | None = None

    async def _submit(self, request) -> str:
        self.submitted.append(request)
        if self.submit_error is not None:
            error, self.submit_error = self.submit_error, None
            raise error
        job_id = f"job-{len(self.submitted)}"
        self.jobs[str(request.client_request_id)] = job_id
        return job_id

    def context(self) -> PreparationContext:
        return PreparationContext(
            store=self.store,
            data_dir=self.data_dir,
            workspace_root=self.workspace.resolve() if self.workspace.exists() else None,
            submit=self._submit,
            job_for_submission=self.jobs.get,
            publish=self.published.append,
            submission_refusals=(Refused,),
            submission_blocked=lambda: self.blocked,
            ingest=self.ingest,
        )

    def prepare(self, operation_id: str = "cmd-1", **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(prepare_operation(self.context(), operation_id, PreparationInput(**kwargs)))

    def row(self, operation_id: str = "cmd-1") -> dict[str, Any]:
        row = self.store.get_operation(operation_id)
        assert row is not None
        return row


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def _received(harness: Harness, command_id: str = "cmd-1") -> tuple[str, str]:
    bundle_path, manifest = _write_return(harness.workspace)
    _accept(harness.store, command_id, bundle_path, manifest)
    return bundle_path, manifest


def _collect(harness: Harness) -> Any:
    """Collect delivered solve commands as the backend's delivery loop does."""

    return collect_solve_deliveries(
        harness.data_dir,
        harness.store,
        retain=lambda operation_id: retain_operation_snapshot(
            harness.store, harness.data_dir, harness.workspace.resolve(), operation_id
        ),
    )


# -- setup revisions ---------------------------------------------------------------


def test_a_setup_revision_is_immutable_and_named_by_its_content(harness: Harness) -> None:
    first = _revision(harness.store, _setup())
    again = _revision(harness.store, _setup())
    other = _revision(harness.store, _setup(rigid=12.0))

    assert first == again != other
    assert json.loads(harness.store.get_setup_revision(first)["setup_json"])["geometry"]["mesh"][
        "rigid_size_mm"
    ] == 20.0


def test_a_setup_names_no_snapshot_and_must_be_a_valid_submission() -> None:
    with pytest.raises(ValueError, match="names no snapshot"):
        validate_setup({**_setup(), "geometry": {**_setup()["geometry"], "ingest_id": "wgi_x"}})
    with pytest.raises(ValueError):
        validate_setup({"geometry": {"drive_channels": [], "mesh": {}}})


# -- preparation ---------------------------------------------------------------------


def test_a_solve_is_prepared_from_the_retained_snapshot_and_submitted_once(harness: Harness) -> None:
    _received(harness)
    revision = _revision(harness.store, _setup())

    summary = harness.prepare(setup_revision_id=revision)

    assert (summary["state"], summary["stage"], summary["jobId"]) == ("accepted", "submitted", "job-1")
    # Meshed from WG's own copy, not the exchange folder.
    retained = Path(harness.ingest.calls[0]["bundle_path"])
    assert (harness.data_dir / "imports" / "bundles") in retained.parents or retained.parent.name == "bundles"
    assert harness.ingest.calls[0]["retained_copy"] is True
    request = harness.submitted[0]
    assert request.client_request_id == "cad-solve:cmd-1"
    assert request.geometry.ingest_id == summary["preparationId"]
    row = harness.row()
    assert json.loads(row["request_json"])["client_request_id"] == "cad-solve:cmd-1"
    assert row["setup_revision_id"] == revision
    # Every committed stage was published, in order.
    stages = [item["stage"] for item in harness.published]
    for expected in ("validating", "preparing-mesh", "ready", "submitted"):
        assert expected in stages
    assert stages.index("validating") < stages.index("preparing-mesh") < stages.index("ready") < stages.index("submitted")


def test_a_first_cad_authored_model_waits_for_its_setup(harness: Harness) -> None:
    _received(harness)

    summary = harness.prepare()

    assert (summary["state"], summary["reason"]) == ("needs_user_input", "setup_required")
    assert harness.ingest.calls == [] and harness.submitted == []


def test_an_accepted_return_still_prepares_after_its_folder_is_removed_and_wg_restarts(
    harness: Harness, tmp_path: Path
) -> None:
    _received(harness)
    assert harness.prepare()["reason"] == "setup_required"  # retained, then waits
    shutil.rmtree(harness.workspace)
    harness.store.close()
    harness.store = CadLinkStore(tmp_path / "cadlink.db")  # the restart

    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))

    assert (summary["state"], summary["jobId"]) == ("accepted", "job-1")
    assert "imports" in harness.ingest.calls[0]["bundle_path"]


def test_a_delivered_return_is_retained_before_its_delivery_is_acknowledged(
    harness: Harness, tmp_path: Path
) -> None:
    bundle_path, manifest = _write_return(harness.workspace)
    requests = harness.data_dir / "ipc" / "wglink" / ".wg-solve-requests"
    requests.mkdir(parents=True)
    (requests / "cmd-1.json").write_text(json.dumps({
        "schemaVersion": 3, "target": "waveguide-generator", "commandId": "cmd-1",
        "returnId": "wgr_1", "bundlePath": bundle_path, "manifestSha256": manifest,
        "requestedAt": "2026-09-13T01:00:00Z",
    }))

    _collect(harness)

    assert list(requests.iterdir()) == []  # acknowledged
    assert json.loads(harness.row()["snapshot_json"])["manifest_sha256"] == manifest
    # Nothing prepared it before its folder went and WG restarted.
    shutil.rmtree(harness.workspace)
    harness.store.close()
    harness.store = CadLinkStore(tmp_path / "cadlink.db")
    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))
    assert (summary["state"], summary["jobId"]) == ("accepted", "job-1")


def test_a_return_carrying_its_cad_document_prepares_from_the_retained_copy(
    harness: Harness,
) -> None:
    bundle_path, manifest = _write_return(harness.workspace, document=b"F3D archive")
    _accept(harness.store, "cmd-1", bundle_path, manifest)

    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))

    assert summary["state"] == "accepted"
    retained = Path(harness.ingest.calls[0]["bundle_path"])
    assert not (retained / "model.f3d").exists()  # no second copy of the archive
    with pytest.raises(WgReturnError, match="model.f3d"):
        read_wgreturn(retained)  # which is why a retained copy is read as one


def test_an_operation_whose_return_is_not_there_waits_instead_of_failing(
    harness: Harness,
) -> None:
    bundle_path, _manifest_sha = _received(harness)
    moved = harness.workspace / "elsewhere"
    (harness.workspace / bundle_path).rename(moved)  # a drive not mounted, say
    revision = _revision(harness.store, _setup())

    waiting = harness.prepare(setup_revision_id=revision)

    assert (waiting["state"], waiting["reason"]) == ("needs_user_input", "preparation_failed")
    assert "not in the WGLink folder" in waiting["message"]
    moved.rename(harness.workspace / bundle_path)
    assert harness.prepare(setup_revision_id=revision)["state"] == "accepted"


def test_a_return_that_changed_after_the_request_is_refused(harness: Harness) -> None:
    bundle_path, _manifest_sha = _write_return(harness.workspace)
    _accept(harness.store, "cmd-1", bundle_path, "sha256:" + "f" * 64)

    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))

    assert (summary["state"], summary["reason"]) == ("rejected", "snapshot_invalid")
    assert harness.submitted == []


# -- fencing -------------------------------------------------------------------------


def test_an_old_attempt_finishing_after_a_takeover_changes_nothing(harness: Harness) -> None:
    _received(harness)
    revision = _revision(harness.store, _setup())

    def taken_over() -> None:
        row = harness.row()
        assert harness.store.claim("cmd-1", int(row["attempt_generation"])) is not None

    harness.ingest.during = taken_over

    summary = harness.prepare(setup_revision_id=revision)

    # The old attempt committed no record and submitted nothing; the operation
    # belongs to the attempt that took it over.
    assert summary["state"] == "processing" and summary["attemptGeneration"] == 2
    with closing(sqlite3.connect(harness.store.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ingests").fetchone()[0] == 0
    assert harness.submitted == []


def test_a_dismissal_during_preparation_stops_the_attempt(harness: Harness) -> None:
    _received(harness)
    harness.ingest.during = lambda: harness.store.request_cancel("cmd-1")

    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))

    assert summary["state"] == "cancelled"
    assert harness.submitted == []
    with closing(sqlite3.connect(harness.store.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ingests").fetchone()[0] == 0


def test_an_idle_operation_is_dismissed_at_once(harness: Harness) -> None:
    _received(harness)

    assert harness.store.request_cancel("cmd-1")["state"] == "cancelled"
    assert harness.prepare(setup_revision_id=_revision(harness.store, _setup()))["state"] == "cancelled"


def test_a_dismissal_stands_when_the_fenced_attempt_then_fails(harness: Harness) -> None:
    _received(harness)
    harness.ingest.during = lambda: harness.store.request_cancel("cmd-1")
    harness.ingest.error = ChildRefusal("mesh", "the isolated CAD child crashed")

    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))

    assert summary["state"] == "cancelled"


def test_a_dismissal_during_a_refused_submission_stands(harness: Harness) -> None:
    _received(harness)
    original = harness._submit

    async def dismissed_then_refused(request):
        harness.store.request_cancel("cmd-1")
        harness.submitted.append(request)
        raise Refused("the selected engine cannot take this record; pick one of: bempp")

    harness._submit = dismissed_then_refused  # type: ignore[method-assign]
    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup(engine="metal")))
    assert summary["state"] == "cancelled"

    harness._submit = original  # type: ignore[method-assign]
    again = harness.prepare(setup_revision_id=_revision(harness.store, _setup(engine="bempp")))
    assert again["state"] == "cancelled" and len(harness.submitted) == 1


def test_a_job_created_as_the_user_dismisses_is_still_recorded(harness: Harness) -> None:
    _received(harness)
    original = harness._submit

    async def dismissed_then_created(request):
        harness.store.request_cancel("cmd-1")
        return await original(request)

    harness._submit = dismissed_then_created  # type: ignore[method-assign]

    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))

    assert (summary["state"], summary["jobId"]) == ("accepted", "job-1")


# -- binding and recovery ------------------------------------------------------------


class _ProcessStopped(BaseException):
    """The backend stopping mid-submission: nothing after it runs."""


def test_a_setup_change_after_binding_does_not_alter_the_recovery(harness: Harness) -> None:
    _received(harness)
    harness.submit_error = _ProcessStopped()

    with pytest.raises(_ProcessStopped):
        harness.prepare(setup_revision_id=_revision(harness.store, _setup(rigid=20.0)))
    bound = harness.row()["request_json"]
    assert bound is not None and harness.row()["state"] == "processing"
    assert recover_operations(harness.context()) == 1  # the restart
    assert harness.row()["reason"] == "interrupted"

    changed = _revision(harness.store, _setup(rigid=12.0))
    second = harness.prepare(setup_revision_id=changed)

    assert second["state"] == "accepted"
    assert len(harness.ingest.calls) == 1  # nothing was prepared again
    assert harness.submitted[-1].geometry.mesh.rigid_size_mm == 20.0
    assert harness.row()["request_json"] == bound


def test_a_failed_submission_that_created_nothing_releases_the_binding(harness: Harness) -> None:
    _received(harness)
    harness.submit_error = RuntimeError("database is locked")

    failed = harness.prepare(setup_revision_id=_revision(harness.store, _setup(engine="metal")))

    assert (failed["state"], failed["reason"]) == ("needs_user_input", "submission_refused")
    assert harness.row()["request_json"] is None
    accepted = harness.prepare(setup_revision_id=_revision(harness.store, _setup(engine="bempp")))
    assert accepted["state"] == "accepted" and harness.submitted[-1].options.engine == "bempp"


def test_a_submission_key_conflict_is_the_job_that_key_made(harness: Harness) -> None:
    from server.jobs.store import SubmissionConflictError

    _received(harness)

    async def browser_got_there_first(request):
        harness.submitted.append(request)
        harness.jobs["cad-solve:cmd-1"] = "job-browser"
        raise SubmissionConflictError(
            "Submission key 'cad-solve:cmd-1' was already used for a different request"
        )

    harness._submit = browser_got_there_first  # type: ignore[method-assign]

    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))

    assert (summary["state"], summary["jobId"]) == ("accepted", "job-browser")


def test_wg_stopping_after_the_job_exists_recovers_that_job(harness: Harness) -> None:
    _received(harness)
    revision = _revision(harness.store, _setup())
    # The job is created and then the answer is lost.
    original = harness._submit

    async def created_then_lost(request):
        await original(request)
        raise ConnectionResetError("the answer never arrived")

    harness._submit = created_then_lost  # type: ignore[method-assign]

    summary = harness.prepare(setup_revision_id=revision)

    assert (summary["state"], summary["jobId"]) == ("accepted", "job-1")
    assert len(harness.submitted) == 1


def test_startup_recovers_a_job_created_before_the_acknowledgement(harness: Harness) -> None:
    _received(harness)
    row = harness.row()
    harness.store.claim("cmd-1", int(row["attempt_generation"]))
    harness.jobs["cad-solve:cmd-1"] = "job-9"

    assert recover_operations(harness.context()) == 1

    assert (harness.row()["state"], harness.row()["job_id"]) == ("accepted", "job-9")


def test_startup_takes_over_an_attempt_the_stopped_backend_held(harness: Harness) -> None:
    _received(harness)
    row = harness.row()
    old = harness.store.claim("cmd-1", int(row["attempt_generation"]))

    assert recover_operations(harness.context()) == 1

    recovered = harness.row()
    assert (recovered["state"], recovered["reason"]) == ("needs_user_input", "interrupted")
    # The stopped attempt is fenced out for good.
    assert harness.store.advance_operation("cmd-1", old, stage="ready") is None


def _app(harness: Harness, job_for_submission_key) -> SimpleNamespace:
    async def never_start() -> None:
        raise AssertionError("CAD recovery started the jobs runtime")

    runtime = SimpleNamespace(
        store=SimpleNamespace(job_for_submission_key=job_for_submission_key),
        submit=harness._submit,
        events=None,
        start=never_start,
    )
    return SimpleNamespace(state=SimpleNamespace(
        cadlink_store=harness.store, data_dir=str(harness.data_dir), jobs_runtime=runtime,
        cad_workspace=None, update_restart=None,
    ))


def test_the_startup_handler_recovers_through_the_jobs_database_alone(harness: Harness) -> None:
    from server.cadlink.api import _preparation_context, _recover_on_startup
    from server.jobs.store import SubmissionConflictError

    _received(harness)
    harness.store.claim("cmd-1", 0)
    harness.jobs["cad-solve:cmd-1"] = "job-7"
    app = _app(harness, harness.jobs.get)

    asyncio.run(_recover_on_startup(app)())

    assert (harness.row()["state"], harness.row()["job_id"]) == ("accepted", "job-7")

    async def context():
        return _preparation_context(app.state)

    # A key conflict means the key made a job: it is reconciled, never refused.
    assert SubmissionConflictError not in asyncio.run(context()).submission_refusals


def test_a_jobs_database_not_created_yet_holds_no_job(harness: Harness) -> None:
    from server.cadlink.api import _recover_on_startup

    _received(harness)
    harness.store.claim("cmd-1", 0)

    def no_table(_key: str) -> str | None:
        raise sqlite3.OperationalError("no such table: job_submissions")

    asyncio.run(_recover_on_startup(_app(harness, no_table))())

    assert (harness.row()["state"], harness.row()["reason"]) == ("needs_user_input", "interrupted")


def test_a_refused_submission_releases_the_binding_for_a_new_choice(harness: Harness) -> None:
    _received(harness)
    harness.submit_error = Refused("the selected engine cannot take this record; pick one of: bempp")

    refused = harness.prepare(setup_revision_id=_revision(harness.store, _setup(engine="metal")))

    assert (refused["state"], refused["reason"]) == ("needs_user_input", "engine_unavailable")
    assert "pick one of" in refused["message"]
    assert harness.row()["request_json"] is None
    accepted = harness.prepare(setup_revision_id=_revision(harness.store, _setup(engine="bempp")))
    assert accepted["state"] == "accepted"
    assert harness.submitted[-1].options.engine == "bempp"


def _approve_restart_while_meshing(harness: Harness, message: str) -> None:
    """The update restart is approved while this attempt meshes, once."""

    def approve() -> None:
        harness.blocked = message
        harness.ingest.during = None

    harness.ingest.during = approve


def test_no_solve_is_submitted_while_an_update_restart_is_pending(harness: Harness) -> None:
    _received(harness)
    _approve_restart_while_meshing(harness, "An update restart is pending.")

    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))

    # Its own reason, so it is re-queued after the restart rather than left waiting.
    assert (summary["state"], summary["reason"]) == ("needs_user_input", "update_restart_pending")
    assert summary["message"] == "An update restart is pending."
    assert harness.submitted == [] and harness.row()["request_json"] is None


def test_a_worker_crash_is_a_recoverable_outcome(harness: Harness) -> None:
    _received(harness)
    revision = _revision(harness.store, _setup())
    harness.ingest.error = ChildRefusal(
        "mesh", "the isolated CAD child exceeded its 600 second deadline and was stopped"
    )

    crashed = harness.prepare(setup_revision_id=revision)

    assert (crashed["state"], crashed["reason"]) == ("needs_user_input", "preparation_failed")
    assert "600 second deadline" in crashed["message"]
    harness.ingest.error = None
    assert harness.prepare(setup_revision_id=revision)["state"] == "accepted"


# -- approvals -----------------------------------------------------------------------


def test_a_waiver_on_one_preparation_does_not_carry_to_the_next(harness: Harness) -> None:
    _received(harness)
    harness.ingest.findings = [{"id": "healing-1", "kind": "healing-performed", "blocking": True}]
    first = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))
    assert (first["state"], first["reason"]) == ("needs_user_input", "findings_need_review")
    harness.store.add_approvals("cmd-1", first["preparationId"], ["healing-1"])

    # A different setup makes a different preparation: the waiver stays behind,
    # also when it is sent along with the request.
    other = _revision(harness.store, _setup(rigid=12.0))
    second = harness.prepare(setup_revision_id=other)
    assert (second["state"], second["reason"]) == ("needs_user_input", "findings_need_review")
    assert second["preparationId"] != first["preparationId"]
    stale = harness.prepare(
        setup_revision_id=other,
        approve_preparation_id=first["preparationId"],
        approve_finding_ids=("healing-1",),
    )
    assert (stale["state"], stale["reason"]) == ("needs_user_input", "findings_need_review")

    third = harness.prepare(
        setup_revision_id=other,
        approve_preparation_id=second["preparationId"],
        approve_finding_ids=("healing-1",),
    )
    assert (third["state"], third["preparationId"]) == ("accepted", second["preparationId"])
    assert len(harness.ingest.calls) == 2  # the second preparation was resumed, not remade
    report = json.loads(
        harness.store.get_ingest(third["preparationId"])["record_json"]
    )["report_sha256"]
    assert harness.submitted[-1].geometry.acknowledged_findings == [f"{report}:healing-1"]


def test_an_approval_through_the_route_applies_to_the_resumed_preparation(
    harness: Harness,
) -> None:
    from fastapi import HTTPException

    from server.cadlink.api import OperationApprovalsRequest, post_cad_operation_approvals

    _received(harness)
    harness.ingest.findings = [{"id": "healing-1", "kind": "healing-performed", "blocking": True}]
    revision = _revision(harness.store, _setup())
    first = harness.prepare(setup_revision_id=revision)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cadlink_store=harness.store)))

    def approve(*finding_ids: str) -> dict[str, Any]:
        payload = OperationApprovalsRequest(
            preparationId=first["preparationId"], findingIds=list(finding_ids)
        )
        return asyncio.run(post_cad_operation_approvals("cmd-1", payload, request))

    with pytest.raises(HTTPException) as unknown:
        approve("healing-9")
    assert unknown.value.status_code == 422
    assert approve("healing-1")["approvals"] == [
        {"preparation_id": first["preparationId"], "finding_id": "healing-1"}
    ]

    resumed = harness.prepare(setup_revision_id=revision)

    assert (resumed["state"], resumed["preparationId"]) == ("accepted", first["preparationId"])
    assert len(harness.ingest.calls) == 1


# -- routes: the authoritative state a reloaded UI reads -----------------------------


def test_a_reloaded_ui_reads_the_same_stage(harness: Harness) -> None:
    from server.cadlink.api import get_cad_operation, list_cad_operations

    _received(harness)
    harness.ingest.findings = [{"id": "healing-1", "kind": "healing-performed", "blocking": True}]
    harness.prepare(setup_revision_id=_revision(harness.store, _setup()))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cadlink_store=harness.store)))

    detail = asyncio.run(get_cad_operation("cmd-1", request))
    listed = asyncio.run(list_cad_operations(request, pending=True, limit=100))

    assert (detail["state"], detail["stage"], detail["reason"]) == (
        "needs_user_input", "ready", "findings_need_review",
    )
    assert detail["preparation"]["blockingFindingIds"] == ["healing-1"]
    assert [item["operationId"] for item in listed["operations"]] == ["cmd-1"]
    assert listed["operations"][0]["stage"] == "ready"


def test_a_preparation_names_a_setup_revision_that_exists(harness: Harness) -> None:
    from fastapi import HTTPException

    from server.cadlink.api import PrepareOperationRequest, post_prepare_cad_operation

    _received(harness)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cadlink_store=harness.store)))

    with pytest.raises(HTTPException) as refused:
        asyncio.run(post_prepare_cad_operation(
            "cmd-1", PrepareOperationRequest(setupRevisionId="wgs_missing"), request
        ))

    assert refused.value.status_code == 404
    assert harness.row()["state"] == "received"


# -- preparation identity ------------------------------------------------------------


def test_a_meshing_semantics_change_is_a_new_preparation_identity(monkeypatch) -> None:
    from server.mesh import imported as meshing

    bundle = SimpleNamespace(artifact_sha256="sha256:" + "2" * 64, manifest_sha256="sha256:" + "1" * 64)
    manifest = {"sources": [{"id": "source-hf"}], "instances": []}
    sizes = {"rigid_size_mm": 20}

    baseline = ingest_module._cache_lookup_key(bundle, manifest, sizes, [], {})
    monkeypatch.setattr(ingest_module, "_semantics_key_entry", lambda: {})
    # Today's constants add nothing to a key: no existing mesh is re-made.
    assert ingest_module._cache_lookup_key(bundle, manifest, sizes, [], {}) == baseline
    monkeypatch.undo()

    # A last-bit difference between platforms' maths libraries is not a change.
    monkeypatch.setattr(
        meshing, "_SAGITTA_QUANTISE_LOG", math.nextafter(meshing._SAGITTA_QUANTISE_LOG, 1.0)
    )
    assert ingest_module.meshing_semantics_fingerprint() == ingest_module._BASELINE_MESHING_SEMANTICS
    monkeypatch.undo()

    monkeypatch.setattr(meshing, "IMPORTED_SURFACE_DEVIATION_MM", 0.12)
    changed = ingest_module._cache_lookup_key(bundle, manifest, sizes, [], {})
    assert changed != baseline


# -- existing installations ----------------------------------------------------------


def _old_installation(path: Path) -> None:
    """A cadlink.db as the build before these stages left it."""

    store = CadLinkStore(path)
    for command_id, state in (("cmd-done", "accepted"), ("cmd-refused", "refused"), ("cmd-open", None)):
        _accept(store, command_id, f"wgreturn/{command_id}.wgreturn", "sha256:" + "a" * 64)
        if state is not None:
            from server.cadlink.solve_command import record_outcome

            record_outcome(
                store, command_id, state=state,
                job_id="job-done" if state == "accepted" else None,
                reason=None if state == "accepted" else "bundle changed",
            )
    store.record_legacy_outcome("cmd-legacy", kind=PREPARE_AND_SOLVE, state="accepted", job_id="job-old")
    store.close()
    with closing(sqlite3.connect(path)) as conn:
        for column in ("stage", "setup_revision_id", "request_json", "snapshot_json",
                       "preparation_id", "approvals_json"):
            conn.execute(f"ALTER TABLE cad_operations DROP COLUMN {column}")
        conn.execute("DROP TABLE cad_setup_revisions")
        conn.execute("DROP TABLE cad_preparations")
        conn.commit()


def test_an_existing_installation_upgrades_without_replaying_anything(tmp_path: Path) -> None:
    path = tmp_path / "cadlink.db"
    _old_installation(path)

    store = CadLinkStore(path)

    summaries = {row["operation_id"]: operation_summary(row) for row in store.list_operations()}
    assert summaries["cmd-done"]["state"] == "accepted" and summaries["cmd-done"]["stage"] == "submitted"
    assert summaries["cmd-refused"]["state"] == "rejected"
    assert summaries["cmd-legacy"]["legacy"] is True
    # Only the unfinished request is left for the delivery loop to prepare;
    # nothing finished is.
    waiting = store.list_operations(kind=PREPARE_AND_SOLVE, states={"received"}, oldest_first=True)
    assert [row["operation_id"] for row in waiting if not row["legacy"]] == ["cmd-open"]
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
        assert conn.execute("SELECT COUNT(*) FROM cad_setup_revisions").fetchone()[0] == 0


def test_an_interrupted_migration_leaves_a_state_the_next_start_completes(
    tmp_path: Path, monkeypatch
) -> None:
    from server.cadlink import store as store_module

    path = tmp_path / "cadlink.db"
    _old_installation(path)

    def power_cut(*_args, **_kwargs):
        raise RuntimeError("the process stopped mid-migration")

    monkeypatch.setattr(store_module, "_import_legacy_solve_ledger", power_cut)
    with pytest.raises(RuntimeError, match="mid-migration"):
        CadLinkStore(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(cad_operations)")}
        assert "stage" not in columns  # rolled back as a whole
    monkeypatch.undo()

    reopened = CadLinkStore(path)
    assert reopened.get_operation("cmd-done")["state"] == "accepted"
    with closing(sqlite3.connect(path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(cad_operations)")}
        assert {"stage", "request_json", "approvals_json"} <= columns


# -- retention -----------------------------------------------------------------------


def test_cleanup_keeps_what_a_pending_operation_still_references(
    harness: Harness, tmp_path: Path, monkeypatch
) -> None:
    """Captured-document pruning retains the model state a pending preparation names."""

    from server.cadlink import api
    from server.cadlink.api import _retained_return_states, pending_operation_return_states

    _received(harness)
    harness.ingest.findings = [{"id": "healing-1", "kind": "healing-performed", "blocking": True}]
    harness.prepare(setup_revision_id=_revision(harness.store, _setup()))  # waits on findings

    assert pending_operation_return_states(harness.store) == ["sha256:" + "5" * 64]
    assert asyncio.run(_retained_return_states(None, "Tritonia", harness.store)) == [
        "sha256:" + "5" * 64
    ]
    # The cleanup a placed run document triggers holds it back too.
    reclaimed: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        api,
        "reclaim_captured_documents",
        lambda _root, stem, retained: reclaimed.append((stem, list(retained))),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cadlink_store=harness.store)))
    asyncio.run(api._reclaim_captured_documents(request, tmp_path / "runs", "Tritonia"))
    assert reclaimed == [("Tritonia", ["sha256:" + "5" * 64])]
    # Once the operation is finished, nothing of it is held back any more.
    harness.store.request_cancel("cmd-1")
    assert pending_operation_return_states(harness.store) == []
    # The retained snapshot itself is never pruned.
    assert list((harness.data_dir / "imports" / "bundles").iterdir())


# -- faults --------------------------------------------------------------------------


def test_a_malformed_return_never_blocks_the_deliveries_behind_it(harness: Harness) -> None:
    nested = harness.workspace / "wgreturn" / "nested.wgreturn"
    nested.mkdir(parents=True)
    body = b"[" * 50_000  # under the size limit, deeper than the parser can go
    (nested / "wgreturn.json").write_bytes(body)
    good_path, good_manifest = _write_return(harness.workspace)
    requests = harness.data_dir / "ipc" / "wglink" / ".wg-solve-requests"
    requests.mkdir(parents=True)
    for command_id, bundle_path, manifest, at in (
        ("cmd-bad", "wgreturn/nested.wgreturn", "sha256:" + hashlib.sha256(body).hexdigest(),
         "2026-09-13T01:00:00Z"),
        ("cmd-good", good_path, good_manifest, "2026-09-13T01:00:01Z"),
    ):
        (requests / f"{command_id}.json").write_text(json.dumps({
            "schemaVersion": 3, "target": "waveguide-generator", "commandId": command_id,
            "returnId": "wgr_1", "bundlePath": bundle_path, "manifestSha256": manifest,
            "requestedAt": at,
        }))

    _collect(harness)

    assert list(requests.iterdir()) == []
    assert harness.row("cmd-bad")["snapshot_json"] is None
    assert json.loads(harness.row("cmd-good")["snapshot_json"])["manifest_sha256"] == good_manifest
    with pytest.raises(WgReturnError, match="nested too deeply"):
        read_wgreturn(nested)


def test_an_unexpected_failure_never_strands_the_operation(harness: Harness) -> None:
    _received(harness)
    revision = _revision(harness.store, _setup())
    harness.ingest.error = KeyError("an unexpected failure")

    summary = harness.prepare(setup_revision_id=revision)

    assert (summary["state"], summary["reason"]) == ("needs_user_input", "preparation_failed")
    harness.ingest.error = None
    assert harness.prepare(setup_revision_id=revision)["state"] == "accepted"


def test_a_setup_this_build_cannot_read_asks_for_the_settings_again(harness: Harness) -> None:
    _received(harness)
    # A setup revision a different build stored, which this one cannot read.
    unreadable = harness.store.create_setup_revision('{"geometry": {}}', "sha256:" + "0" * 64)

    summary = harness.prepare(setup_revision_id=str(unreadable["revision_id"]))

    assert (summary["state"], summary["reason"]) == ("needs_user_input", "setup_required")
    assert harness.prepare(setup_revision_id=_revision(harness.store, _setup()))["state"] == "accepted"


def test_a_dismissal_stands_when_the_attempt_then_fails_unexpectedly(harness: Harness) -> None:
    _received(harness)
    harness.ingest.during = lambda: harness.store.request_cancel("cmd-1")
    harness.ingest.error = KeyError("an unexpected failure")

    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))

    assert summary["state"] == "cancelled"


def _update_link(store: CadLinkStore, operation_id: str) -> None:
    target = {
        "document_id": "urn:adsk.wipprod:dm.lineage:doc-1",
        "design_id": "wgd_1",
        "instance_id": "wgi_1",
        "expected_baseline": {"kind": "document_signature_hash", "value": "sha256:base"},
    }
    inputs = {"export_id": "wge_1"}
    store.accept_operation(
        operation_id, "update_link", request_digest("update_link", target, inputs), target, inputs
    )


def test_a_cad_mutation_under_way_is_not_dismissed(harness: Harness) -> None:
    _update_link(harness.store, "upd-1")
    assert harness.store.claim("upd-1", 0) == 1

    # It may need recovery from the document, not cancellation.
    assert harness.store.request_cancel("upd-1")["state"] == "processing"
    # One that has not started can still be dismissed.
    _update_link(harness.store, "upd-2")
    assert harness.store.request_cancel("upd-2")["state"] == "cancelled"


def test_preparing_an_operation_that_is_not_a_solve_is_refused(harness: Harness) -> None:
    from fastapi import HTTPException

    from server.cadlink.api import PrepareOperationRequest, post_prepare_cad_operation

    _update_link(harness.store, "upd-1")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cadlink_store=harness.store)))

    with pytest.raises(HTTPException) as refused:
        asyncio.run(post_prepare_cad_operation("upd-1", PrepareOperationRequest(), request))

    assert refused.value.status_code == 409
    assert harness.row("upd-1")["state"] == "received"


# -- the legacy solve-command routes -------------------------------------------------
#
# A build before the backend owned solves (v0.3.2, v0.3.3-rc.1) polled
# GET /api/cadlink/solve-command and posted outcomes back. Such a page can still
# be open in a browser tab across an update restart. The backend's delivery loop
# is the one consumer (CAD-OPERATIONS.md, "Solve-command compatibility"): these
# routes collect nothing, record nothing and hand nothing out.


def _legacy_request(harness: Harness) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        data_dir=str(harness.data_dir),
        cadlink_store=harness.store,
        cad_workspace=SimpleNamespace(selected_path=lambda: harness.workspace),
        jobs_runtime=None,
    )))


def _deliver_file(harness: Harness, bundle_path: str, manifest: str, command_id: str = "cmd-1") -> Path:
    requests = harness.data_dir / "ipc" / "wglink" / ".wg-solve-requests"
    requests.mkdir(parents=True, exist_ok=True)
    (requests / f"{command_id}.json").write_text(json.dumps({
        "schemaVersion": 3, "target": "waveguide-generator", "commandId": command_id,
        "operationId": command_id, "returnId": "wgr_1", "bundlePath": bundle_path,
        "manifestSha256": manifest, "requestedAt": "2026-09-13T01:00:00Z",
    }))
    return requests


def test_the_legacy_poll_collects_nothing_and_hands_nothing_out(harness: Harness) -> None:
    from server.cadlink.api import get_solve_command

    bundle_path, manifest = _write_return(harness.workspace)
    requests = _deliver_file(harness, bundle_path, manifest)

    assert asyncio.run(get_solve_command(_legacy_request(harness))) == {"command": None}
    # The delivery waits for the backend's loop; the poll neither claimed nor recorded it.
    assert [path.name for path in requests.iterdir()] == ["cmd-1.json"]
    assert harness.store.get_operation("cmd-1") is None


def test_a_legacy_poll_never_rejects_a_retained_operation(harness: Harness) -> None:
    from server.cadlink.api import get_solve_command

    bundle_path, manifest = _write_return(harness.workspace)
    _accept(harness.store, "cmd-1", bundle_path, manifest)
    retain_operation_snapshot(harness.store, harness.data_dir, harness.workspace.resolve(), "cmd-1")
    assert json.loads(harness.row()["snapshot_json"])["manifest_sha256"] == manifest
    # The WGLink folder is still selected, but the return has left it.
    shutil.rmtree(harness.workspace / bundle_path)

    assert asyncio.run(get_solve_command(_legacy_request(harness))) == {"command": None}

    assert harness.row()["state"] == "received"
    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))
    assert (summary["state"], summary["jobId"]) == ("accepted", "job-1")


def test_a_legacy_poll_never_ends_an_operation_an_attempt_holds(harness: Harness) -> None:
    from server.cadlink.api import get_solve_command

    bundle_path, manifest = _write_return(harness.workspace)
    _accept(harness.store, "cmd-1", bundle_path, manifest)
    generation = harness.store.claim("cmd-1", 0)
    assert harness.store.bind_request(
        "cmd-1", generation, setup_revision_id="wgs_x", request_json='{"x":1}'
    ) is not None
    (harness.workspace / bundle_path / "wgreturn.json").write_text("{}")  # the exchange copy changes
    before = harness.row()

    assert asyncio.run(get_solve_command(_legacy_request(harness))) == {"command": None}

    assert harness.row() == before
    # The attempt's job is still recorded as the operation's outcome.
    recorded = harness.store.record_outcome("cmd-1", generation, "accepted", job_id="job-9")
    assert recorded is not None and recorded["job_id"] == "job-9"


def test_the_legacy_outcome_route_records_nothing(harness: Harness) -> None:
    from server.cadlink.api import SolveCommandOutcome, post_solve_command_outcome

    bundle_path, manifest = _write_return(harness.workspace)
    _accept(harness.store, "cmd-1", bundle_path, manifest)
    assert harness.store.claim("cmd-1", 0) == 1
    before = harness.row()
    request = _legacy_request(harness)

    for report in (
        SolveCommandOutcome(commandId="cmd-1", state="refused", reason="Dismissed."),
        SolveCommandOutcome(commandId="cmd-1", state="accepted", jobId="job-5"),
        SolveCommandOutcome(commandId="cmd-unknown", state="accepted", jobId="job-6"),
    ):
        answer = asyncio.run(post_solve_command_outcome(report, request))
        # A 2xx answer: an old page drops its copy instead of showing a failure.
        assert (answer["recorded"], answer["cleared"]) == (False, True)

    assert harness.row() == before
    assert harness.store.get_operation("cmd-unknown") is None


# -- a delivery is acknowledged after its snapshot is retained -----------------------


def _claims(requests: Path) -> list[str]:
    return sorted(path.name for path in requests.iterdir() if path.name.startswith(".wg-solve-claim-"))


def _retention_passes(monkeypatch, passes: int) -> None:
    from server.cadlink import solve_command

    # raising=False: the bound is what acknowledging after retention introduced.
    monkeypatch.setattr(solve_command, "RETENTION_PASSES", passes, raising=False)


def test_a_delivery_is_acknowledged_only_after_its_snapshot_is_retained(harness: Harness) -> None:
    bundle_path, manifest = _write_return(harness.workspace)
    bundle = harness.workspace / bundle_path
    hidden = harness.workspace / "not-yet-readable"
    bundle.rename(hidden)  # a file another process holds, a drive not mounted, a folder not synced
    requests = _deliver_file(harness, bundle_path, manifest)

    _collect(harness)

    # The claim is kept: the add-in's command is not spent on a return WG could not keep.
    assert len(_claims(requests)) == 1
    assert harness.row()["snapshot_json"] is None
    hidden.rename(bundle)
    _collect(harness)

    assert list(requests.iterdir()) == []
    assert json.loads(harness.row()["snapshot_json"])["manifest_sha256"] == manifest
    # The return then leaves the WGLink folder, and the operation still prepares.
    shutil.rmtree(bundle)
    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))
    assert (summary["state"], summary["jobId"]) == ("accepted", "job-1")


def test_a_claim_whose_return_stays_unreadable_is_acknowledged_at_the_bound(
    harness: Harness, monkeypatch, caplog
) -> None:
    import logging

    _retention_passes(monkeypatch, 3)
    bundle_path, manifest = _write_return(harness.workspace)
    (harness.workspace / bundle_path).rename(harness.workspace / "gone")
    requests = _deliver_file(harness, bundle_path, manifest)

    with caplog.at_level(logging.INFO, logger="server.cadlink.solve_command"):
        for _kept in range(2):
            _collect(harness)
            assert len(_claims(requests)) == 1
        _collect(harness)

    assert list(requests.iterdir()) == []
    assert any(
        record.levelno == logging.WARNING and "cmd-1" in record.getMessage()
        for record in caplog.records
    )
    # Acknowledged anyway, and the operation waits for its return, as before.
    row = harness.row()
    assert (row["state"], row["snapshot_json"]) == ("received", None)
    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))
    assert (summary["state"], summary["reason"]) == ("needs_user_input", "preparation_failed")


def test_a_return_that_can_never_be_retained_is_acknowledged_at_once(harness: Harness) -> None:
    bundle_path, _manifest_sha = _write_return(harness.workspace)
    # The command names a manifest this return does not have.
    requests = _deliver_file(harness, bundle_path, "sha256:" + "0" * 64)

    _collect(harness)

    assert list(requests.iterdir()) == []
    assert harness.row()["snapshot_json"] is None
    summary = harness.prepare()
    assert (summary["state"], summary["reason"]) == ("rejected", "snapshot_invalid")


def test_one_unreadable_claim_never_holds_the_deliveries_behind_it(harness: Harness) -> None:
    first_path, first_manifest = _write_return(harness.workspace, "first.wgreturn")
    second_path, second_manifest = _write_return(harness.workspace, "second.wgreturn", step=b"STEP 2")
    (harness.workspace / first_path).rename(harness.workspace / "gone")
    requests = _deliver_file(harness, first_path, first_manifest, "cmd-1")
    _deliver_file(harness, second_path, second_manifest, "cmd-2")

    _collect(harness)

    assert len(_claims(requests)) == 1
    assert not (requests / "cmd-2.json").exists()
    assert harness.row("cmd-1")["snapshot_json"] is None
    assert json.loads(harness.row("cmd-2")["snapshot_json"])["manifest_sha256"] == second_manifest


# -- a dismissal is reconciled with the jobs store first -----------------------------


class _JobStore:
    """The jobs store as the app wires it: ``job_for_submission_key``, which can fail."""

    def __init__(self, harness: Harness) -> None:
        self.harness = harness
        self.error: BaseException | None = None

    def job_for_submission_key(self, key: str) -> str | None:
        if self.error is not None:
            raise self.error
        return self.harness.jobs.get(key)


def _cancel(harness: Harness, jobs: _JobStore, operation_id: str = "cmd-1") -> dict[str, Any]:
    from server.cadlink.api import post_cancel_cad_operation

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        cadlink_store=harness.store,
        data_dir=str(harness.data_dir),
        jobs_runtime=SimpleNamespace(store=jobs, submit=None, events=None),
        update_restart=None,
        cad_workspace=None,
    )))
    return asyncio.run(post_cancel_cad_operation(operation_id, request))


def _interrupted_while_bound(harness: Harness) -> None:
    """Submission raised after the jobs system may have committed, and its store was unreadable."""

    _received(harness)
    revision = _revision(harness.store, _setup())
    ctx = harness.context()

    def unreadable(_key: str) -> str | None:
        raise sqlite3.OperationalError("database is locked")

    ctx.job_for_submission = unreadable
    harness.submit_error = RuntimeError("connection reset after commit")
    summary = asyncio.run(prepare_operation(ctx, "cmd-1", PreparationInput(setup_revision_id=revision)))
    assert (summary["state"], summary["reason"]) == ("needs_user_input", "interrupted")
    assert harness.row()["request_json"] is not None  # still bound: a job may exist


def test_a_dismissal_follows_a_job_the_operation_already_made(harness: Harness) -> None:
    _interrupted_while_bound(harness)
    harness.jobs["cad-solve:cmd-1"] = "job-1"  # it did

    summary = _cancel(harness, _JobStore(harness))

    # The job exists: the operation is accepted with it, and the user cancels
    # the job in the jobs list. It never reads "cancelled" beside a running job.
    assert (summary["state"], summary["jobId"]) == ("accepted", "job-1")
    assert (harness.row()["state"], harness.row()["job_id"]) == ("accepted", "job-1")


def test_a_dismissal_waits_while_wg_cannot_tell_whether_a_job_exists(harness: Harness) -> None:
    from fastapi import HTTPException

    _interrupted_while_bound(harness)
    before = harness.row()
    jobs = _JobStore(harness)
    jobs.error = sqlite3.OperationalError("database is locked")

    with pytest.raises(HTTPException) as refused:
        _cancel(harness, jobs)

    assert refused.value.status_code == 409
    assert "cannot confirm" in str(refused.value.detail)
    assert harness.row() == before
    # Once the jobs store answers, the dismissal goes through.
    jobs.error = None
    assert _cancel(harness, jobs)["state"] == "cancelled"


def test_a_request_that_was_never_bound_is_dismissed_without_the_jobs_store(
    harness: Harness,
) -> None:
    _received(harness)
    jobs = _JobStore(harness)
    jobs.error = sqlite3.OperationalError("database is locked")

    assert _cancel(harness, jobs)["state"] == "cancelled"


# -- correlation in WG's logs --------------------------------------------------------


def test_each_attempt_logs_its_operation_and_generation(harness: Harness, caplog) -> None:
    import logging

    _received(harness)

    def logged(*parts: str) -> bool:
        return any(
            all(part in record.getMessage() for part in parts)
            for record in caplog.records
            if record.name == "server.cadlink.preparation"
        )

    with caplog.at_level(logging.INFO, logger="server.cadlink.preparation"):
        assert harness.prepare()["reason"] == "setup_required"
        assert harness.prepare(setup_revision_id=_revision(harness.store, _setup()))["state"] == "accepted"

    # The first attempt: claimed, validated, and waiting for its settings.
    assert logged("cmd-1", "attempt 1", "claimed")
    assert logged("cmd-1", "attempt 1", "stage validating")
    assert logged("cmd-1", "attempt 1", "finished needs_user_input", "setup_required")
    # The second: its own generation at every step, and the job it made.
    assert logged("cmd-1", "attempt 2", "claimed")
    for stage in ("validating", "preparing-mesh", "ready"):
        assert logged("cmd-1", "attempt 2", f"stage {stage}")
    assert logged("cmd-1", "attempt 2", "finished accepted", "job-1")


def test_a_dismissal_waits_while_a_bound_attempt_runs_and_wg_cannot_read_the_jobs(
    harness: Harness,
) -> None:
    from fastapi import HTTPException

    _received(harness)
    generation = harness.store.claim("cmd-1", 0)
    assert harness.store.bind_request(
        "cmd-1", generation, setup_revision_id="wgs_x", request_json='{"x":1}'
    ) is not None  # an attempt is submitting this request
    before = harness.row()
    jobs = _JobStore(harness)
    jobs.error = sqlite3.OperationalError("database is locked")

    with pytest.raises(HTTPException) as refused:
        _cancel(harness, jobs)

    # A dismissal would turn the attempt's "interrupted" into "cancelled",
    # whatever job its submission made.
    assert refused.value.status_code == 409
    assert harness.row() == before
    # Once the jobs store answers, the dismissal fences the attempt as before,
    # and a job it made still stands as the outcome.
    jobs.error = None
    assert _cancel(harness, jobs)["state"] == "cancel_requested"
    recorded = harness.store.record_outcome("cmd-1", generation, "accepted", job_id="job-1")
    assert recorded is not None and (recorded["state"], recorded["job_id"]) == ("accepted", "job-1")


# -- the update restart latch --------------------------------------------------------
#
# After a restart is approved WG starts no new installation-owned work
# (UPDATE-TRANSACTION-CONTRACT.md 4.2; CAD-OPERATIONS.md, "Preparation"). These go
# through the latch as the app wires it (``_preparation_context``).


def _latched_state(harness: Harness, approval: Any) -> SimpleNamespace:
    return SimpleNamespace(
        cadlink_store=harness.store,
        data_dir=str(harness.data_dir),
        jobs_runtime=None,
        update_restart=approval,
        cad_workspace=SimpleNamespace(selected_path=lambda: harness.workspace),
    )


def test_the_prepare_route_refuses_while_an_update_restart_is_pending(
    harness: Harness, monkeypatch
) -> None:
    from server.cadlink import api
    from server.updates.restart import RestartApproval

    _received(harness)
    before = harness.row()
    approval = RestartApproval()
    approval.approve("0.3.4")
    started: list[Any] = []

    async def prepare_stand_in(_ctx: Any, operation_id: str, _request: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"operationId": operation_id}

    monkeypatch.setattr(api, "prepare_operation", prepare_stand_in)
    monkeypatch.setattr(api, "_track", lambda _state, task: started.append(task))
    request = SimpleNamespace(app=SimpleNamespace(state=_latched_state(harness, approval)))

    response = asyncio.run(api.post_prepare_cad_operation("cmd-1", api.PrepareOperationRequest(), request))

    # Refused before anything starts, with the envelope every latched route uses.
    assert started == []
    assert response.status_code == 409
    body = json.loads(response.body)
    assert (body["error"]["code"], body["error"]["retryable"]) == ("update_restart_pending", True)
    assert "about to restart to install 0.3.4" in body["detail"]
    assert harness.row() == before
    approval.release("the launcher discarded the request")
    asyncio.run(api.post_prepare_cad_operation("cmd-1", api.PrepareOperationRequest(), request))
    assert len(started) == 1


def test_the_delivery_loop_starts_nothing_while_an_update_restart_is_pending(
    harness: Harness,
) -> None:
    from server.cadlink.api import _preparation_context
    from server.cadlink.preparation import run_delivery_pass
    from server.updates.restart import RestartApproval

    first_path, first_manifest = _write_return(harness.workspace, "first.wgreturn")
    _accept(harness.store, "cmd-1", first_path, first_manifest)  # received before the approval
    second_path, second_manifest = _write_return(harness.workspace, "second.wgreturn", step=b"STEP 2")
    requests = _deliver_file(harness, second_path, second_manifest, "cmd-2")
    approval = RestartApproval()
    approval.approve("0.3.4")
    state = _latched_state(harness, approval)

    def one_pass() -> list[str]:
        async def go() -> list[str]:
            started: list[asyncio.Future[Any]] = []
            ids = await run_delivery_pass(
                _preparation_context(state, workspace_root=harness.workspace.resolve()),
                spawn=lambda _operation_id, coroutine: started.append(asyncio.ensure_future(coroutine)),
            )
            await asyncio.gather(*started)
            return ids

        return asyncio.run(go())

    assert one_pass() == []

    assert harness.row("cmd-1")["state"] == "received"
    # A delivery waits on disk, untouched, as a refused ingest leaves its return.
    assert [path.name for path in requests.iterdir()] == ["cmd-2.json"]
    assert harness.store.get_operation("cmd-2") is None
    # Called off without a restart: what the latch held proceeds.
    approval.release("the launcher discarded the request")
    assert one_pass() == ["cmd-1", "cmd-2"]


def test_a_preparation_an_update_restart_overtakes_before_its_claim_changes_nothing(
    harness: Harness,
) -> None:
    _received(harness)
    before = harness.row()
    # Approved after the loop listed it, or after the route let it through.
    harness.blocked = "Waveguide Generator is about to restart to install 0.3.4."

    summary = asyncio.run(prepare_operation(
        harness.context(), "cmd-1", PreparationInput(), expected_generation=0
    ))

    assert (summary["state"], summary["attemptGeneration"]) == ("received", 0)
    assert harness.row() == before
    assert harness.ingest.calls == [] and harness.submitted == []
    harness.blocked = None
    assert harness.prepare(setup_revision_id=_revision(harness.store, _setup()))["state"] == "accepted"


def test_an_update_restart_approved_while_a_pass_collects_starts_nothing(
    harness: Harness, monkeypatch
) -> None:
    from server.cadlink import preparation

    bundle_path, manifest = _write_return(harness.workspace)
    _deliver_file(harness, bundle_path, manifest)
    real_collect = preparation.collect_solve_deliveries

    def collect_then_approve(*args: Any, **kwargs: Any) -> Any:
        answer = real_collect(*args, **kwargs)
        harness.blocked = "Waveguide Generator is about to restart to install 0.3.4."
        return answer

    monkeypatch.setattr(preparation, "collect_solve_deliveries", collect_then_approve)

    async def one_pass() -> list[str]:
        started: list[asyncio.Future[Any]] = []
        ids = await preparation.run_delivery_pass(
            harness.context(),
            spawn=lambda _operation_id, coroutine: started.append(asyncio.ensure_future(coroutine)),
        )
        await asyncio.gather(*started)
        return ids

    assert asyncio.run(one_pass()) == []
    row = harness.row()
    assert (row["state"], row["attempt_generation"]) == ("received", 0)
    assert harness.ingest.calls == []


def test_a_requeue_after_an_update_restart_changes_only_what_it_read(harness: Harness) -> None:
    from server.cadlink.operations import REASON_UPDATE_RESTART_PENDING as HELD

    _received(harness)
    generation = harness.store.claim("cmd-1", 0)
    assert harness.store.record_outcome("cmd-1", generation, "needs_user_input", reason=HELD) is not None
    parked = harness.row()

    # A generation the caller did not read, or a reason it does not wait for: nothing changes.
    assert harness.store.requeue_operation("cmd-1", generation - 1, reason=HELD) is None
    assert harness.store.requeue_operation("cmd-1", generation, reason="interrupted") is None
    assert harness.row() == parked
    # The user's own attempt took it over meanwhile: the older read changes nothing.
    newer = harness.store.claim("cmd-1", generation)
    assert harness.store.record_outcome("cmd-1", newer, "needs_user_input", reason=HELD) is not None
    assert harness.store.requeue_operation("cmd-1", generation, reason=HELD) is None
    # The generation it read, and the reason it waits for: received again, at that generation.
    requeued = harness.store.requeue_operation("cmd-1", newer, reason=HELD)
    assert requeued is not None
    assert (requeued["state"], requeued["attempt_generation"], requeued["reason"]) == ("received", newer, None)
    # Dismissed meanwhile: a cancelled solve is never queued again.
    assert harness.store.request_cancel("cmd-1")["state"] == "cancelled"
    assert harness.store.requeue_operation("cmd-1", newer, reason=HELD) is None
    assert harness.row()["state"] == "cancelled"
