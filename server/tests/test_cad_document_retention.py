"""Pending states survive until pinning; pins survive job-history removal."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from server.app import create_app
from server.cadlink import api as cadlink_api
from server.cadlink.api import ArchiveRunDocumentRequest, archive_run_document
from server.workspace.archive import archive_cad_document
from server.tests.cad_document_fixtures import retained_document_record


STEM = "Tritonia"


def app_with_runs(tmp_path: Path):
    app = create_app(data_dir=tmp_path / "data")
    runs = tmp_path / "runs"
    runs.mkdir()
    app.state.workspace.select(runs)
    app.state.cadlink_store.claim_cad_document_lineage("urn:synthetic:tritonia", STEM)
    app.state.cad_workspace.set_capture_mode("run")
    return app, runs


def bundle_for(tmp_path: Path, digest: str) -> Path:
    bundle = tmp_path / "wgreturn" / f"{digest}.wgreturn"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "document.f3d").write_bytes(f"model-{digest}".encode())
    return bundle


def record_for(app, digest: str, *, at: str) -> dict[str, object]:
    lineage = app.state.cadlink_store.list_cad_document_projects()[0]["lineage_id"]
    evidence = retained_document_record(app, f"model-{digest}".encode(), f"sha256:{digest}", lineage)
    return {
        **evidence,
        "ingest_id": evidence["ingest_id"],
        "return_id": f"wgr_{digest}",
        "created_at": at,
        "document": {
            "name": STEM,
            "native_id": None,
            "return_state_hash": f"sha256:{digest}",
            "file": "document.f3d",
        },
        "project": {"archive_stem": STEM, "lineage_id": lineage},
    }


def capture_directly(app, runs: Path, tmp_path: Path, digest: str, *, at: str) -> None:
    """A capture with no retention input, as the very first one always is."""

    archive_cad_document(bundle_for(tmp_path, digest), record_for(app, digest, at=at), runs, STEM)


def imported_run(
    app,
    job_id: str,
    digest: str,
    *,
    status: str = "queued",
    archived_at: str | None = None,
    has_results: bool = True,
) -> None:
    """One run solved from a captured model state, as the store holds it."""

    metadata: dict[str, object] = {
        "imported_geometry": {
            "ingest_id": f"wgi_{digest}",
            "anchor_lineage_id": app.state.cadlink_store.list_cad_document_projects()[0]["lineage_id"],
            "archive_stem": STEM,
            "document": {"name": STEM, "return_state_hash": f"sha256:{digest}"},
        },
    }
    if archived_at is not None:
        metadata["archived_at"] = archived_at
    store = app.state.jobs_runtime.store
    store.initialize()
    now = "2026-08-20T12:00:00Z"
    store.create_job(
        {
            "id": job_id,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "completed_at": now if status == "complete" else None,
            "progress": 1.0 if status == "complete" else 0.0,
            "stage": status,
            "stage_message": status,
            "config_json": {"geometry": {"type": "imported", "ingest_id": f"wgi_{digest}"}},
            "config_summary_json": {"geometry_type": "imported"},
            "has_results": has_results,
            "has_mesh_artifact": True,
            "task_metadata": metadata,
        }
    )


def capture_through_the_app(app, tmp_path: Path, digest: str, *, at: str) -> None:
    """An ingestion's capture, which is where the retention decision is made."""

    asyncio.run(
        cadlink_api._archive_cad_document(
            app.state.workspace,
            app.state.cad_workspace,
            app.state.jobs_runtime,
            app.state.cadlink_store,
            bundle_for(tmp_path, digest),
            record_for(app, digest, at=at),
        )
    )


def archive_request(app, digest: str, run: str = "14_Tritonia") -> ArchiveRunDocumentRequest:
    store = app.state.jobs_runtime.store
    store.initialize()
    rows, _ = store.list_jobs(limit=100)
    matching = next((row for row in rows if
        ((row.get('task_metadata') or {}).get('imported_geometry') or {}).get('document', {}).get('return_state_hash') == f'sha256:{digest}'), None)
    job_id = matching['id'] if matching else f'placement-{digest}'
    if matching is None:
        imported_run(app, job_id, digest, status='complete', archived_at='already-archived')
    store.mutate_job_metadata(job_id, {'archive': {'key': 'name:Tritonia', 'folder': STEM, 'run': run}})
    return ArchiveRunDocumentRequest(jobId=job_id)


