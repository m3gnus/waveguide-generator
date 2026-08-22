"""The run archive: folder naming and the captured CAD document."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from server.workspace.archive import (
    archive_cad_document,
    archive_folder_slug,
    captured_cad_document,
    place_run_cad_document,
)


# Asserted identically by ``frontend/src/stores/designNameSlug.parity.test.ts``.
# The browser names the folder it writes runs into; the server names the same
# folder when it files a captured CAD document there. Change both or neither.
SLUG_PARITY_TABLE = [
    ("Big Horn", "Big_Horn"),
    ("Björn's Horn", "Bjorn_s_Horn"),
    ("  ..weird__name..  ", "weird_name"),
    ("ÅÄÖ 12", "AAO_12"),
    ("R-OSSE 40x30", "R-OSSE_40x30"),
]


def test_archive_folder_slug_matches_the_browser_rule() -> None:
    for name, expected in SLUG_PARITY_TABLE:
        assert archive_folder_slug(name, "design") == expected


def test_archive_folder_slug_falls_back_rather_than_naming_nothing() -> None:
    assert archive_folder_slug("", "design") == "design"
    assert archive_folder_slug("///", "design") == "design"


def bundle_with_document(tmp_path: Path, *, content: bytes = b"f3d-bytes") -> Path:
    bundle = tmp_path / "wgreturn" / "Big Horn.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "document.f3d").write_bytes(content)
    return bundle


def record_for(bundle_name: str = "document.f3d") -> dict[str, object]:
    return {
        "ingest_id": "wgi_01",
        "return_id": "wgr_01",
        "created_at": "2026-08-19T10:00:00Z",
        "document": {
            "name": "Big Horn v7",
            "native_id": "urn:doc",
            "return_state_hash": "sha256:abc123",
            "file": bundle_name,
        },
    }


def test_a_captured_document_is_filed_under_the_design_by_return_state(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"

    relative = archive_cad_document(bundle, record_for(), runs, "Big Horn")

    assert relative == "cad/sha256_abc123.f3d"
    archived = runs / "Big_Horn" / relative
    assert archived.read_bytes() == b"f3d-bytes"
    assert json.loads(archived.with_suffix(".json").read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "documentName": "Big Horn v7",
        "nativeId": "urn:doc",
        "returnStateHash": "sha256:abc123",
        "ingestId": "wgi_01",
        "returnId": "wgr_01",
        "capturedAt": "2026-08-19T10:00:00Z",
    }


def test_re_ingesting_the_same_return_does_not_write_a_second_copy(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    first = archive_cad_document(bundle, record_for(), runs, "Big Horn")
    archived = runs / "Big_Horn" / str(first)
    before = archived.stat().st_mtime_ns

    # A second return of an unchanged document: one file per return state, not
    # one per ingestion, so this must not rewrite what is already there.
    again = archive_cad_document(bundle, record_for(), runs, "Big Horn")

    assert again == first
    assert archived.stat().st_mtime_ns == before
    assert sorted(path.name for path in (runs / "Big_Horn" / "cad").iterdir()) == [
        "sha256_abc123.f3d",
        "sha256_abc123.json",
    ]


def test_a_return_without_a_captured_document_archives_nothing(tmp_path: Path) -> None:
    bundle = tmp_path / "wgreturn" / "Big Horn.wgreturn"
    bundle.mkdir(parents=True)
    runs = tmp_path / "runs"
    record = record_for()
    record["document"] = {"name": "Big Horn", "return_state_hash": "sha256:abc", "file": None}

    assert archive_cad_document(bundle, record, runs, "Big Horn") is None
    assert not runs.exists()


def test_a_missing_document_file_is_not_an_error(tmp_path: Path) -> None:
    bundle = tmp_path / "wgreturn" / "Big Horn.wgreturn"
    bundle.mkdir(parents=True)
    runs = tmp_path / "runs"

    assert archive_cad_document(bundle, record_for(), runs, "Big Horn") is None
    assert not runs.exists()


def test_the_stored_bundle_copy_leaves_the_captured_document_behind(tmp_path: Path) -> None:
    """The content-addressed copy is deliberately not a byte copy.

    A Fusion archive is tens of megabytes and is not geometry WG solves from, so
    copying it into the application data directory as well would store every
    captured document twice. The consequence is that the stored copy is no
    longer a readable bundle -- ``read_wgreturn`` refuses a declared member that
    is missing -- and nothing reads it back today. Anything that starts to must
    read the user's own bundle instead.
    """

    from server.cadlink.ingest import _stage_bundle_cas, cad_document_member
    from server.cadlink.wgreturn import read_wgreturn
    from server.tests.test_cadlink_wgreturn import _manifest, write_bundle

    step, document = b"STEP", b"fusion-archive-bytes"
    manifest = _manifest(step)
    manifest["files"]["document.f3d"] = {
        "sha256": "sha256:" + hashlib.sha256(document).hexdigest(),
        "size_bytes": len(document),
        "media_type": "application/vnd.autodesk.fusion360",
        "purpose": "cad-document",
    }
    bundle_path = write_bundle(tmp_path / "workspace", manifest, step=step)
    (bundle_path / "document.f3d").write_bytes(document)
    bundle = read_wgreturn(bundle_path)

    assert cad_document_member(bundle.manifest) == "document.f3d"
    _destination, staged, _temporary = _stage_bundle_cas(bundle, tmp_path / "imports")

    assert staged is not None
    assert (staged / "assembly.step").read_bytes() == step
    assert not (staged / "document.f3d").exists()
    # The user's own bundle is untouched: it is the copy that keeps the document.
    assert (bundle_path / "document.f3d").read_bytes() == document


def test_the_run_copy_lands_beside_the_run_that_produced_it(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    archive_cad_document(bundle, record_for(), runs, "Big Horn")

    relative = place_run_cad_document(
        runs, "Big Horn", "14_Big_Horn", "14_Big_Horn", "sha256:abc123"
    )

    assert relative == "14_Big_Horn/14_Big_Horn.f3d"
    placed = runs / "Big_Horn" / relative
    assert placed.read_bytes() == b"f3d-bytes"
    # The sidecar travels with it: a bare .f3d in a run folder says nothing
    # about which model state it is.
    assert json.loads(placed.with_suffix(".cad.json").read_text(encoding="utf-8"))[
        "returnStateHash"
    ] == "sha256:abc123"
    # The project-level original stays: a return that is never solved still has
    # to keep its document somewhere.
    assert (runs / "Big_Horn" / "cad" / "sha256_abc123.f3d").is_file()


def test_the_run_sidecar_does_not_replace_the_solver_result_json(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    archive_cad_document(bundle, record_for(), runs, "Big Horn")
    run_directory = runs / "Big_Horn" / "14_Big_Horn"
    run_directory.mkdir()
    result = run_directory / "14_Big_Horn.json"
    result_bytes = b'{"channels":{"left":{"spl":[91.5]}}}\n'
    result.write_bytes(result_bytes)

    relative = place_run_cad_document(
        runs, "Big Horn", "14_Big_Horn", "14_Big_Horn", "sha256:abc123"
    )

    assert relative == "14_Big_Horn/14_Big_Horn.f3d"
    assert result.read_bytes() == result_bytes
    assert json.loads(
        (run_directory / "14_Big_Horn.cad.json").read_text(encoding="utf-8")
    )["returnStateHash"] == "sha256:abc123"


def test_an_existing_different_run_sidecar_is_never_clobbered(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    archive_cad_document(bundle, record_for(), runs, "Big Horn")
    run_directory = runs / "Big_Horn" / "14_Big_Horn"
    run_directory.mkdir()
    sidecar = run_directory / "14_Big_Horn.cad.json"
    sidecar.write_bytes(b'{"user":"metadata"}\n')

    assert place_run_cad_document(
        runs, "Big Horn", "14_Big_Horn", "14_Big_Horn", "sha256:abc123"
    ) is None
    assert sidecar.read_bytes() == b'{"user":"metadata"}\n'
    assert not (run_directory / "14_Big_Horn.f3d").exists()


def test_placing_the_run_copy_again_is_a_no_op(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    archive_cad_document(bundle, record_for(), runs, "Big Horn")
    first = place_run_cad_document(
        runs, "Big Horn", "14_Big_Horn", "14_Big_Horn", "sha256:abc123"
    )
    placed = runs / "Big_Horn" / str(first)
    before = placed.stat().st_mtime_ns

    again = place_run_cad_document(
        runs, "Big Horn", "14_Big_Horn", "14_Big_Horn", "sha256:abc123"
    )

    assert again == first
    assert placed.stat().st_mtime_ns == before


def test_a_replaced_run_document_is_never_clobbered(tmp_path: Path) -> None:
    """Re-archiving after a restart must not overwrite the user's own file."""

    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    archive_cad_document(bundle, record_for(), runs, "Big Horn")
    place_run_cad_document(runs, "Big Horn", "14_Big_Horn", "14_Big_Horn", "sha256:abc123")
    placed = runs / "Big_Horn" / "14_Big_Horn" / "14_Big_Horn.f3d"
    placed.write_bytes(b"the-users-own-edited-model")

    assert place_run_cad_document(
        runs, "Big Horn", "14_Big_Horn", "14_Big_Horn", "sha256:abc123"
    ) is None
    assert placed.read_bytes() == b"the-users-own-edited-model"


