"""The run archive: folder naming and the captured CAD document."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import pytest

from server.workspace.archive import (
    archive_cad_document,
    archive_folder_slug,
    captured_cad_document,
    pin_cad_document,
    reclaim_captured_documents,
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

    # <safe document name>_<UTC YYMMDD-HHMM>_<first 12 hex chars of the digest>
    assert relative == "cad/Big_Horn_v7_260819-1000_abc123.f3d"
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


def test_the_capture_stamp_falls_back_when_the_timestamp_is_unusable(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    record = record_for()
    record["created_at"] = "not-a-timestamp"

    relative = archive_cad_document(bundle, record, runs, "Big Horn")

    assert relative == "cad/Big_Horn_v7_000000-0000_abc123.f3d"


def test_a_document_name_full_of_hostile_characters_is_made_safe(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    record = record_for()
    record["document"]["name"] = "  ../etc/passwd: Bjsüÿørn\\Horn <v2> ??  "

    relative = archive_cad_document(bundle, record, runs, "Big Horn")

    assert relative is not None
    archived = runs / "Big_Horn" / relative
    assert archived.is_file()
    # No path separators, colons, or other filesystem-hostile characters
    # survive -- only the portable alphabet archive_folder_slug already
    # guarantees for run and design folders.
    assert re.fullmatch(r"[A-Za-z0-9._-]+", archived.name)
    assert "/" not in archived.name and "\\" not in archived.name


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
        "Big_Horn_v7_260819-1000_abc123.f3d",
        "Big_Horn_v7_260819-1000_abc123.json",
    ]


def test_re_ingesting_a_return_already_filed_under_the_legacy_name_is_a_no_op(
    tmp_path: Path,
) -> None:
    """A return that was archived before this change stays a single file.

    The dedup check has to recognise the pre-existing ``sha256_<digest>.f3d``
    just as reliably as the new friendly name, or every re-ingestion of an
    old capture would grow a second, redundant copy beside it.
    """

    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    directory = runs / "Big_Horn" / "cad"
    directory.mkdir(parents=True)
    (directory / "sha256_abc123.f3d").write_bytes(b"f3d-bytes")
    (directory / "sha256_abc123.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "documentName": "Big Horn v7",
                "nativeId": "urn:doc",
                "returnStateHash": "sha256:abc123",
                "ingestId": "wgi_00",
                "returnId": "wgr_00",
                "capturedAt": "2026-08-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    relative = archive_cad_document(bundle, record_for(), runs, "Big Horn")

    assert relative == "cad/sha256_abc123.f3d"
    assert sorted(path.name for path in directory.iterdir()) == [
        "sha256_abc123.f3d",
        "sha256_abc123.json",
    ]


def test_archiving_a_new_model_state_prunes_the_previous_one(tmp_path: Path) -> None:
    """The project-level cad/ folder keeps only the newest model state.

    Each run folder keeps its own copy forever as a pinned shared version,
    so an older run stays reopenable even after the shared project-level copy
    it was solved from is superseded and removed.
    """

    bundle = bundle_with_document(tmp_path, content=b"first-bytes")
    runs = tmp_path / "runs"
    first_record = record_for()
    first_record["created_at"] = "2026-08-19T10:00:00Z"
    first_record["document"]["return_state_hash"] = "sha256:aaa111"
    first = archive_cad_document(bundle, first_record, runs, "Big Horn")

    later_bundle = bundle_with_document(tmp_path / "later", content=b"second-bytes")
    second_record = record_for()
    second_record["created_at"] = "2026-08-20T11:30:00Z"
    second_record["document"]["return_state_hash"] = "sha256:bbb222"
    second = archive_cad_document(later_bundle, second_record, runs, "Big Horn")

    assert first == "cad/Big_Horn_v7_260819-1000_aaa111.f3d"
    assert second == "cad/Big_Horn_v7_260820-1130_bbb222.f3d"
    assert (runs / "Big_Horn" / second).read_bytes() == b"second-bytes"
    # The first state's data file and sidecar are both gone: only the newest
    # model state's pair remains.
    assert sorted(path.name for path in (runs / "Big_Horn" / "cad").iterdir()) == [
        "Big_Horn_v7_260820-1130_bbb222.f3d",
        "Big_Horn_v7_260820-1130_bbb222.json",
    ]


def test_a_retained_model_state_survives_a_newer_capture(tmp_path: Path) -> None:
    """Pruning is superseded-and-released, not merely superseded.

    A run queued from the first state has not been archived yet, and its own
    copy is made *from* this folder, so deleting the state here would leave
    that run with no model to be archived from at all.
    """

    bundle = bundle_with_document(tmp_path, content=b"first-bytes")
    runs = tmp_path / "runs"
    first_record = record_for()
    first_record["document"]["return_state_hash"] = "sha256:aaa111"
    archive_cad_document(bundle, first_record, runs, "Big Horn")

    later_bundle = bundle_with_document(tmp_path / "later", content=b"second-bytes")
    second_record = record_for()
    second_record["created_at"] = "2026-08-20T11:30:00Z"
    second_record["document"]["return_state_hash"] = "sha256:bbb222"
    archive_cad_document(
        later_bundle, second_record, runs, "Big Horn", ["sha256:aaa111"]
    )

    assert sorted(path.name for path in (runs / "Big_Horn" / "cad").iterdir()) == [
        "Big_Horn_v7_260819-1000_aaa111.f3d",
        "Big_Horn_v7_260819-1000_aaa111.json",
        "Big_Horn_v7_260820-1130_bbb222.f3d",
        "Big_Horn_v7_260820-1130_bbb222.json",
    ]
    # The retained state is still the one its own runs will be given.
    placed = pin_cad_document(runs, "Big Horn", "sha256:aaa111", verify_document=lambda path: path.read_bytes() == b"first-bytes")
    assert placed == "cad/Big_Horn_v7_260819-1000_aaa111.pinned.f3d"
    assert (runs / "Big_Horn" / placed).read_bytes() == b"first-bytes"


def test_a_retained_state_is_matched_by_digest_not_by_spelling(tmp_path: Path) -> None:
    """``sha256:<hex>`` and a bare ``<hex>`` name one model state, not two."""

    bundle = bundle_with_document(tmp_path, content=b"first-bytes")
    runs = tmp_path / "runs"
    first_record = record_for()
    first_record["document"]["return_state_hash"] = "sha256:AAA111"
    archive_cad_document(bundle, first_record, runs, "Big Horn")

    later_bundle = bundle_with_document(tmp_path / "later", content=b"second-bytes")
    second_record = record_for()
    second_record["created_at"] = "2026-08-20T11:30:00Z"
    second_record["document"]["return_state_hash"] = "sha256:bbb222"
    archive_cad_document(later_bundle, second_record, runs, "Big Horn", ["aaa111"])

    assert (runs / "Big_Horn" / "cad" / "Big_Horn_v7_260819-1000_aaa111.f3d").is_file()


def test_reclaiming_drops_the_states_no_run_still_needs(tmp_path: Path) -> None:
    """Retention ends at release: the project view returns to one model."""

    bundle = bundle_with_document(tmp_path, content=b"first-bytes")
    runs = tmp_path / "runs"
    first_record = record_for()
    first_record["document"]["return_state_hash"] = "sha256:aaa111"
    archive_cad_document(bundle, first_record, runs, "Big Horn")
    later_bundle = bundle_with_document(tmp_path / "later", content=b"second-bytes")
    second_record = record_for()
    second_record["created_at"] = "2026-08-20T11:30:00Z"
    second_record["document"]["return_state_hash"] = "sha256:bbb222"
    archive_cad_document(
        later_bundle, second_record, runs, "Big Horn", ["sha256:aaa111"]
    )

    # Still held: nothing goes.
    reclaim_captured_documents(runs, "Big Horn", ["sha256:aaa111"])
    assert len(list((runs / "Big_Horn" / "cad").iterdir())) == 4

    reclaim_captured_documents(runs, "Big Horn", [])

    assert sorted(path.name for path in (runs / "Big_Horn" / "cad").iterdir()) == [
        "Big_Horn_v7_260820-1130_bbb222.f3d",
        "Big_Horn_v7_260820-1130_bbb222.json",
    ]


def test_reclaiming_leaves_a_folder_it_cannot_order_alone(tmp_path: Path) -> None:
    """No capture time on every entry means no defensible current model.

    Guessing one risks deleting the current one, so this hands the decision to
    the next ingestion, which knows exactly what it just wrote.
    """

    runs = tmp_path / "runs"
    directory = runs / "Big_Horn" / "cad"
    directory.mkdir(parents=True)
    (directory / "sha256_aaa111.f3d").write_bytes(b"legacy-bytes")
    (directory / "sha256_aaa111.json").write_text(
        json.dumps({"schemaVersion": 1, "returnStateHash": "sha256:aaa111"}),
        encoding="utf-8",
    )
    bundle = bundle_with_document(tmp_path, content=b"second-bytes")
    record = record_for()
    record["document"]["return_state_hash"] = "sha256:bbb222"
    archive_cad_document(bundle, record, runs, "Big Horn", ["sha256:aaa111"])

    reclaim_captured_documents(runs, "Big Horn", [])

    assert (directory / "sha256_aaa111.f3d").is_file()


def test_archiving_a_new_model_state_prunes_a_legacy_named_one(tmp_path: Path) -> None:
    """A legacy ``sha256_<digest>`` capture is pruned exactly like a new one."""

    runs = tmp_path / "runs"
    directory = runs / "Big_Horn" / "cad"
    directory.mkdir(parents=True)
    (directory / "sha256_aaa111.f3d").write_bytes(b"legacy-bytes")
    (directory / "sha256_aaa111.json").write_text(
        json.dumps({"schemaVersion": 1, "returnStateHash": "sha256:aaa111"}),
        encoding="utf-8",
    )

    bundle = bundle_with_document(tmp_path, content=b"second-bytes")
    record = record_for()
    record["document"]["return_state_hash"] = "sha256:bbb222"

    relative = archive_cad_document(bundle, record, runs, "Big Horn")

    assert relative == "cad/Big_Horn_v7_260819-1000_bbb222.f3d"
    assert sorted(path.name for path in directory.iterdir()) == [
        "Big_Horn_v7_260819-1000_bbb222.f3d",
        "Big_Horn_v7_260819-1000_bbb222.json",
    ]


def test_re_archiving_the_same_state_prunes_nothing(tmp_path: Path) -> None:
    """The dedup no-op path must never prune: nothing new arrived to keep."""

    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    directory = runs / "Big_Horn" / "cad"
    archive_cad_document(bundle, record_for(), runs, "Big Horn")
    before = {
        path.name: path.stat().st_mtime_ns for path in directory.iterdir()
    }

    again = archive_cad_document(bundle, record_for(), runs, "Big Horn")

    assert again == "cad/Big_Horn_v7_260819-1000_abc123.f3d"
    after = {path.name: path.stat().st_mtime_ns for path in directory.iterdir()}
    assert after == before


def test_pruning_leaves_unrelated_files_in_the_folder_alone(tmp_path: Path) -> None:
    """Files this function has no naming convention for are never touched."""

    bundle = bundle_with_document(tmp_path, content=b"first-bytes")
    runs = tmp_path / "runs"
    first_record = record_for()
    first_record["document"]["return_state_hash"] = "sha256:aaa111"
    archive_cad_document(bundle, first_record, runs, "Big Horn")

    directory = runs / "Big_Horn" / "cad"
    (directory / "notes.txt").write_text("keep me", encoding="utf-8")
    (directory / "orphan.json").write_text("{not json", encoding="utf-8")
    # A stray data file with no sidecar at all -- its digest cannot be
    # determined, so pruning must leave it exactly where it is.
    (directory / "mystery.f3d").write_bytes(b"unidentified-model")

    later_bundle = bundle_with_document(tmp_path / "later", content=b"second-bytes")
    second_record = record_for()
    second_record["created_at"] = "2026-08-20T11:30:00Z"
    second_record["document"]["return_state_hash"] = "sha256:bbb222"
    archive_cad_document(later_bundle, second_record, runs, "Big Horn")

    remaining = sorted(path.name for path in directory.iterdir())
    assert remaining == [
        "Big_Horn_v7_260820-1130_bbb222.f3d",
        "Big_Horn_v7_260820-1130_bbb222.json",
        "mystery.f3d",
        "notes.txt",
        "orphan.json",
    ]
    assert (directory / "notes.txt").read_text(encoding="utf-8") == "keep me"
    assert (directory / "mystery.f3d").read_bytes() == b"unidentified-model"


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

    from server.cadlink.intake.ingest import _stage_bundle_cas, cad_document_member
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
    _destination, staged, _temporary, _replacing = _stage_bundle_cas(
        bundle, tmp_path / "imports"
    )

    assert staged is not None
    assert (staged / "assembly.step").read_bytes() == step
    assert not (staged / "document.f3d").exists()
    # The user's own bundle is untouched: it is the copy that keeps the document.
    assert (bundle_path / "document.f3d").read_bytes() == document


def test_two_runs_share_one_pinned_file_and_reingestion_is_a_noop(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    relative = archive_cad_document(bundle, record_for(), runs, "Big Horn")
    original = runs / "Big_Horn" / relative
    before = original.stat().st_mtime_ns
    pinned = pin_cad_document(runs, "Big Horn", "sha256:abc123", verify_document=lambda path: path.read_bytes() == b"f3d-bytes")
    assert pinned != relative
    assert not original.exists()
    for _run in (14, 15):
        assert pin_cad_document(runs, "Big Horn", "sha256:abc123", verify_document=lambda path: path.read_bytes() == b"f3d-bytes") == pinned
    assert archive_cad_document(bundle, record_for(), runs, "Big Horn") == pinned
    original = runs / "Big_Horn" / pinned
    assert original.stat().st_mtime_ns == before
    assert list((runs / "Big_Horn").rglob("*.f3d")) == [original]
    assert original.with_suffix(".cad.json").is_file()
    assert not original.with_suffix(".json").exists()


@pytest.mark.parametrize("conflict_kind", ["model", "sidecar", "legacy_sidecar"])
def test_pin_conflict_names_the_file_and_preserves_all_bytes(tmp_path, conflict_kind):
    runs = tmp_path / "runs"
    relative = archive_cad_document(bundle_with_document(tmp_path), record_for(), runs, "Big Horn")
    source = runs / "Big_Horn" / relative
    destination = source.with_name(source.stem + ".pinned.f3d")
    conflict = {"model": destination, "sidecar": destination.with_suffix(".cad.json"),
                "legacy_sidecar": source.with_suffix(".cad.json")}[conflict_kind]
    conflict.write_bytes(b"unrelated file")
    before = {p: p.read_bytes() for p in runs.rglob("*") if p.is_file()}
    with pytest.raises(OSError) as error:
        pin_cad_document(runs, "Big Horn", "sha256:abc123", verify_document=lambda _path: True)
    assert str(conflict) in str(error.value)
    assert {p: p.read_bytes() for p in runs.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("legacy_pin", [False, True])
def test_a_pinned_version_survives_actual_v035_recapture_and_prune(tmp_path: Path, legacy_pin) -> None:
    from server.tests.fixtures import archive_v035 as older

    runs = tmp_path / "runs"
    bundle = bundle_with_document(tmp_path)
    record = record_for()
    first = archive_cad_document(bundle, record, runs, "Big Horn")
    if legacy_pin:
        # Upgrade a sidecar-only pin made by the initial implementation.
        source = runs / "Big_Horn" / first
        source.with_suffix(".json").rename(source.with_suffix(".cad.json"))
    pinned = pin_cad_document(runs, "Big Horn", "sha256:abc123", verify_document=lambda _path: True)
    document = runs / "Big_Horn" / pinned
    sidecar = document.with_suffix(".cad.json")
    before = (document.read_bytes(), sidecar.read_bytes(), document.stat().st_mtime_ns)
    assert older._placed_run_document(document.parent, document.stem, "sha256:abc123") == document
    # Same document/hash/minute: old WG recreates the exact ordinary filename.
    assert older.archive_cad_document(bundle, record, runs, "Big Horn") == first
    assert (runs / "Big_Horn" / first).exists()
    assert captured_cad_document(runs, "Big Horn", "sha256:abc123") == document
    assert pin_cad_document(runs, "Big Horn", "sha256:abc123", verify_document=lambda _path: True) == pinned
    later = record_for()
    later["document"]["return_state_hash"] = "sha256:later"
    older.archive_cad_document(bundle_with_document(tmp_path / "later"), later, runs, "Big Horn")
    older._prune_captured_documents(document.parent, set(), [])
    assert not (runs / "Big_Horn" / first).exists()
    assert (document.read_bytes(), sidecar.read_bytes(), document.stat().st_mtime_ns) == before
    reclaim_captured_documents(runs, "Big Horn")
    assert document.is_file()


def test_pinning_requires_verified_bytes_and_never_overwrites_a_pin(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    relative = archive_cad_document(bundle_with_document(tmp_path), record_for(), runs, "Big Horn")
    original = runs / "Big_Horn" / relative
    assert pin_cad_document(runs, "Big Horn", "sha256:abc123", verify_document=lambda _path: False) is None
    assert original.with_suffix(".json").exists()
    pinned = original.with_suffix(".cad.json")
    pinned.write_text('{"returnStateHash":"sha256:other"}')
    before = pinned.read_bytes()
    with pytest.raises(OSError, match=re.escape(str(pinned))):
        pin_cad_document(runs, "Big Horn", "sha256:abc123", verify_document=lambda _path: True)
    assert pinned.read_bytes() == before


def test_pinning_an_uncaptured_model_does_nothing(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    assert pin_cad_document(runs, "Horn", "sha256:missing", verify_document=lambda _path: True) is None
    assert not runs.exists()


def test_the_captured_document_is_found_by_its_return_state(tmp_path: Path) -> None:
    bundle = bundle_with_document(tmp_path)
    runs = tmp_path / "runs"
    archive_cad_document(bundle, record_for(), runs, "Big Horn")

    found = captured_cad_document(runs, "Big Horn", "sha256:abc123")

    assert found is not None and found.name == "Big_Horn_v7_260819-1000_abc123.f3d"
    assert captured_cad_document(runs, "Big Horn", "sha256:other") is None
    # The sidecar is metadata about the document, never the document itself.
    assert captured_cad_document(runs, "Big Horn", "sha256:abc123").suffix == ".f3d"


def test_the_captured_document_is_found_under_its_legacy_name_too(tmp_path: Path) -> None:
    """A document archived before this change is still found by full digest."""

    runs = tmp_path / "runs"
    directory = runs / "Big_Horn" / "cad"
    directory.mkdir(parents=True)
    (directory / "sha256_abc123.f3d").write_bytes(b"legacy-bytes")
    (directory / "sha256_abc123.json").write_text(
        json.dumps({"schemaVersion": 1, "returnStateHash": "sha256:abc123"}),
        encoding="utf-8",
    )

    found = captured_cad_document(runs, "Big Horn", "sha256:abc123")

    assert found is not None
    assert found.name == "sha256_abc123.f3d"
    assert found.read_bytes() == b"legacy-bytes"


def test_a_digest_fragment_collision_is_disambiguated_by_the_sidecar(
    tmp_path: Path,
) -> None:
    """A shared 12-character fragment must never resolve to the wrong file.

    Two different digests that happen to share their first twelve hex
    characters would, without the sidecar check, let one return's lookup
    silently hand back a different return's document. Both files are placed
    directly rather than through two ``archive_cad_document`` calls, since the
    project-level folder now only ever keeps the newest state and would prune
    the first the moment the second was archived; this isolates the lookup's
    disambiguation from that pruning.
    """

    runs = tmp_path / "runs"
    directory = runs / "Big_Horn" / "cad"
    directory.mkdir(parents=True)

    def place(name: str, content: bytes, digest: str) -> None:
        (directory / f"{name}.f3d").write_bytes(content)
        (directory / f"{name}.json").write_text(
            json.dumps({"schemaVersion": 1, "returnStateHash": digest}),
            encoding="utf-8",
        )

    # Distinct digests that happen to share their first twelve hex characters.
    digest_one = "sha256:abcabcabcabc111111"
    digest_two = "sha256:abcabcabcabc222222"
    place("Big_Horn_v7_260819-1000_abcabcabcabc", b"model-one", digest_one)
    place("Other_Name_260820-1100_abcabcabcabc", b"model-two", digest_two)

    found_one = captured_cad_document(runs, "Big Horn", digest_one)
    found_two = captured_cad_document(runs, "Big Horn", digest_two)

    assert found_one is not None and found_one.read_bytes() == b"model-one"
    assert found_two is not None and found_two.read_bytes() == b"model-two"


@pytest.mark.parametrize("member", ["../secret.f3d", "/secret.f3d", "linked.f3d"])
def test_capture_refuses_members_outside_bundle(tmp_path, member):
    bundle = bundle_with_document(tmp_path)
    secret = tmp_path / "secret.f3d"
    secret.write_bytes(b"private")
    if member == "/secret.f3d":
        member = str(secret)
    elif member == "linked.f3d":
        (bundle / member).symlink_to(secret)
    record = record_for()
    record["document"]["file"] = member
    runs = tmp_path / "runs"
    assert archive_cad_document(bundle, record, runs, "Horn") is None
    assert not runs.exists()


def test_legacy_run_copy_remains_a_verified_source_without_creating_copies(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    old = runs / "Big_Horn" / "14_Big_Horn" / "14_Big_Horn.f3d"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"legacy")
    old.with_suffix(".cad.json").write_text(json.dumps({"returnStateHash": "sha256:abc123"}))
    assert captured_cad_document(runs, "Big Horn", "sha256:abc123", verify_run_document=lambda path: path.read_bytes() == b"legacy") == old
    assert pin_cad_document(runs, "Big Horn", "sha256:abc123", verify_document=lambda path: path.read_bytes() == b"legacy") == "14_Big_Horn/14_Big_Horn.f3d"
    assert list((runs / "Big_Horn").rglob("*.f3d")) == [old]


@pytest.mark.parametrize("after_model_rename", [False, True])
def test_interrupted_pinning_can_be_retried_without_losing_the_model(tmp_path, monkeypatch, after_model_rename):
    from server.workspace import archive

    runs = tmp_path / "runs"
    relative = archive_cad_document(bundle_with_document(tmp_path), record_for(), runs, "Big Horn")
    original = runs / "Big_Horn" / relative
    # Exercise byte-preserving retries with a legacy compact sidecar too.
    sidecar = original.with_suffix(".json")
    raw = json.dumps(json.loads(sidecar.read_bytes())).encode()
    sidecar.write_bytes(raw)
    if after_model_rename:
        real_unlink = Path.unlink

        def interrupted_unlink(path, *args, **kwargs):
            if path == sidecar:
                raise OSError("interrupted after model rename")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", interrupted_unlink)
    else:
        def interrupted_rename(_source, _destination):
            raise OSError("interrupted before model rename")

        monkeypatch.setattr(archive.os, "rename", interrupted_rename)
    with pytest.raises(OSError):
        pin_cad_document(runs, "Big Horn", "sha256:abc123", verify_document=lambda _path: True)
    monkeypatch.undo()
    pinned = pin_cad_document(runs, "Big Horn", "sha256:abc123", verify_document=lambda _path: True)
    document = runs / "Big_Horn" / pinned
    assert document.read_bytes() == b"f3d-bytes"
    assert document.with_suffix(".cad.json").read_bytes() == raw
    assert not document.with_suffix(".json").exists()
    assert list(document.parent.glob("*.f3d")) == [document]