def test_a_queued_run_keeps_its_model_when_a_changed_one_is_ingested(
    tmp_path: Path,
) -> None:
    """Acceptance 1: queue A, capture B, then archive A.

    The run must get A's bytes and A's sidecar. Before the retention rule this
    was the whole defect: capturing B deleted A, and archiving the A run then
    filed nothing while answering HTTP 200.
    """

    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(app, "job-a", "aaa", status="queued")

    capture_through_the_app(app, tmp_path, "bbb", at="2026-08-21T09:00:00Z")

    # Both states are on disk: the newest, and the one a run still needs.
    assert sorted(path.name for path in (runs / STEM / "cad").iterdir()) == [
        "Tritonia_260820-0900_aaa.f3d",
        "Tritonia_260820-0900_aaa.json",
        "Tritonia_260821-0900_bbb.f3d",
        "Tritonia_260821-0900_bbb.json",
    ]

    placed = asyncio.run(
        archive_run_document(archive_request(app, "aaa"), SimpleNamespace(app=app))
    )

    assert placed == {"placed": True, "relativePath": "cad/Tritonia_260820-0900_aaa.pinned.f3d", "modelFile": "cad/Tritonia_260820-0900_aaa.pinned.f3d"}
    run_folder = runs / STEM / "cad"
    assert run_folder.joinpath("Tritonia_260820-0900_aaa.pinned.f3d").read_bytes() == b"model-aaa"
    sidecar = json.loads(
        run_folder.joinpath("Tritonia_260820-0900_aaa.pinned.cad.json").read_text(encoding="utf-8")
    )
    assert sidecar["returnStateHash"] == "sha256:aaa"


def test_a_run_archived_before_its_capture_finishes_still_gets_the_copy(
    tmp_path: Path,
) -> None:
    """Acceptance 2: a solve can outrun the capture it was started from.

    The capture is asynchronous because it copies tens of megabytes out of a
    possibly cloud-synced folder. A fast solve reaches the archive route while
    that copy is still in flight, so the route joins the capture instead of
    reporting that there is nothing to place.
    """

    app, runs = app_with_runs(tmp_path)
    gate = threading.Event()
    real_capture = cadlink_api.archive_cad_document

    def held_capture(*args: object, **kwargs: object) -> object:
        assert gate.wait(timeout=10), "the capture was never released"
        return real_capture(*args, **kwargs)

    async def scenario() -> dict[str, object]:
        cadlink_api.archive_cad_document = held_capture  # type: ignore[assignment]
        try:
            cadlink_api._schedule_cad_document_capture(
                app.state,
                app.state.cadlink_store,
                bundle_for(tmp_path, "ccc"),
                record_for(app, "ccc", at="2026-08-22T09:00:00Z"),
            )
            # Let the capture reach the worker thread and block there.
            for _ in range(50):
                await asyncio.sleep(0)
            assert not (runs / STEM / "cad").exists()

            placing = asyncio.ensure_future(
                archive_run_document(
                    archive_request(app, "ccc", "15_Tritonia"), SimpleNamespace(app=app)
                )
            )
            for _ in range(50):
                await asyncio.sleep(0)
            # Still waiting: it has not decided the document is missing.
            assert not placing.done()

            gate.set()
            return await placing
        finally:
            cadlink_api.archive_cad_document = real_capture  # type: ignore[assignment]
            gate.set()

    placed = asyncio.run(scenario())

    assert placed == {"placed": True, "relativePath": "cad/Tritonia_260822-0900_ccc.pinned.f3d", "modelFile": "cad/Tritonia_260822-0900_ccc.pinned.f3d"}
    assert (
        runs / STEM / "cad" / "Tritonia_260822-0900_ccc.pinned.f3d"
    ).read_bytes() == b"model-ccc"