def test_placing_a_run_copy_of_an_uncaptured_model_does_nothing(tmp_path: Path) -> None:
    runs = tmp_path / "runs"

    assert place_run_cad_document(
        runs, "Big Horn", "14_Big_Horn", "14_Big_Horn", "sha256:never-captured"
    ) is None
    assert place_run_cad_document(runs, "Big Horn", "14_Big_Horn", "14_Big_Horn", "") is None
    assert not runs.exists()


def test_a_run_folder_that_escapes_the_project_is_refused(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    archive_cad_document(bundle, record_for(), runs, "Big Horn")

    for escape in ("..", "../elsewhere", "", "."):
        assert place_run_cad_document(
            runs, "Big Horn", escape, "14_Big_Horn", "sha256:abc123"
        ) is None
    assert not (tmp_path / "runs" / "elsewhere").exists()


def test_the_captured_document_is_found_by_its_return_state(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    archive_cad_document(bundle, record_for(), runs, "Big Horn")

    found = captured_cad_document(runs, "Big Horn", "sha256:abc123")

    assert found is not None and found.name == "sha256_abc123.f3d"
    assert captured_cad_document(runs, "Big Horn", "sha256:other") is None
    # The sidecar is metadata about the document, never the document itself.
    assert captured_cad_document(runs, "Big Horn", "sha256:abc123").suffix == ".f3d"
