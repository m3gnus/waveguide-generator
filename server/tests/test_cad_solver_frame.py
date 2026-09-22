"""The solver frame of an unlinked (CAD-authored) snapshot.

docs/architecture/CAD-OPERATIONS.md, "Unlinked solver frame". A return with no
WG instance carries no throat frame, so the user confirms, once per project,
which assembly axis it radiates along. These tests pin the frame contract, the
durable confirmation, and the record-level gate every solve path meets.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from server.cadlink import solver_frame
from server.cadlink.solver_frame import (
    AS_MODELLED,
    AXES,
    CONTRACT,
    CONTRACT_V1,
    FrameConfirmationError,
    allowed_axes,
    confirm_frame,
    confirmation_key,
    frame_matrix,
    frame_preview,
    frame_requirement,
    frame_spec,
    record_frame_refusal,
    record_is_unlinked,
    spec_matrix,
)
from server.cadlink.store import STORE_FORMAT_VERSION, CadLinkStore


REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "frontend" / "src" / "viewport" / "solverFrame.fixture.json"
FIXTURE_V2 = REPO / "frontend" / "src" / "viewport" / "solverFrame.v2.fixture.json"
MANIFEST_SHA = "sha256:" + "a" * 64


def _manifest(**coordinate_system: object) -> dict:
    return {
        "coordinate_system": {
            "length_unit": "mm",
            "handedness": "right",
            "matrix_convention": "row-major-local-to-parent",
            **coordinate_system,
        },
        "assembly": {"file": "assembly.step", "n_bodies_expected": 1},
        "instances": [],
        "sources": [{"id": "s", "role": "HF", "required": True}],
    }


def _unlinked_record(
    *,
    axis: str | None = AS_MODELLED,
    lineage_id: str | None = "wgl_project",
    export_frame: str = "root-component",
    allowed: tuple[str, ...] = AXES,
    contract: str = CONTRACT,
    document_up: str | None = None,
) -> dict:
    if contract == CONTRACT_V1:
        matrix = frame_matrix(axis or AS_MODELLED)
        requirement: dict = {"contract": CONTRACT_V1, "export_frame": export_frame}
        extra: dict = {}
    else:
        manifest = (
            _manifest(document_up=document_up) if document_up else _manifest()
        )
        if document_up:
            manifest["required_features"] = ["document-up-v1"]
        spec = frame_spec(axis or AS_MODELLED, manifest)
        matrix = spec_matrix(spec)
        requirement = {"contract": CONTRACT, "export_frame": export_frame, "document_up": document_up}
        extra = {key: spec[key] for key in ("up", "up_source", "document_up")}
    normalisation: dict = {
        "anchor_instance_id": None,
        "assembly_frame_is_solver_frame": axis in (None, AS_MODELLED),
        "matrix": matrix.tolist(),
    }
    if axis is not None:
        normalisation["solver_frame"] = {
            "contract": contract,
            "axis": axis,
            **extra,
            "requirement": requirement,
            "allowed_axes": list(allowed),
            "matrix": matrix.tolist(),
        }
    return {
        "ingest_id": "wgi_" + "1" * 26,
        "manifest_sha256": MANIFEST_SHA,
        "anchor": {"instance_id": None, "design_id": None, "throat_frame": None},
        "normalisation": normalisation,
        "project": {"lineage_id": lineage_id} if lineage_id else None,
    }


def _linked_record() -> dict:
    return {
        "ingest_id": "wgi_" + "2" * 26,
        "manifest_sha256": MANIFEST_SHA,
        "anchor": {"instance_id": "i", "design_id": "d", "throat_frame": {}},
        "normalisation": {"anchor_instance_id": "i", "assembly_frame_is_solver_frame": False},
        "project": {"lineage_id": "wgl_project"},
    }


# -- the frame contract --------------------------------------------------------


@pytest.mark.parametrize("axis", AXES)
def test_every_axis_is_a_proper_rotation_taking_that_axis_to_solver_plus_z(axis: str) -> None:
    matrix = frame_matrix(axis)
    assert matrix.shape == (4, 4)
    rotation = matrix[:3, :3]
    assert np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-12)
    assert np.isclose(np.linalg.det(rotation), 1.0, atol=1e-12)
    assert np.allclose(matrix[:3, 3], 0.0)  # the origin is kept
    assert np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0])
    sign = -1.0 if axis[0] == "-" else 1.0
    direction = np.zeros(3)
    direction["xyz".index(axis[1])] = sign
    assert np.allclose(rotation @ direction, [0.0, 0.0, 1.0], atol=1e-12)


def test_as_modelled_is_the_identity_and_the_default() -> None:
    assert AS_MODELLED == "+z"
    assert np.array_equal(frame_matrix("+z"), np.eye(4))


def test_the_minimal_rotation_keeps_the_shared_perpendicular_axis() -> None:
    # About X for the Y axes and -Z, about Y for the X axes: fixed, so a
    # confirmed frame means the same horizontal plane on every export.
    for axis in ("+y", "-y", "-z"):
        assert np.allclose(frame_matrix(axis)[:3, :3] @ [1, 0, 0], [1, 0, 0])
    for axis in ("+x", "-x"):
        assert np.allclose(frame_matrix(axis)[:3, :3] @ [0, 1, 0], [0, 1, 0])


def test_an_unknown_axis_is_refused() -> None:
    for axis in ("z", "+Z", "", None, "+w"):
        with pytest.raises(ValueError):
            frame_matrix(axis)  # type: ignore[arg-type]


def test_the_frontend_fixture_is_exactly_this_contract() -> None:
    """The preview applies these numbers; the Python contract decides them."""

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # The fixture pins contract v1, the matrices historical records keep; a v2
    # preview hands the frontend its matrices, which it only applies.
    assert fixture["contract"] == CONTRACT_V1
    assert fixture["matrixConvention"] == "row-major"
    assert sorted(fixture["axes"]) == sorted(AXES)
    for axis in AXES:
        assert fixture["axes"][axis] == frame_matrix(axis).tolist()


def test_the_frontend_v2_fixture_is_exactly_the_current_contract() -> None:
    """The frontend's v2 fixture: every axis's matrix and up, with no document up."""

    fixture = json.loads(FIXTURE_V2.read_text(encoding="utf-8"))
    assert fixture["contract"] == CONTRACT == "cad-solver-frame-v2"
    assert fixture["matrixConvention"] == "row-major"
    assert fixture["documentUp"] is None
    assert sorted(fixture["axes"]) == sorted(AXES)
    for axis in AXES:
        spec = frame_spec(axis, _manifest())
        assert fixture["axes"][axis] == spec_matrix(spec).tolist()
        assert fixture["up"][axis] == spec["up"]


def test_the_requirement_is_the_contract_the_export_frame_and_the_document_up() -> None:
    assert frame_requirement(_manifest()) == {
        "contract": CONTRACT, "export_frame": "root-component", "document_up": None,
    }
    assert frame_requirement(_manifest(export_frame="selected-occurrence-component")) == {
        "contract": CONTRACT,
        "export_frame": "selected-occurrence-component",
        "document_up": None,
    }
    stated = _manifest(document_up="+y")
    # Only under the feature: a stray field states nothing.
    assert frame_requirement(stated)["document_up"] is None
    stated["required_features"] = ["document-up-v1"]
    assert frame_requirement(stated)["document_up"] == "+y"


def test_a_declared_reduced_domain_allows_only_the_modelled_frame() -> None:
    assert allowed_axes(_manifest()) == AXES
    half = _manifest()
    half["assembly"]["domain"] = {"cut_planes": ["x0"]}
    assert allowed_axes(half) == (AS_MODELLED,)


def test_the_key_is_the_project_or_else_the_exact_snapshot() -> None:
    assert confirmation_key("wgl_project", MANIFEST_SHA) == "lineage:wgl_project"
    assert confirmation_key(None, MANIFEST_SHA) == f"snapshot:{MANIFEST_SHA}"
    assert confirmation_key("  ", MANIFEST_SHA) == f"snapshot:{MANIFEST_SHA}"


# -- the durable confirmation --------------------------------------------------


def test_a_confirmation_is_stored_overwritten_and_absent_by_default(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    requirement = {"contract": CONTRACT, "export_frame": "root-component"}
    assert store.get_frame_confirmation("lineage:p") is None
    first = store.record_frame_confirmation("lineage:p", requirement, "+y")
    assert first["axis"] == "+y" and first["requirement"] == requirement
    second = store.record_frame_confirmation("lineage:p", requirement, "-x")
    assert store.get_frame_confirmation("lineage:p")["axis"] == "-x"
    assert second["confirmed_at"] >= first["confirmed_at"]
    with pytest.raises(ValueError):
        store.record_frame_confirmation("lineage:p", requirement, "sideways")


def test_the_table_is_additive_and_an_existing_file_gains_it_without_rows(tmp_path: Path) -> None:
    path = tmp_path / "cadlink.db"
    CadLinkStore(path).initialize()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE cad_frame_confirmations")
        conn.execute("PRAGMA user_version = 11")
    reopened = CadLinkStore(path)
    reopened.initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == STORE_FORMAT_VERSION == 11
        assert conn.execute("SELECT COUNT(*) FROM cad_frame_confirmations").fetchone()[0] == 0
    # Absence is unconfirmed: nothing was manufactured at open.
    assert reopened.get_frame_confirmation("lineage:wgl_project") is None


# -- the record gate every solve path meets ------------------------------------


def test_a_linked_record_is_never_gated(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    assert record_is_unlinked(_linked_record()) is False
    assert record_frame_refusal(store, _linked_record()) is None


def test_an_unconfirmed_unlinked_record_is_refused(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    record = _unlinked_record()
    assert record_is_unlinked(record) is True
    message = record_frame_refusal(store, record)
    assert message is not None and "solver frame" in message


def test_a_confirmed_matching_frame_passes_and_any_difference_is_refused(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    record = _unlinked_record(axis="+y")
    confirm_frame(store, record, "+y")
    assert record_frame_refusal(store, record) is None
    # Re-confirmed to another axis: this record was meshed in the old one.
    confirm_frame(store, record, "+z")
    assert record_frame_refusal(store, record) is not None


def test_a_changed_export_frame_needs_confirming_again(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    confirm_frame(store, _unlinked_record(axis="+y"), "+y")
    moved = _unlinked_record(axis="+y", export_frame="selected-occurrence-component")
    assert record_frame_refusal(store, moved) is not None


def test_later_exports_of_the_same_project_reuse_the_confirmation(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    confirm_frame(store, _unlinked_record(axis="-x"), "-x")
    later = _unlinked_record(axis="-x")
    later["manifest_sha256"] = "sha256:" + "b" * 64
    later["ingest_id"] = "wgi_" + "3" * 26
    assert record_frame_refusal(store, later) is None


def test_a_projectless_snapshot_is_confirmed_for_itself_only(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    confirm_frame(store, _unlinked_record(lineage_id=None), "+z")
    assert record_frame_refusal(store, _unlinked_record(lineage_id=None)) is None
    other = _unlinked_record(lineage_id=None)
    other["manifest_sha256"] = "sha256:" + "c" * 64
    assert record_frame_refusal(store, other) is not None


def test_a_record_from_before_the_contract_is_never_taken_as_confirmed(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    confirm_frame(store, _unlinked_record(), "+z")
    legacy = _unlinked_record(axis=None)
    assert record_is_unlinked(legacy) is True
    message = record_frame_refusal(store, legacy)
    assert message is not None and "again" in message


def test_confirming_refuses_a_linked_record_and_a_disallowed_axis(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    with pytest.raises(FrameConfirmationError):
        confirm_frame(store, _linked_record(), "+z")
    half = _unlinked_record(allowed=(AS_MODELLED,))
    with pytest.raises(FrameConfirmationError):
        confirm_frame(store, half, "+y")
    with pytest.raises(FrameConfirmationError):
        confirm_frame(store, _unlinked_record(), "up")
    assert store.get_frame_confirmation("lineage:wgl_project") is None


def test_a_confirmed_axis_this_snapshot_does_not_allow_is_refused(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    confirm_frame(store, _unlinked_record(axis="+y"), "+y")
    half = _unlinked_record(axis=AS_MODELLED, allowed=(AS_MODELLED,))
    message = record_frame_refusal(store, half)
    assert message is not None and "half" in message


def test_the_preview_names_each_axis_matrix_relative_to_the_displayed_record(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    record = _unlinked_record(axis="+y")
    preview = frame_preview(store, record)
    assert preview["linked"] is False
    assert preview["recordAxis"] == "+y"
    assert preview["confirmed"] is None
    assert preview["contract"] == CONTRACT
    assert preview["requirement"] == {
        "contract": CONTRACT, "export_frame": "root-component", "document_up": None,
    }
    by_axis = {item["axis"]: item for item in preview["axes"]}
    assert list(by_axis) == list(AXES)
    for axis, item in by_axis.items():
        matrix = spec_matrix(frame_spec(axis, _manifest()))
        assert item["solverFromAssembly"] == matrix.tolist()
        expected = matrix @ np.linalg.inv(spec_matrix(frame_spec("+y", _manifest())))
        assert np.allclose(item["previewFromRecord"], expected, atol=1e-12)
        assert item["allowed"] is True
        assert item["up"] == ("+y" if axis[1] == "z" else "+z")
    confirm_frame(store, record, "-z")
    assert frame_preview(store, record)["confirmed"]["axis"] == "-z"
    assert frame_preview(store, _linked_record()) == {"linked": True}


def test_resolving_a_manifest_uses_the_confirmed_axis_or_the_modelled_frame(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    manifest = _manifest()
    resolved = solver_frame.resolve_for_manifest(store, manifest, MANIFEST_SHA)
    assert resolved is not None
    assert (resolved.confirmed_axis, resolved.axis) == (None, AS_MODELLED)
    store.record_frame_confirmation(resolved.key, resolved.requirement, "+x")
    again = solver_frame.resolve_for_manifest(store, manifest, MANIFEST_SHA)
    assert (again.confirmed_axis, again.axis, again.confirmed) == ("+x", "+x", True)
    linked = _manifest()
    linked["instances"] = [{"instance_id": "i"}]
    assert solver_frame.resolve_for_manifest(store, linked, MANIFEST_SHA) is None