def test_archiving_a_run_twice_never_substitutes_the_newer_model(
    tmp_path: Path,
) -> None:
    """Acceptance 3: a retried archive is a no-op, not a re-attribution.

    A retry after a restart must answer identically, and must never hand the
    run the model that superseded the one it was solved from.
    """

    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(app, "job-a", "aaa", status="complete")
    capture_through_the_app(app, tmp_path, "bbb", at="2026-08-21T09:00:00Z")

    first = asyncio.run(
        archive_run_document(archive_request(app, "aaa"), SimpleNamespace(app=app))
    )
    again = asyncio.run(
        archive_run_document(archive_request(app, "aaa"), SimpleNamespace(app=app))
    )

    assert first == again == {
        "placed": True,
        "relativePath": "cad/Tritonia_260820-0900_aaa.pinned.f3d",
        "modelFile": "cad/Tritonia_260820-0900_aaa.pinned.f3d",
    }
    document = runs / STEM / "cad" / "Tritonia_260820-0900_aaa.pinned.f3d"
    assert document.read_bytes() == b"model-aaa"
    assert document.read_bytes() != b"model-bbb"
    sidecar = json.loads(
        document.with_suffix(".cad.json").read_text(encoding="utf-8")
    )
    assert sidecar["returnStateHash"] == "sha256:aaa"


def test_a_superseded_model_is_dropped_once_every_run_has_released_it(
    tmp_path: Path,
) -> None:
    """The other half of retention: the project view returns to one model.

    Retention is until release, not forever. Once the runs that referenced a
    superseded state have their own copies, capturing the next state prunes it
    exactly as it always did.
    """

    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(
        app, "job-a", "aaa", status="complete", archived_at="2026-08-20T13:00:00Z"
    )

    capture_through_the_app(app, tmp_path, "bbb", at="2026-08-21T09:00:00Z")

    assert sorted(path.name for path in (runs / STEM / "cad").iterdir()) == [
        "Tritonia_260821-0900_bbb.f3d",
        "Tritonia_260821-0900_bbb.json",
    ]


def test_pinned_versions_remain_after_all_runs_release_them(
    tmp_path: Path,
) -> None:
    """A released state does not have to wait for another ingestion to go.

    Otherwise a project that is never swept again keeps every state a run ever
    referenced, and the one-current-model view never comes back.
    """

    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(app, "job-a", "aaa", status="complete")
    capture_through_the_app(app, tmp_path, "bbb", at="2026-08-21T09:00:00Z")

    # The A run is archived and marked archived, so it has released A.
    asyncio.run(archive_run_document(archive_request(app, "aaa"), SimpleNamespace(app=app)))
    app.state.jobs_runtime.store.mutate_job_metadata(
        "job-a", {"archived_at": "2026-08-21T10:00:00Z"}
    )
    # A second run, of the current state, archives next.
    asyncio.run(
        archive_run_document(
            archive_request(app, "bbb", "15_Tritonia"), SimpleNamespace(app=app)
        )
    )

    app.state.jobs_runtime.store.delete_job_with_event("job-a")
    capture_through_the_app(app, tmp_path, "ccc", at="2026-08-22T09:00:00Z")
    assert sorted(path.name for path in (runs / STEM / "cad").iterdir()) == [
        "Tritonia_260820-0900_aaa.pinned.cad.json", "Tritonia_260820-0900_aaa.pinned.f3d",
        "Tritonia_260821-0900_bbb.pinned.cad.json", "Tritonia_260821-0900_bbb.pinned.f3d",
        "Tritonia_260822-0900_ccc.f3d", "Tritonia_260822-0900_ccc.json",
    ]
    assert len(list((runs / STEM).rglob("*.f3d"))) == 3


