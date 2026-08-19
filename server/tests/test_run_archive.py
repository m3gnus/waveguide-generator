"""The run archive: folder naming and the captured CAD document."""

from __future__ import annotations

import json
from pathlib import Path

from server.workspace.archive import archive_cad_document, archive_folder_slug


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
