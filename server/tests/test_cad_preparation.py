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
)
from server.cadlink.setup import setup_content, setup_digest, validate_setup
from server.cadlink.solve_command import oldest_pending_solve_command
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
    from server.cadlink.api import _pending_solve_command

    bundle_path, manifest = _write_return(harness.workspace)
    requests = harness.data_dir / "ipc" / "wglink" / ".wg-solve-requests"
    requests.mkdir(parents=True)
    (requests / "cmd-1.json").write_text(json.dumps({
        "schemaVersion": 3, "target": "waveguide-generator", "commandId": "cmd-1",
        "returnId": "wgr_1", "bundlePath": bundle_path, "manifestSha256": manifest,
        "requestedAt": "2026-09-13T01:00:00Z",
    }))

    _pending_solve_command(harness.data_dir, harness.workspace.resolve(), harness.store)

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


def test_no_solve_is_submitted_while_an_update_restart_is_pending(harness: Harness) -> None:
    _received(harness)
    harness.blocked = "An update restart is pending."

    summary = harness.prepare(setup_revision_id=_revision(harness.store, _setup()))

    assert (summary["state"], summary["reason"]) == ("needs_user_input", "submission_refused")
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
    # Only the unfinished request is handed out again; nothing finished is.
    pending = oldest_pending_solve_command(store)
    assert pending is not None and pending.command_id == "cmd-open"
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
    from server.cadlink.api import _pending_solve_command

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

    _pending_solve_command(harness.data_dir, harness.workspace.resolve(), harness.store)

    assert list(requests.iterdir()) == []
    assert harness.row("cmd-bad")["snapshot_json"] is None
    assert json.loads(harness.row("cmd-good")["snapshot_json"])["manifest_sha256"] == good_manifest
    with pytest.raises(WgReturnError, match="nested too deeply"):
        read_wgreturn(nested)


def test_an_unexpected_failure_never_strands_the_operation(harness: Harness) -> None:
    _received(harness)
    # A setup revision a different build stored, which this one cannot read.
    unreadable = harness.store.create_setup_revision('{"geometry": {}}', "sha256:" + "0" * 64)

    summary = harness.prepare(setup_revision_id=str(unreadable["revision_id"]))

    assert (summary["state"], summary["reason"]) == ("needs_user_input", "preparation_failed")
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