def test_a_missing_model_is_reported_rather_than_answered_as_a_success(
    tmp_path: Path,
) -> None:
    """A copy that was asked for and not made must say so, and say whether
    asking again could still produce it."""

    app, _runs = app_with_runs(tmp_path)

    answer = asyncio.run(
        archive_run_document(archive_request(app, "zzz"), SimpleNamespace(app=app))
    )

    assert answer["placed"] is False
    assert answer["retryable"] is False
    assert "not in the project archive" in str(answer["reason"])


def test_only_runs_that_still_owe_an_archive_retain_a_model(tmp_path: Path) -> None:
    """The retention query is the whole retention decision, so pin it.

    A failed run is never archived and holds nothing; an archived one has its
    own copy already. Retaining either would make the project folder grow
    without bound, which is what the pruning exists to prevent.
    """

    app, _runs = app_with_runs(tmp_path)
    imported_run(app, "queued", "aaa", status="queued")
    imported_run(app, "running", "bbb", status="running")
    imported_run(app, "unarchived", "ccc", status="complete")
    imported_run(
        app, "archived", "ddd", status="complete", archived_at="2026-08-20T13:00:00Z"
    )
    imported_run(app, "failed", "eee", status="error", has_results=False)
    imported_run(app, "resultless", "fff", status="complete", has_results=False)

    rows = app.state.jobs_runtime.store.unreleased_cad_return_states()

    assert sorted(row["return_state_hash"] for row in rows) == [
        "sha256:aaa",
        "sha256:bbb",
        "sha256:ccc",
    ]
    assert {row["archive_stem"] for row in rows} == {STEM}


