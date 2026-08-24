"""The CAD Link project surface: one folder per project, its captured models.

These routes are what turns a pile of runs into "this project, in order, and
the Fusion model each stretch of it came from". The contract they have to keep
is that a project's runs and its captured CAD documents always name the same
folder -- the two used to be derived independently and could disagree.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.app import create_app
from server.cadlink.store import CadLinkStore
from server.cadlink.api import (
    ArchiveRunDocumentRequest,
    archive_run_document,
    download_project_document,
    list_project_documents,
)
from server.workspace.archive import archive_cad_document


def project(app, *, filename: str = "Tritonia.cfg") -> tuple[str, str]:
    """Register one design and return its (design_id, lineage_id)."""

    saved = app.state.cadlink_store.save(
        requested=None,
        design_hash="sha256:" + hashlib.sha256(filename.encode()).hexdigest(),
        filename=filename,
        snapshot_builder=lambda identity: f"CadLink = {{\nDesignId = {identity.design_id}\n}}\n",
        saved_at="2026-08-20T12:00:00Z",
    )
    return saved["identity"].design_id, saved["identity"].lineage_id


def capture(runs: Path, tmp_path: Path, stem: str, digest: str, *, at: str) -> None:
    bundle = tmp_path / "wgreturn" / f"{digest}.wgreturn"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "document.f3d").write_bytes(f"model-{digest}".encode())
    archive_cad_document(
        bundle,
        {
            "ingest_id": f"wgi_{digest}",
            "return_id": f"wgr_{digest}",
            "created_at": at,
            "document": {
                "name": "Tritonia",
                "native_id": None,
                "return_state_hash": f"sha256:{digest}",
                "file": "document.f3d",
            },
        },
        runs,
        stem,
    )


def app_with_runs(tmp_path: Path):
    app = create_app(data_dir=tmp_path / "data")
    runs = tmp_path / "runs"
    runs.mkdir()
    app.state.workspace.select(runs)
    return app, runs


def test_a_project_lists_only_its_newest_captured_model(tmp_path: Path) -> None:
    """The project-level cad/ folder keeps only the newest model state.

    Capturing a second return state prunes the first, so the listing (which
    reads that folder directly) reports one item, not one per return.
    """

    app, runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app)
    capture(runs, tmp_path, "Tritonia", "aaa", at="2026-08-20T09:00:00Z")
    capture(runs, tmp_path, "Tritonia", "bbb", at="2026-08-21T09:00:00Z")

    listing = asyncio.run(list_project_documents(lineage_id, SimpleNamespace(app=app)))

    assert listing["archiveStem"] == "Tritonia"
    assert [item["returnStateHash"] for item in listing["items"]] == ["sha256:bbb"]
    assert listing["items"][0]["filename"] == "Tritonia_260821-0900_bbb.f3d"
    assert listing["items"][0]["bytes"] == len(b"model-bbb")


def test_a_model_whose_file_was_removed_is_still_listed_as_unavailable(
    tmp_path: Path,
) -> None:
    """The record must not vanish with the file.

    A run in the history still came from that model. Dropping the row would
    silently reattribute those runs to whichever model is listed next.
    """

    app, runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app)
    capture(runs, tmp_path, "Tritonia", "aaa", at="2026-08-20T09:00:00Z")
    (runs / "Tritonia" / "cad" / "Tritonia_260820-0900_aaa.f3d").unlink()

    listing = asyncio.run(list_project_documents(lineage_id, SimpleNamespace(app=app)))

    assert listing["items"] == [
        {
            "returnStateHash": "sha256:aaa",
            "documentName": "Tritonia",
            "ingestId": "wgi_aaa",
            "returnId": "wgr_aaa",
            "capturedAt": "2026-08-20T09:00:00Z",
            "filename": None,
            "bytes": None,
        }
    ]


def test_downloading_a_model_hands_back_the_archived_file(tmp_path: Path) -> None:
    app, runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app)
    capture(runs, tmp_path, "Tritonia", "aaa", at="2026-08-20T09:00:00Z")

    response = asyncio.run(
        download_project_document(lineage_id, "sha256:aaa", SimpleNamespace(app=app))
    )

    assert Path(response.path).read_bytes() == b"model-aaa"
    assert response.filename == "Tritonia_260820-0900_aaa.f3d"


def test_downloading_a_model_that_was_never_captured_says_why(tmp_path: Path) -> None:
    app, _runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app)

    with pytest.raises(HTTPException) as refused:
        asyncio.run(
            download_project_document(lineage_id, "sha256:never", SimpleNamespace(app=app))
        )

    assert refused.value.status_code == 404
    assert "captured before the setting" in str(refused.value.detail)


def test_a_malformed_return_state_hash_is_refused_before_touching_disk(
    tmp_path: Path,
) -> None:
    app, _runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app)

    for malformed in ("../../etc/passwd", "sha256:aaa/../../x", "a" * 200):
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                download_project_document(lineage_id, malformed, SimpleNamespace(app=app))
            )
        assert refused.value.status_code == 422


def test_an_unknown_project_is_not_found(tmp_path: Path) -> None:
    app, _runs = app_with_runs(tmp_path)

    with pytest.raises(HTTPException) as refused:
        asyncio.run(list_project_documents("wgl_nope", SimpleNamespace(app=app)))

    assert refused.value.status_code == 404


def test_an_older_project_is_found_beyond_the_recent_design_head_cap(
    tmp_path: Path,
) -> None:
    app, _runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app, filename="Older Project.cfg")
    store = app.state.cadlink_store
    start = datetime(2026, 8, 21, tzinfo=timezone.utc)
    for index in range(500):
        saved_at = (start + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")
        store.save(
            requested=None,
            design_hash="sha256:" + hashlib.sha256(f"newer-{index}".encode()).hexdigest(),
            filename=f"newer-{index}.cfg",
            snapshot_builder=lambda identity: f"DesignId={identity.design_id}",
            saved_at=saved_at,
        )
    assert lineage_id not in {
        str(row["lineage_id"]) for row in store.list_designs(limit=500)
    }

    listing = asyncio.run(list_project_documents(lineage_id, SimpleNamespace(app=app)))

    assert listing["archiveStem"] == "Older Project"
    assert listing["items"] == []


def test_the_run_copy_is_filed_only_in_run_mode(tmp_path: Path) -> None:
    app, runs = app_with_runs(tmp_path)
    project(app)
    capture(runs, tmp_path, "Tritonia", "aaa", at="2026-08-20T09:00:00Z")
    request = ArchiveRunDocumentRequest(
        subdirectory="Tritonia/14_Tritonia",
        runStem="14_Tritonia",
        archiveStem="Tritonia",
        returnStateHash="sha256:aaa",
    )

    app.state.cad_workspace.set_capture_mode("project")
    declined = asyncio.run(archive_run_document(request, SimpleNamespace(app=app)))
    assert declined == {"placed": False, "reason": "Capture mode is project."}
    assert not (runs / "Tritonia" / "14_Tritonia").exists()

    app.state.cad_workspace.set_capture_mode("run")
    placed = asyncio.run(archive_run_document(request, SimpleNamespace(app=app)))

    assert placed == {"placed": True, "relativePath": "14_Tritonia/14_Tritonia.f3d"}
    assert (runs / "Tritonia" / "14_Tritonia" / "14_Tritonia.f3d").read_bytes() == b"model-aaa"


def test_a_run_subdirectory_that_escapes_the_archive_is_refused(tmp_path: Path) -> None:
    app, runs = app_with_runs(tmp_path)
    project(app)
    capture(runs, tmp_path, "Tritonia", "aaa", at="2026-08-20T09:00:00Z")
    app.state.cad_workspace.set_capture_mode("run")

    with pytest.raises(ValueError):
        asyncio.run(
            archive_run_document(
                ArchiveRunDocumentRequest(
                    subdirectory="Tritonia/../../escape",
                    runStem="14_Tritonia",
                    archiveStem="Tritonia",
                    returnStateHash="sha256:aaa",
                ),
                SimpleNamespace(app=app),
            )
        )
    assert not (tmp_path / "escape").exists()


def test_the_capture_and_the_run_agree_on_one_folder_after_a_rename(
    tmp_path: Path,
) -> None:
    """The whole point of freezing the archive stem.

    Filing used to derive the folder from the CAD document name while the run
    archive derived it from the job's own label, so renaming a run stranded its
    Fusion model in a folder the run never appeared in.
    """

    app, runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app)
    store = app.state.cadlink_store

    claimed = store.claim_archive_stem(lineage_id, preferred="Tritonia")
    assert claimed == "Tritonia"
    # A later return from a document that has since been renamed must not open
    # a second folder: the first claim is the project's folder for good.
    assert store.claim_archive_stem(lineage_id, preferred="Tritonia rev B") == "Tritonia"

    capture(runs, tmp_path, claimed, "aaa", at="2026-08-20T09:00:00Z")
    listing = asyncio.run(list_project_documents(lineage_id, SimpleNamespace(app=app)))

    assert listing["archiveStem"] == "Tritonia"
    assert [item["returnStateHash"] for item in listing["items"]] == ["sha256:aaa"]


def test_a_lineage_with_no_recorded_name_falls_back_to_the_design_file(
    tmp_path: Path,
) -> None:
    app, _runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app, filename="Big Horn.cfg")

    listing = asyncio.run(list_project_documents(lineage_id, SimpleNamespace(app=app)))

    assert listing["archiveStem"] == "Big Horn"
    assert listing["items"] == []
    # Listing must not claim a name: only an actual filing decides the folder.
    assert (app.state.cadlink_store.get_lineage_cad_names(lineage_id) or {}).get(
        "archive_stem"
    ) in (None, "")


def test_the_project_folder_is_revealed_only_once_it_exists(tmp_path: Path) -> None:
    from server.cadlink.api import reveal_project_folder

    app, _runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app)

    with pytest.raises(HTTPException) as refused:
        asyncio.run(reveal_project_folder(lineage_id, SimpleNamespace(app=app)))

    assert refused.value.status_code == 404
    assert "Solve a run" in str(refused.value.detail)


def test_a_sidecar_that_cannot_be_read_is_skipped_not_fatal(tmp_path: Path) -> None:
    app, runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app)
    capture(runs, tmp_path, "Tritonia", "aaa", at="2026-08-20T09:00:00Z")
    (runs / "Tritonia" / "cad" / "broken.json").write_text("{not json", encoding="utf-8")

    listing = asyncio.run(list_project_documents(lineage_id, SimpleNamespace(app=app)))

    assert [item["returnStateHash"] for item in listing["items"]] == ["sha256:aaa"]


def test_listing_reports_the_folder_it_read(tmp_path: Path) -> None:
    app, runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app)

    listing = asyncio.run(list_project_documents(lineage_id, SimpleNamespace(app=app)))

    assert json.loads(json.dumps(listing["folder"])) == str(
        (runs / "Tritonia").resolve()
    )


def test_a_document_authored_in_cad_becomes_a_project_of_its_own(
    tmp_path: Path,
) -> None:
    """Geometry authored in CAD used to belong to no project at all.

    Its runs carried no lineage, so they dropped out of the very history they
    were listed above, and the panel named itself after whatever document
    Fusion had open instead of after the project it was actually on.
    """

    app, _runs = app_with_runs(tmp_path)
    store = app.state.cadlink_store
    urn = "urn:adsk.wipprod:dm.lineage:Jur0DxWKR12JPVdr3BLhEQ"

    lineage_id = store.claim_cad_document_lineage(urn, "260627 - PartyMEH v10")
    assert lineage_id
    # The urn is the identity, so a second return from the same document is the
    # same project rather than a second one.
    assert store.claim_cad_document_lineage(urn, "260627 - PartyMEH v11") == lineage_id
    # The stem is the server-normalised ASCII folder key, not the document name:
    # two documents whose names differ only in punctuation would otherwise share
    # one folder. The readable name lives in document_name.
    assert store.claim_archive_stem(lineage_id, preferred="260627 - PartyMEH v10") == (
        "260627_-_PartyMEH_v10"
    )

    # A rename moves the label but never the folder: the captured .f3d already
    # lives there.
    assert store.claim_cad_document_lineage(urn, "PartyMEH final") == lineage_id
    names = store.get_lineage_cad_names(lineage_id) or {}
    assert names["document_name"] == "PartyMEH final"
    assert names["archive_stem"] == "260627_-_PartyMEH_v10"


def test_a_cad_only_project_is_listed_and_its_folder_resolves(tmp_path: Path) -> None:
    from server.cadlink.api import list_designs

    app, runs = app_with_runs(tmp_path)
    store = app.state.cadlink_store
    lineage_id = store.claim_cad_document_lineage(
        "urn:adsk.wipprod:dm.lineage:cad-only", "PartyMEH"
    )
    store.claim_archive_stem(lineage_id, preferred="PartyMEH")

    listing = asyncio.run(list_designs(SimpleNamespace(app=app)))
    entry = next(
        item for item in listing["items"] if item["lineageId"] == lineage_id
    )
    assert entry["designId"] is None
    assert entry["documentName"] == "PartyMEH"
    assert entry["archiveStem"] == "PartyMEH"

    # It has no design row, so the folder used to 404 rather than resolve.
    capture(runs, tmp_path, "PartyMEH", "ccc", at="2026-08-23T09:00:00Z")
    documents = asyncio.run(list_project_documents(lineage_id, SimpleNamespace(app=app)))
    assert documents["archiveStem"] == "PartyMEH"
    assert [item["returnStateHash"] for item in documents["items"]] == ["sha256:ccc"]


def test_a_design_project_reports_what_cad_calls_it(tmp_path: Path) -> None:
    from server.cadlink.api import list_designs

    app, _runs = app_with_runs(tmp_path)
    store = app.state.cadlink_store
    _design_id, lineage_id = project(app)
    store.record_cad_document(lineage_id, "urn:adsk.wipprod:dm.lineage:wg", "Tritonia M")

    listing = asyncio.run(list_designs(SimpleNamespace(app=app)))
    entry = next(item for item in listing["items"] if item["lineageId"] == lineage_id)
    assert entry["documentName"] == "Tritonia M"

    # First writer wins on the document: a second lineage cannot steal it.
    _other_design_id, other_lineage = project(app, filename="Other.cfg")
    store.record_cad_document(
        other_lineage, "urn:adsk.wipprod:dm.lineage:wg", "Tritonia M"
    )
    assert (store.get_lineage_cad_names(other_lineage) or {}).get(
        "document_native_id"
    ) in (None, "")


def test_ingestion_gives_a_cad_authored_return_a_project(tmp_path: Path) -> None:
    from server.cadlink.ingest import _resolve_project

    store = CadLinkStore(tmp_path / "cadlink.db")
    urn = "urn:adsk.wipprod:dm.lineage:party"

    resolved = _resolve_project(store, None, urn, "260627 - PartyMEH v10")

    assert resolved is not None
    assert resolved["design_id"] is None
    assert resolved["document_name"] == "260627 - PartyMEH v10"
    assert resolved["archive_stem"] == "260627_-_PartyMEH_v10"
    # The same document is the same project on every later return.
    again = _resolve_project(store, None, urn, "260627 - PartyMEH v10")
    assert again is not None
    assert again["lineage_id"] == resolved["lineage_id"]


def test_a_return_with_no_document_identity_claims_nothing(tmp_path: Path) -> None:
    from server.cadlink.ingest import _resolve_project

    store = CadLinkStore(tmp_path / "cadlink.db")

    assert _resolve_project(store, None, None, "Untitled") is None
    assert store.list_cad_document_projects() == []


def test_a_cad_authored_run_joins_the_project_its_document_owns(
    tmp_path: Path,
) -> None:
    from server.jobs.runtime import _cad_authored_project

    store = CadLinkStore(tmp_path / "cadlink.db")
    urn = "urn:adsk.wipprod:dm.lineage:party"
    lineage_id = store.claim_cad_document_lineage(urn, "PartyMEH")
    store.claim_archive_stem(lineage_id, preferred="PartyMEH")

    record = {
        "project": {"lineage_id": lineage_id, "archive_stem": "PartyMEH"},
        "document": {"native_id": urn, "name": "PartyMEH"},
    }
    assert asyncio.run(_cad_authored_project(store, record)) == (lineage_id, "PartyMEH")

    # A return ingested before projects existed carries no project block, but
    # the claim is keyed on the document, so the document still finds it.
    legacy = {"document": {"native_id": urn, "name": "PartyMEH"}}
    assert asyncio.run(_cad_authored_project(store, legacy)) == (lineage_id, "PartyMEH")

    # A document nothing has claimed still belongs to no project.
    unknown = {"document": {"native_id": "urn:adsk:unknown", "name": "Other"}}
    assert asyncio.run(_cad_authored_project(store, unknown)) == (None, None)


def legacy_capture(runs: Path, stem: str, digest: str, *, content: bytes) -> None:
    """Write a document exactly as the pre-friendly-name archiver did.

    Real legacy files predate this test suite's ``capture`` helper, so this
    reproduces the old ``sha256_<digest>.f3d`` naming directly rather than
    going through ``archive_cad_document``, which now only ever writes the
    new friendly name.
    """

    directory = runs / stem / "cad"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"sha256_{digest}.f3d").write_bytes(content)
    (directory / f"sha256_{digest}.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "documentName": "Tritonia",
                "nativeId": None,
                "returnStateHash": f"sha256:{digest}",
                "ingestId": f"wgi_{digest}",
                "returnId": f"wgr_{digest}",
                "capturedAt": "2026-08-19T09:00:00Z",
            }
        ),
        encoding="utf-8",
    )


def test_downloading_a_legacy_named_model_serves_its_own_filename(tmp_path: Path) -> None:
    """A document captured before the friendly-name change stays readable.

    ``sha256_<digest>.f3d`` is not renamed on disk, and the download route
    must still hand back that exact name -- not a name recomputed to look
    like the new convention.
    """

    app, runs = app_with_runs(tmp_path)
    _design_id, lineage_id = project(app)
    legacy_capture(runs, "Tritonia", "aaa", content=b"legacy-model-aaa")

    response = asyncio.run(
        download_project_document(lineage_id, "sha256:aaa", SimpleNamespace(app=app))
    )

    assert Path(response.path).read_bytes() == b"legacy-model-aaa"
    assert response.filename == "sha256_aaa.f3d"


def test_a_second_capture_of_a_legacy_document_is_a_no_op(tmp_path: Path) -> None:
    """Re-archiving a return already filed under the legacy name writes nothing new."""

    app, runs = app_with_runs(tmp_path)
    project(app)
    legacy_capture(runs, "Tritonia", "aaa", content=b"legacy-model-aaa")

    capture(runs, tmp_path, "Tritonia", "aaa", at="2026-08-20T09:00:00Z")

    assert sorted(path.name for path in (runs / "Tritonia" / "cad").iterdir()) == [
        "sha256_aaa.f3d",
        "sha256_aaa.json",
    ]
