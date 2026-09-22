"""Solver frame contract v2: the roll, the document up, and what history keeps.

docs/architecture/CAD-OPERATIONS.md, "Unlinked solver frame". v2 fixes the
horizontal polar plane: it contains the forward axis and is perpendicular to
the model's up, which is the CAD document's up when the return states it and
CAD +Z (or +Y for a model radiating along +-Z) when it does not. Records and
confirmations made under v1 keep their meaning.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from server.cadlink.solver_frame import (
    AS_MODELLED,
    AXES,
    CONTRACT,
    CONTRACT_V1,
    CONTRACT_V2,
    DOCUMENT_UP_FEATURE,
    confirm_frame,
    frame_matrix,
    frame_preview,
    frame_requirement,
    frame_spec,
    record_frame_refusal,
    record_solver_frame,
    resolve_up,
    spec_matrix,
)
from server.cadlink.store import CadLinkStore
from server.cadlink.wgreturn import WgReturnValidationError, validate_manifest

from test_cadlink_wgreturn import _manifest as wgreturn_manifest

VECTORS = {
    "+x": np.array((1.0, 0, 0)), "-x": np.array((-1.0, 0, 0)),
    "+y": np.array((0, 1.0, 0)), "-y": np.array((0, -1.0, 0)),
    "+z": np.array((0, 0, 1.0)), "-z": np.array((0, 0, -1.0)),
}


def _manifest(document_up: str | None = None, **assembly: object) -> dict:
    manifest: dict = {
        "required_features": ["checksummed-files-v1"],
        "coordinate_system": {"length_unit": "mm"},
        "assembly": {"file": "assembly.step", **assembly},
        "instances": [],
    }
    if document_up is not None:
        manifest["required_features"].append(DOCUMENT_UP_FEATURE)
        manifest["coordinate_system"]["document_up"] = document_up
    return manifest


@pytest.mark.parametrize("document_up", [None, "+y", "+z"])
@pytest.mark.parametrize("axis", AXES)
def test_every_forward_axis_under_every_up_rule(axis: str, document_up: str | None) -> None:
    spec = frame_spec(axis, _manifest(document_up))
    matrix = spec_matrix(spec)
    rotation = matrix[:3, :3]
    assert spec["contract"] == CONTRACT_V2 == CONTRACT
    assert np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-12)
    assert np.isclose(np.linalg.det(rotation), 1.0)
    assert np.allclose(matrix[:3, 3], 0.0) and np.allclose(matrix[3], [0, 0, 0, 1])
    forward, up = VECTORS[axis], VECTORS[spec["up"]]
    # Forward is solver +Z, up is solver +Y.
    assert np.allclose(rotation @ forward, [0, 0, 1])
    assert np.allclose(rotation @ up, [0, 1, 0])
    # The horizontal polar plane (solver x-z) holds forward and is normal to up.
    horizontal_normal = rotation.T @ np.array((0.0, 1.0, 0.0))
    assert np.isclose(horizontal_normal @ forward, 0.0)
    assert np.allclose(horizontal_normal, up)
    expected_up, expected_source = {
        None: (("+y", "default") if axis[1] == "z" else ("+z", "default")),
        "+y": (("+z", "forward-parallel-to-document-up") if axis[1] == "y" else ("+y", "document")),
        "+z": (("+y", "forward-parallel-to-document-up") if axis[1] == "z" else ("+z", "document")),
    }[document_up]
    assert (spec["up"], spec["up_source"], spec["document_up"]) == (expected_up, expected_source, document_up)


@pytest.mark.parametrize("document_up", [None, "+y", "+z"])
def test_the_modelled_frame_is_the_identity_under_every_up_rule(document_up: str | None) -> None:
    """So the modelled frame and every declared half keep the transform they had."""

    assert np.array_equal(spec_matrix(frame_spec(AS_MODELLED, _manifest(document_up))), np.eye(4))


def test_a_z_up_document_puts_the_horizontal_plane_in_cad_x_y_for_a_model_facing_x() -> None:
    """The v1 roll put it in CAD x-z, which is vertical in a Z-up document."""

    v2 = spec_matrix(frame_spec("+x", _manifest("+z")))[:3, :3]
    v1 = frame_matrix("+x")[:3, :3]
    # Solver x (the horizontal polar direction) in CAD coordinates.
    assert np.allclose(v2.T @ [1, 0, 0], [0, 1, 0])
    assert np.allclose(v1.T @ [1, 0, 0], [0, 0, -1])
    y_up = spec_matrix(frame_spec("+x", _manifest("+y")))[:3, :3]
    assert np.allclose(y_up.T @ [1, 0, 0], [0, 0, -1])


def test_forward_parallel_to_the_document_up_is_explicit() -> None:
    assert resolve_up("+y", "+y") == ("+z", "forward-parallel-to-document-up")
    assert resolve_up("-y", "+y") == ("+z", "forward-parallel-to-document-up")
    assert resolve_up("-z", "+z") == ("+y", "forward-parallel-to-document-up")
    with pytest.raises(ValueError):
        resolve_up("+x", "-y")


def test_a_frame_whose_up_does_not_follow_the_contract_is_refused() -> None:
    spec = frame_spec("+x", _manifest("+z"))
    with pytest.raises(ValueError):
        spec_matrix({**spec, "up": "+y"})
    with pytest.raises(ValueError):
        spec_matrix({**spec, "up_source": "default"})
    with pytest.raises(ValueError):
        spec_matrix({**spec, "contract": "cad-solver-frame-v9"})


def test_a_v1_axis_or_v1_frame_still_means_the_v1_matrix() -> None:
    for axis in AXES:
        assert np.array_equal(spec_matrix(axis), frame_matrix(axis))
        assert np.array_equal(spec_matrix({"contract": CONTRACT_V1, "axis": axis}), frame_matrix(axis))


def test_a_declared_domain_keeps_the_modelled_transform() -> None:
    half = _manifest("+z", domain={"kind": "half", "cut_planes": ["x0"]})
    frame = record_solver_frame(half, AS_MODELLED)
    assert frame["allowed_axes"] == [AS_MODELLED]
    assert np.array_equal(np.asarray(frame["matrix"]), np.eye(4))
    with pytest.raises(ValueError):
        record_solver_frame(half, frame_spec("+x", half))


# -- identities: confirmation, preview, history ---------------------------------


def _record(manifest: dict, axis: str, *, contract: str = CONTRACT, sha: str = "a") -> dict:
    if contract == CONTRACT_V1:
        frame = {
            "contract": CONTRACT_V1,
            "axis": axis,
            "requirement": {"contract": CONTRACT_V1, "export_frame": "root-component"},
            "allowed_axes": list(AXES),
            "matrix": frame_matrix(axis).tolist(),
        }
    else:
        frame = record_solver_frame(manifest, axis)
    return {
        "ingest_id": "wgi_" + "1" * 26,
        "manifest_sha256": "sha256:" + sha * 64,
        "anchor": {"instance_id": None, "design_id": None, "throat_frame": None},
        "normalisation": {"anchor_instance_id": None, "matrix": frame["matrix"], "solver_frame": frame},
        "project": {"lineage_id": "wgl_project"},
    }


def test_the_confirmation_records_the_whole_transform_and_its_up(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    manifest = _manifest("+y")
    record = _record(manifest, "+x")
    row = confirm_frame(store, record, "+x")
    assert row["requirement"] == frame_requirement(manifest)
    assert row["requirement"]["document_up"] == "+y"
    frame = row["frame"]
    assert (frame["contract"], frame["axis"], frame["up"], frame["up_source"]) == (
        CONTRACT_V2, "+x", "+y", "document",
    )
    assert frame["matrix"] == spec_matrix(frame_spec("+x", manifest)).tolist()
    assert frame["provenance"] == "chosen"
    assert record_frame_refusal(store, record) is None


def test_a_changed_document_up_is_a_different_frame(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    confirm_frame(store, _record(_manifest("+y"), "+x"), "+x")
    assert record_frame_refusal(store, _record(_manifest("+z"), "+x", sha="b")) is not None
    assert record_frame_refusal(store, _record(_manifest(), "+x", sha="c")) is not None


def test_a_historical_record_keeps_its_contract(tmp_path: Path) -> None:
    """A v1 record, confirmed under v1, still solves and previews as v1."""

    store = CadLinkStore(tmp_path / "cadlink.db")
    old = _record(_manifest(), "+x", contract=CONTRACT_V1)
    assert record_frame_refusal(store, old) is not None
    row = confirm_frame(store, old, "+x")
    assert row["requirement"] == {"contract": CONTRACT_V1, "export_frame": "root-component"}
    assert row["frame"]["matrix"] == frame_matrix("+x").tolist()
    assert record_frame_refusal(store, old) is None
    preview = frame_preview(store, old)
    assert preview["contract"] == CONTRACT_V1
    by_axis = {item["axis"]: item for item in preview["axes"]}
    for axis in AXES:
        assert by_axis[axis]["solverFromAssembly"] == frame_matrix(axis).tolist()
    assert preview["confirmed"]["axis"] == "+x"
    # The v1 confirmation is not a v2 one: a new preparation is confirmed anew.
    new = _record(_manifest(), "+x", sha="d")
    assert record_frame_refusal(store, new) is not None
    # Confirming it moves the project to v2. The old record still states its
    # v1 frame, and is prepared again rather than solved under a frame the
    # project no longer confirms.
    confirm_frame(store, new, "+x")
    assert record_frame_refusal(store, new) is None
    assert old["normalisation"]["solver_frame"]["contract"] == CONTRACT_V1
    assert record_frame_refusal(store, old) is not None


def test_the_preview_states_the_record_frame_and_each_axis_up(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    manifest = _manifest("+z")
    preview = frame_preview(store, _record(manifest, "+x"))
    assert preview["recordFrame"]["up"] == "+z"
    assert preview["recordFrame"]["upSource"] == "document"
    assert preview["recordFrame"]["documentUp"] == "+z"
    by_axis = {item["axis"]: item for item in preview["axes"]}
    assert (by_axis["+z"]["up"], by_axis["+z"]["upSource"]) == ("+y", "forward-parallel-to-document-up")
    assert (by_axis["-y"]["up"], by_axis["-y"]["upSource"]) == ("+z", "document")
    assert np.allclose(by_axis["+x"]["previewFromRecord"], np.eye(4))


# -- the manifest field -----------------------------------------------------------


def _unlinked_manifest() -> dict:
    return wgreturn_manifest(b"STEP")


def test_document_up_is_read_only_under_its_feature() -> None:
    manifest = _unlinked_manifest()
    validate_manifest(manifest)
    stated = _unlinked_manifest()
    stated["coordinate_system"]["document_up"] = "+y"
    with pytest.raises(WgReturnValidationError, match="document-up-v1"):
        validate_manifest(stated)
    stated["required_features"].append(DOCUMENT_UP_FEATURE)
    validate_manifest(stated)
    bare = _unlinked_manifest()
    bare["required_features"].append(DOCUMENT_UP_FEATURE)
    with pytest.raises(WgReturnValidationError, match="document-up-v1"):
        validate_manifest(bare)
    for bad in ("-y", "+x", "y", 1):
        wrong = _unlinked_manifest()
        wrong["required_features"].append(DOCUMENT_UP_FEATURE)
        wrong["coordinate_system"]["document_up"] = bad
        with pytest.raises(WgReturnValidationError):
            validate_manifest(wrong)
