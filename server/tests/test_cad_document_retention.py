"""A newer CAD return must not erase the model a pending run still needs.

The project-level ``cad/`` folder deliberately shows one current model state,
and it is also the only place a run's own copy is made from. Those two facts
used to be in direct conflict: ingesting a changed model pruned the previous
one, so a run queued from the previous model was archived without any model at
all -- and the route said so with an HTTP 200 that nothing read.

The rule these tests hold to is that a captured model state survives while any
queued, running or unarchived run still has to be archived from it, is
reclaimed once they have been, and that a run archive waits for a capture that
is still copying instead of concluding there is nothing to place.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from server.app import create_app
from server.cadlink import api as cadlink_api
from server.cadlink.api import ArchiveRunDocumentRequest, archive_run_document
from server.workspace.archive import archive_cad_document


STEM = "Tritonia"


def app_with_runs(tmp_path: Path):
    app = create_app(data_dir=tmp_path / "data")
    runs = tmp_path / "runs"
    runs.mkdir()
    app.state.workspace.select(runs)
    app.state.cad_workspace.set_capture_mode("run")
    return app, runs


def bundle_for(tmp_path: Path, digest: str) -> Path:
    bundle = tmp_path / "wgreturn" / f"{digest}.wgreturn"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "document.f3d").write_bytes(f"model-{digest}".encode())
    return bundle


def record_for(digest: str, *, at: str) -> dict[str, object]:
    return {
        "ingest_id": f"wgi_{digest}",
        "return_id": f"wgr_{digest}",
        "created_at": at,
        "document": {
            "name": STEM,
            "native_id": None,
            "return_state_hash": f"sha256:{digest}",
            "file": "document.f3d",
        },
        "project": {"archive_stem": STEM},
    }


def capture_directly(runs: Path, tmp_path: Path, digest: str, *, at: str) -> None:
    """A capture with no retention input, as the very first one always is."""

    archive_cad_document(bundle_for(tmp_path, digest), record_for(digest, at=at), runs, STEM)


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
            "config_json": {},
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
            record_for(digest, at=at),
        )
    )


def archive_request(digest: str, run: str = "14_Tritonia") -> ArchiveRunDocumentRequest:
    return ArchiveRunDocumentRequest(
        subdirectory=f"{STEM}/{run}",
        runStem=run,
        archiveStem=STEM,
        returnStateHash=f"sha256:{digest}",
    )


def test_a_queued_run_keeps_its_model_when_a_changed_one_is_ingested(
    tmp_path: Path,
) -> None:
    """Acceptance 1: queue A, capture B, then archive A.

    The run must get A's bytes and A's sidecar. Before the retention rule this
    was the whole defect: capturing B deleted A, and archiving the A run then
    filed nothing while answering HTTP 200.
    """

    app, runs = app_with_runs(tmp_path)
    capture_directly(runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
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
        archive_run_document(archive_request("aaa"), SimpleNamespace(app=app))
    )

    assert placed == {"placed": True, "relativePath": "14_Tritonia/14_Tritonia.f3d"}
    run_folder = runs / STEM / "14_Tritonia"
    assert run_folder.joinpath("14_Tritonia.f3d").read_bytes() == b"model-aaa"
    sidecar = json.loads(
        run_folder.joinpath("14_Tritonia.cad.json").read_text(encoding="utf-8")
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
                record_for("ccc", at="2026-08-22T09:00:00Z"),
            )
            # Let the capture reach the worker thread and block there.
            for _ in range(50):
                await asyncio.sleep(0)
            assert not (runs / STEM / "cad").exists()

            placing = asyncio.ensure_future(
                archive_run_document(
                    archive_request("ccc", "15_Tritonia"), SimpleNamespace(app=app)
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

    assert placed == {"placed": True, "relativePath": "15_Tritonia/15_Tritonia.f3d"}
    assert (
        runs / STEM / "15_Tritonia" / "15_Tritonia.f3d"
    ).read_bytes() == b"model-ccc"


def test_archiving_a_run_twice_never_substitutes_the_newer_model(
    tmp_path: Path,
) -> None:
    """Acceptance 3: a retried archive is a no-op, not a re-attribution.

    A retry after a restart must answer identically, and must never hand the
    run the model that superseded the one it was solved from.
    """

    app, runs = app_with_runs(tmp_path)
    capture_directly(runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(app, "job-a", "aaa", status="complete")
    capture_through_the_app(app, tmp_path, "bbb", at="2026-08-21T09:00:00Z")

    first = asyncio.run(
        archive_run_document(archive_request("aaa"), SimpleNamespace(app=app))
    )
    again = asyncio.run(
        archive_run_document(archive_request("aaa"), SimpleNamespace(app=app))
    )

    assert first == again == {
        "placed": True,
        "relativePath": "14_Tritonia/14_Tritonia.f3d",
    }
    document = runs / STEM / "14_Tritonia" / "14_Tritonia.f3d"
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
    capture_directly(runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(
        app, "job-a", "aaa", status="complete", archived_at="2026-08-20T13:00:00Z"
    )

    capture_through_the_app(app, tmp_path, "bbb", at="2026-08-21T09:00:00Z")

    assert sorted(path.name for path in (runs / STEM / "cad").iterdir()) == [
        "Tritonia_260821-0900_bbb.f3d",
        "Tritonia_260821-0900_bbb.json",
    ]


def test_placing_a_run_copy_reclaims_the_states_nothing_needs_any_more(
    tmp_path: Path,
) -> None:
    """A released state does not have to wait for another ingestion to go.

    Otherwise a project that is never swept again keeps every state a run ever
    referenced, and the one-current-model view never comes back.
    """

    app, runs = app_with_runs(tmp_path)
    capture_directly(runs, tmp_path, "aaa", at="2026-08-20T09:00:00Z")
    imported_run(app, "job-a", "aaa", status="complete")
    capture_through_the_app(app, tmp_path, "bbb", at="2026-08-21T09:00:00Z")

    # The A run is archived and marked archived, so it has released A.
    asyncio.run(archive_run_document(archive_request("aaa"), SimpleNamespace(app=app)))
    app.state.jobs_runtime.store.mutate_job_metadata(
        "job-a", {"archived_at": "2026-08-21T10:00:00Z"}
    )
    # A second run, of the current state, archives next.
    asyncio.run(
        archive_run_document(
            archive_request("bbb", "15_Tritonia"), SimpleNamespace(app=app)
        )
    )

    assert sorted(path.name for path in (runs / STEM / "cad").iterdir()) == [
        "Tritonia_260821-0900_bbb.f3d",
        "Tritonia_260821-0900_bbb.json",
    ]
    # The first run keeps the model it was actually solved from.
    assert (
        runs / STEM / "14_Tritonia" / "14_Tritonia.f3d"
    ).read_bytes() == b"model-aaa"


def test_a_missing_model_is_reported_rather_than_answered_as_a_success(
    tmp_path: Path,
) -> None:
    """A copy that was asked for and not made must say so, and say whether
    asking again could still produce it."""

    app, _runs = app_with_runs(tmp_path)

    answer = asyncio.run(
        archive_run_document(archive_request("zzz"), SimpleNamespace(app=app))
    )

    assert answer["placed"] is False
    assert answer["retryable"] is True
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