def test_dont_keep_captures_nothing(tmp_path):
    app, runs = app_with_runs(tmp_path)
    app.state.cad_workspace.set_capture_mode("off")
    capture_through_the_app(app, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    assert list(runs.iterdir()) == []
    answer = asyncio.run(archive_run_document(archive_request(app, "aaa"), SimpleNamespace(app=app)))
    assert answer["placed"] is False
    assert answer["retryable"] is False
    assert "Don't keep" in answer["reason"]
    assert list(runs.iterdir()) == []


def test_two_different_runs_pin_one_file_even_after_dont_keep(tmp_path):
    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    for job_id, number in [("job-a", 14), ("job-b", 15)]:
        imported_run(app, job_id, "aaa", status="complete")
        app.state.jobs_runtime.store.mutate_job_metadata(job_id, {"archive": {"key": "name:Tritonia", "folder": STEM, "run": f"{number}_Tritonia"}})
        answer = asyncio.run(archive_run_document(ArchiveRunDocumentRequest(jobId=job_id), SimpleNamespace(app=app)))
        assert answer == {"placed": True, "relativePath": "cad/Tritonia_260820-0900_aaa.pinned.f3d", "modelFile": "cad/Tritonia_260820-0900_aaa.pinned.f3d"}
        app.state.cad_workspace.set_capture_mode("off")
    assert len(list((runs / STEM).rglob("*.f3d"))) == 1


def test_switching_to_keep_does_not_block_archiving_an_uncaptured_state(tmp_path):
    app, runs = app_with_runs(tmp_path)
    app.state.cad_workspace.set_capture_mode("off")
    capture_through_the_app(app, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    app.state.cad_workspace.set_capture_mode("run")
    result = asyncio.run(archive_run_document(archive_request(app, "aaa"), SimpleNamespace(app=app)))
    assert result["placed"] is False
    assert result["retryable"] is False
    assert result["modelFile"] is None
    assert list(runs.iterdir()) == []


@pytest.mark.parametrize("old_reference", [None, "cad/Tritonia_260820-0900_aaa.f3d"])
@pytest.mark.parametrize("displaced", [False, True])
def test_archive_retry_preserves_the_existing_run_record_model_reference(tmp_path, old_reference, displaced):
    app, runs = app_with_runs(tmp_path)
    request = archive_request(app, "aaa")
    run = runs / STEM / "14_Tritonia"
    run.mkdir(parents=True)
    record = {"schemaVersion": 1, "run": {"jobId": request.jobId}, "cad": {"returnStateHash": "sha256:aaa"}}
    if old_reference:
        record["cad"]["modelFile"] = old_reference
    if displaced:
        (run / "run.json").write_text("user-owned content")
        run = run / "archive"
        run.mkdir()
    record_path = run / "run.json"
    raw = json.dumps(record).encode()
    record_path.write_bytes(raw)
    # The first archive had no model, or its model was removed after archiving.
    before = asyncio.run(archive_run_document(request, SimpleNamespace(app=app)))
    assert before["modelFile"] == old_reference
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    after = asyncio.run(archive_run_document(request, SimpleNamespace(app=app)))
    assert after["placed"] is True
    assert after["modelFile"] == old_reference
    assert record_path.read_bytes() == raw


@pytest.mark.parametrize("content", ["user-owned content", '{"schemaVersion":1,"run":{"jobId":"another"}}'])
def test_unrelated_run_record_is_left_to_the_existing_collision_writer(tmp_path, content):
    app, runs = app_with_runs(tmp_path)
    request = archive_request(app, "aaa")
    run = runs / STEM / "14_Tritonia"
    run.mkdir(parents=True)
    (run / "run.json").write_text(content)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    result = asyncio.run(archive_run_document(request, SimpleNamespace(app=app)))
    assert result["placed"] is True
    assert result["modelFile"] == "cad/Tritonia_260820-0900_aaa.pinned.f3d"
    assert (run / "run.json").read_text() == content


@pytest.mark.parametrize("read_target", ["model", "sidecar", "manifest"])
def test_transient_verification_io_is_retryable_and_cannot_release_retention(tmp_path, monkeypatch, read_target):
    from server.jobs.runtime import JobConflictError

    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(app, "job-a", "aaa", status="complete")
    payload = ArchiveRunDocumentRequest(jobId="job-a")
    original = runs / STEM / "cad" / "Tritonia_260820-0900_aaa.f3d"
    real_open = Path.open
    failing = True

    def transient_open(path, *args, **kwargs):
        target = {"model": path == original, "sidecar": path == original.with_suffix(".json"),
                  "manifest": path.name == "wgreturn.json"}[read_target]
        if failing and target:
            raise OSError("transient cloud read failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", transient_open)
    answer = asyncio.run(archive_run_document(payload, SimpleNamespace(app=app)))
    assert answer["placed"] is False
    assert answer["retryable"] is True

    async def refuse_release():
        nonlocal failing
        runtime = app.state.jobs_runtime
        try:
            with pytest.raises(JobConflictError):
                await runtime.patch_metadata("job-a", {"archived_at": "now"})
            with pytest.raises(JobConflictError):
                await runtime.delete("job-a")
            row = runtime.store.get_job_row("job-a")
            assert row is not None
            assert not row["task_metadata"].get("archived_at")
            failing = False
            await cadlink_api._archive_cad_document(
                app.state.workspace, app.state.cad_workspace, runtime, app.state.cadlink_store,
                bundle_for(tmp_path, "bbb"), record_for(app, "bbb", at="2026-08-21T09:00:00Z"),
            )
            assert original.read_bytes() == b"model-aaa"
            answer = await archive_run_document(payload, SimpleNamespace(app=app))
            assert answer["placed"] is True
            pinned = runs / STEM / answer["relativePath"]
            await cadlink_api._archive_cad_document(
                app.state.workspace, app.state.cad_workspace, runtime, app.state.cadlink_store,
                bundle_for(tmp_path, "ccc"), record_for(app, "ccc", at="2026-08-22T09:00:00Z"),
            )
            assert pinned.read_bytes() == b"model-aaa"
        finally:
            await runtime.shutdown()

    asyncio.run(refuse_release())


def test_deleting_a_completed_job_before_archive_permanently_protects_its_version(tmp_path):
    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(app, "job-a", "aaa", status="complete")

    async def delete():
        try:
            await app.state.jobs_runtime.delete("job-a")
            assert app.state.jobs_runtime.store.get_job_row("job-a") is None
            await cadlink_api._archive_cad_document(
                app.state.workspace, app.state.cad_workspace, app.state.jobs_runtime, app.state.cadlink_store,
                bundle_for(tmp_path, "bbb"), record_for(app, "bbb", at="2026-08-21T09:00:00Z"),
            )
        finally:
            await app.state.jobs_runtime.shutdown()

    asyncio.run(delete())
    document = runs / STEM / "cad" / "Tritonia_260820-0900_aaa.pinned.f3d"
    assert document.read_bytes() == b"model-aaa"
    assert document.with_suffix(".cad.json").is_file()
    assert len(list((runs / STEM).rglob("*.f3d"))) == 2


@pytest.mark.parametrize("recorded", [False, True])
def test_delete_unavailable_root_explains_location_and_requires_explicit_bypass(tmp_path, recorded):
    from server.jobs.runtime import JobProtectionError

    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(app, "job-a", "aaa", status="complete")
    runtime = app.state.jobs_runtime
    if recorded:
        runtime.store.mutate_job_metadata("job-a", {"archived_path": str(runs / STEM / "1_run")})
    offline = runs.with_name("offline")
    runs.rename(offline)
    # Selecting another root must not silently redirect a recorded run.
    if recorded:
        replacement = tmp_path / "replacement"
        replacement.mkdir()
        app.state.workspace.select(replacement)
    before = {p.relative_to(offline): p.read_bytes() for p in offline.rglob("*") if p.is_file()}

    async def scenario():
        try:
            with pytest.raises(JobProtectionError) as error:
                await runtime.delete("job-a")
            assert str(error.value) == (
                f"The folder for this run is not available: {runs}. Reconnect it and try again, "
                "or delete the run without protecting its Fusion model."
            )
            assert runtime.store.get_job_row("job-a") is not None
            await runtime.delete("job-a", skip_model_protection=True)
            assert runtime.store.get_job_row("job-a") is None
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())
    assert {p.relative_to(offline): p.read_bytes() for p in offline.rglob("*") if p.is_file()} == before


def test_delete_conflict_reports_file_and_bypass_does_not_touch_it(tmp_path):
    from server.jobs.runtime import JobProtectionError

    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(app, "job-a", "aaa", status="complete")
    conflict = runs / STEM / "cad" / "Tritonia_260820-0900_aaa.pinned.f3d"
    conflict.write_bytes(b"unrelated")
    before = {p: p.read_bytes() for p in runs.rglob("*") if p.is_file()}

    async def scenario():
        runtime = app.state.jobs_runtime
        try:
            with pytest.raises(JobProtectionError) as error:
                await runtime.delete("job-a")
            assert str(conflict) in str(error.value)
            assert "occupied" in str(error.value)
            assert runtime.store.get_job_row("job-a") is not None
            await runtime.delete("job-a", skip_model_protection=True)
            assert runtime.store.get_job_row("job-a") is None
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())
    assert {p: p.read_bytes() for p in runs.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("windows_shape", [False, True])
def test_root_io_failure_exposes_the_cause_and_allows_confirmed_bypass(tmp_path, monkeypatch, windows_shape):
    from server.jobs.runtime import JobProtectionError

    app, runs = app_with_runs(tmp_path)
    imported_run(app, "job-a", "aaa", status="complete")
    # str(OSError) would show this path through repr(), with doubled backslashes.
    shown = r"C:\Users\example\Documents\WG\runs" if windows_shape else str(runs)

    def unreadable():
        raise PermissionError(13, "Permission denied", shown)

    monkeypatch.setattr(app.state.workspace, "path", unreadable)

    async def scenario():
        runtime = app.state.jobs_runtime
        try:
            with pytest.raises(JobProtectionError) as error:
                await runtime.delete("job-a")
            assert shown in str(error.value)
            assert "Permission denied" in str(error.value)
            await runtime.delete("job-a", skip_model_protection=True)
            assert runtime.store.get_job_row("job-a") is None
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("protect_via", ["archive_document", "archive_metadata", "legacy_archive"])
def test_already_protected_run_can_be_deleted_with_its_root_offline(tmp_path, protect_via):
    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(app, "job-a", "aaa", status="complete")

    async def scenario():
        runtime = app.state.jobs_runtime
        try:
            if protect_via == "archive_document":
                answer = await archive_run_document(ArchiveRunDocumentRequest(jobId="job-a"), SimpleNamespace(app=app))
                assert answer["placed"] is True
            else:
                await runtime.patch_metadata("job-a", {"archived_at": "now"})
                if protect_via == "legacy_archive":
                    # Older versions recorded completion without a pin receipt.
                    runtime.store.mutate_job_metadata("job-a", {"cad_document_protection": None})
            offline = runs.with_name("offline")
            runs.rename(offline)
            # Reload from persistence; a pin receipt must survive runtime restart.
            row = runtime.store.get_job_row("job-a")
            if protect_via != "legacy_archive":
                assert row["task_metadata"]["cad_document_protection"]["return_state_hash"] == "sha256:aaa"
            else:
                assert row["task_metadata"]["archived_at"] == "now"
            await runtime.delete("job-a")
            assert runtime.store.get_job_row("job-a") is None
            assert (offline / STEM / "cad" / "Tritonia_260820-0900_aaa.pinned.f3d").read_bytes() == b"model-aaa"
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_marking_archive_complete_also_protects_the_used_version(tmp_path):
    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(app, "job-a", "aaa", status="complete")

    async def complete_archive():
        try:
            await app.state.jobs_runtime.patch_metadata("job-a", {"archived_at": "now"})
            await cadlink_api._archive_cad_document(
                app.state.workspace, app.state.cad_workspace, app.state.jobs_runtime, app.state.cadlink_store,
                bundle_for(tmp_path, "bbb"), record_for(app, "bbb", at="2026-08-21T09:00:00Z"),
            )
        finally:
            await app.state.jobs_runtime.shutdown()

    asyncio.run(complete_archive())
    assert (runs / STEM / "cad" / "Tritonia_260820-0900_aaa.pinned.f3d").read_bytes() == b"model-aaa"


@pytest.mark.parametrize("check", ["directory", "links"])
def test_transient_archive_lookup_io_is_retryable(tmp_path, monkeypatch, check):
    from server.workspace import archive

    app, runs = app_with_runs(tmp_path)
    capture_directly(app, runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    payload = archive_request(app, "aaa")
    if check == "directory":
        real_iterdir = Path.iterdir

        def unreadable(path):
            if path == runs / STEM / "cad":
                raise OSError("transient directory read failure")
            return real_iterdir(path)

        monkeypatch.setattr(Path, "iterdir", unreadable)
    else:
        def unreadable(*_args, **_kwargs):
            raise OSError("transient link check failure")

        monkeypatch.setattr(archive, "path_has_reparse_point", unreadable)
    result = asyncio.run(archive_run_document(payload, SimpleNamespace(app=app)))
    assert result["placed"] is False
    assert result["retryable"] is True


def test_capture_still_in_progress_is_retryable_before_history_deletion(tmp_path, monkeypatch):
    from server.jobs.runtime import JobConflictError

    app, _runs = app_with_runs(tmp_path)
    imported_run(app, "job-a", "aaa", status="complete")
    monkeypatch.setattr(cadlink_api, "_CAPTURE_WAIT_SECONDS", 0.001)

    async def scenario():
        gate = asyncio.Event()
        capture = asyncio.create_task(gate.wait())
        key = cadlink_api._capture_key("sha256:aaa")
        cadlink_api._CAD_DOCUMENT_CAPTURES[key] = capture
        runtime = app.state.jobs_runtime
        try:
            answer = await archive_run_document(ArchiveRunDocumentRequest(jobId="job-a"), SimpleNamespace(app=app))
            assert answer["retryable"] is True
            with pytest.raises(JobConflictError):
                await runtime.delete("job-a")
            assert runtime.store.get_job_row("job-a") is not None
        finally:
            gate.set()
            await capture
            cadlink_api._CAD_DOCUMENT_CAPTURES.pop(key)
            await runtime.shutdown()

    asyncio.run(scenario())
