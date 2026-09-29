"""The per-source axis an axial CAD Link source moves along (``per-source-axis-v2``)."""

from __future__ import annotations

import io
import json
import math

import numpy as np
import pytest

from server.solver import imported
from server.solver.combine import serialize_channel_bases
from server.solver.imported import (
    AXIAL_CONTRACT_VERSION,
    SourceAxisError,
    axial_domain_problem,
    resolve_source_axes,
)


def _msh(nodes, triangles) -> str:
    rows = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", str(len(nodes))]
    rows += [f"{i} {x!r} {y!r} {z!r}" for i, (x, y, z) in enumerate(nodes, start=1)]
    rows += ["$EndNodes", "$Elements", str(len(triangles))]
    rows += [f"{i} 2 2 {t} {t} {a} {b} {c}" for i, (t, (a, b, c)) in enumerate(triangles, start=1)]
    return "\n".join([*rows, "$EndElements", ""])


def _cap(*, radius: float = 0.05, half_angle_deg: float = 30.0, rings: int = 6, sectors: int = 24,
         sector_range: tuple[int, int] | None = None, flip: bool = False):
    """A spherical cap about +z (apex at the origin), as nodes and (a, b, c) fans."""

    sphere_r = radius / math.sin(math.radians(half_angle_deg))
    nodes = [(0.0, 0.0, 0.0)]
    for ring in range(1, rings + 1):
        polar = math.radians(half_angle_deg) * ring / rings
        for sector in range(sectors):
            azimuth = 2.0 * math.pi * sector / sectors
            nodes.append((
                sphere_r * math.sin(polar) * math.cos(azimuth),
                sphere_r * math.sin(polar) * math.sin(azimuth),
                sphere_r * (1.0 - math.cos(polar)),
            ))
    def node(ring: int, sector: int) -> int:
        return 1 if ring == 0 else 2 + (ring - 1) * sectors + (sector % sectors)
    triangles = []
    lo, hi = sector_range if sector_range is not None else (0, sectors)
    for sector in range(lo, hi):
        triangles.append((node(0, 0), node(1, sector), node(1, sector + 1)))
        for ring in range(1, rings):
            a, b = node(ring, sector), node(ring, sector + 1)
            c, d = node(ring + 1, sector), node(ring + 1, sector + 1)
            triangles += [(a, c, d), (a, d, b)]
    if flip:
        triangles = [(a, c, b) for a, b, c in triangles]
    return nodes, triangles


def test_a_flat_source_moves_along_its_normal() -> None:
    text = _msh([(0, 0, 0), (0.01, 0, 0), (0, 0.01, 0)], [(101, (1, 2, 3))])
    found = resolve_source_axes(text, [101])[101]
    assert found.axis == (0.0, 0.0, 1.0)
    assert found.snapped_to == "+z"
    assert found.area_m2 == pytest.approx(5.0e-5)
    assert found.net_area_ratio == pytest.approx(1.0)


def test_the_winding_makes_the_axis_outward_positive_with_no_vote() -> None:
    nodes, triangles = _cap(flip=True)
    found = resolve_source_axes(_msh(nodes, [(101, t) for t in triangles]), [101])[101]
    assert found.axis == (0.0, 0.0, -1.0) and found.snapped_to == "-z"


def test_a_curved_cap_moves_along_its_cap_axis_not_a_face_normal() -> None:
    nodes, triangles = _cap(half_angle_deg=40.0)
    found = resolve_source_axes(_msh(nodes, [(101, t) for t in triangles]), [101])[101]
    assert found.axis == (0.0, 0.0, 1.0)
    # Net area vector of a cap is its projected disc area, well below the cap area.
    assert 0.7 < found.net_area_ratio < 0.95


def test_a_tilted_source_keeps_its_tilt_and_records_the_raw_axis() -> None:
    tilt = math.radians(10.0)
    nodes, triangles = _cap(half_angle_deg=20.0)
    rotation = np.asarray([[1, 0, 0], [0, math.cos(tilt), -math.sin(tilt)], [0, math.sin(tilt), math.cos(tilt)]])
    tilted = [tuple(float(v) for v in rotation @ np.asarray(n)) for n in nodes]
    found = resolve_source_axes(_msh(tilted, [(101, t) for t in triangles]), [101])[101]
    assert found.snapped_to is None
    assert found.axis == found.raw_axis
    np.testing.assert_allclose(found.axis, (0.0, -math.sin(tilt), math.cos(tilt)), atol=1e-12)


@pytest.mark.parametrize(("degrees", "snaps"), [(0.3, True), (0.49, True), (0.51, False), (2.0, False)])
def test_an_axis_within_half_a_degree_of_a_frame_axis_snaps_exactly(degrees: float, snaps: bool) -> None:
    tilt = math.radians(degrees)
    nodes = [(0, 0, 0), (0.01, 0, 0), (0, 0.01 * math.cos(tilt), 0.01 * math.sin(tilt))]
    found = resolve_source_axes(_msh(nodes, [(101, (1, 2, 3))]), [101])[101]
    assert (found.snapped_to == "+z") is snaps
    if snaps:
        assert found.axis == (0.0, 0.0, 1.0)
        assert found.raw_axis != found.axis
    else:
        assert found.axis == found.raw_axis


def test_a_two_sided_or_closed_source_has_no_axis_and_is_refused() -> None:
    two_sided = _msh(
        [(0, 0, 0), (0.01, 0, 0), (0, 0.01, 0)], [(101, (1, 2, 3)), (101, (1, 3, 2))]
    )
    with pytest.raises(SourceAxisError, match="tag 101.*no outward axis"):
        resolve_source_axes(two_sided, [101])
    with pytest.raises(SourceAxisError, match="tag 7 has no faces"):
        resolve_source_axes(two_sided, [7])


def test_a_mirror_cut_source_is_projected_onto_the_symmetry_and_matches_the_whole() -> None:
    nodes, triangles = _cap(half_angle_deg=35.0, sectors=24)
    whole = resolve_source_axes(_msh(nodes, [(101, t) for t in triangles]), [101])[101]
    # Half the cap: azimuth 0..180 degrees, so the x = 0 plane cuts it along its
    # edge. The half's own net area vector leans toward +y and its projection
    # onto the x0 symmetry is the whole cap's axis.
    half_nodes, half_triangles = _cap(half_angle_deg=35.0, sectors=24, sector_range=(0, 12))
    text = _msh(half_nodes, [(101, t) for t in half_triangles])
    unprojected = resolve_source_axes(text, [101])[101]
    assert unprojected.axis != whole.axis
    projected = resolve_source_axes(text, [101], ["y0"])[101]
    assert projected.projected_planes == ("y0",)
    assert projected.axis == whole.axis == (0.0, 0.0, 1.0)
    assert projected.in_symmetry_subspace()


def test_a_source_the_mirror_does_not_touch_is_never_projected_but_flagged() -> None:
    tilt = math.radians(20.0)
    nodes = [(0.05, 0, 0), (0.06, 0, 0.01 * math.sin(tilt)), (0.05, 0.01, 0)]
    found = resolve_source_axes(_msh(nodes, [(101, (1, 2, 3))]), [101], ["x0"])[101]
    assert found.projected_planes == ()
    assert found.off_symmetry_planes == ("x0",)
    assert not found.in_symmetry_subspace()
    problem = axial_domain_problem({101: found}, ["x0"])
    assert problem is not None and "tag 101" in problem and "Solve it as shown" in problem
    assert axial_domain_problem({101: found}, []) is None


def test_the_axis_does_not_depend_on_which_channel_or_frame_asks() -> None:
    nodes, triangles = _cap(half_angle_deg=25.0)
    text = _msh(nodes, [(101, t) for t in triangles] + [(102, t) for t in triangles])
    both = resolve_source_axes(text, [101, 102])
    assert both[101].axis == both[102].axis == (0.0, 0.0, 1.0)


def test_prepare_axial_drive_records_contract_axes_and_only_for_axial_channels() -> None:
    text = _msh(
        [(0, 0, 0), (0.01, 0, 0), (0, 0.01, 0), (0, 0, 1), (0.01, 0, 1), (0, 0.01, 1)],
        [(101, (1, 2, 3)), (102, (4, 6, 5))],
    )
    record = {"source_tags": {"a": 101, "b": 102}, "symmetry": {"domain_planes": []}}
    channels = [
        {"id": "up", "source_ids": ["a"], "motion": "axial"},
        {"id": "plain", "source_ids": ["b"], "motion": "normal"},
    ]
    axes, identity = imported.prepare_axial_drive(record, text, channels)
    assert set(axes) == {101} and set(identity) == {"up"}
    assert identity["up"]["axial_contract"] == AXIAL_CONTRACT_VERSION == "per-source-axis-v2"
    assert identity["up"]["source_motion"] == "axial"
    assert identity["up"]["source_axes"][0]["axis"] == [0.0, 0.0, 1.0]
    # No axial channel: nothing is parsed, so even an unreadable mesh is fine.
    assert imported.prepare_axial_drive(record, "not a mesh", channels[1:]) == ({}, {})


def _bases_with(metadata: dict) -> tuple[bytes, dict]:
    from types import SimpleNamespace

    result = SimpleNamespace(
        frequencies_hz=np.asarray([100.0]),
        observation_angles_deg=np.asarray([0.0]),
        observation_planes=["horizontal"],
        pressure_complex=np.ones((1, 1, 1), dtype=np.complex128),
        directivity_db=np.zeros((1, 1, 1)),
        impedance=np.ones(1, dtype=np.complex128),
        sphere_pressure_complex=None,
    )
    return serialize_channel_bases({"c": result}, metadata_by_id={"c": metadata}), {}


def test_a_pressure_basis_names_its_axial_contract_and_a_legacy_one_says_so() -> None:
    from server.solver.pressure_basis import export_pressure_basis

    base = {"source_ids": ["a"], "source_tags": [101], "source_normalization": "unit_normal_acceleration"}
    v2 = {
        **base, "source_motion": "axial", "axial_contract": AXIAL_CONTRACT_VERSION,
        "source_axes": [{"tag": 101, "axis": [0.0, 0.0, -1.0], "raw_axis": [0.001, 0.0, -1.0]}],
    }
    for metadata, contract in ((v2, AXIAL_CONTRACT_VERSION), ({**base, "source_motion": "axial"}, "legacy-frame-axis-v1")):
        npz, _ = _bases_with(metadata)
        exported = export_pressure_basis(npz, {}, "c")
        archive = np.load(io.BytesIO(exported.content))
        assert str(archive["axial_contract"]) == contract
        assert str(archive["source_motion"]) == "axial"
    assert archive.files.count("source_axes") == 0
    npz, _ = _bases_with(v2)
    archive = np.load(io.BytesIO(export_pressure_basis(npz, {}, "c").content))
    np.testing.assert_array_equal(archive["source_axes"], [[0.0, 0.0, -1.0]])
    np.testing.assert_array_equal(archive["source_axes_raw"], [[0.001, 0.0, -1.0]])
    # A normal channel carries no axial contract at all.
    npz, _ = _bases_with({**base, "source_motion": "normal"})
    assert "axial_contract" not in np.load(io.BytesIO(export_pressure_basis(npz, {}, "c").content)).files
    assert json.loads(json.dumps(v2)) == v2


def test_a_tilted_source_touching_the_plane_at_one_vertex_is_not_projected() -> None:
    tilt = math.radians(20.0)
    # One corner on x = 0, the rest on x > 0, the face leaning across x.
    nodes = [(0.0, 0.0, 0.0), (0.02, 0.0, 0.02 * math.tan(tilt)), (0.01, 0.01, 0.0)]
    text = _msh(nodes, [(101, (1, 2, 3))])
    found = resolve_source_axes(text, [101], ["x0"])[101]
    assert found.projected_planes == ()
    assert found.off_symmetry_planes == ("x0",)
    assert "not in the symmetry" in axial_domain_problem({101: found}, ["x0"])


def test_a_short_sliver_on_the_plane_does_not_count_as_a_cut() -> None:
    nodes = [(0.0, 0.0, 0.0), (0.0, 1.0e-5, 0.0), (0.05, 0.0, 0.03)]
    found = resolve_source_axes(_msh(nodes, [(101, (1, 2, 3))]), [101], ["x0"])[101]
    assert found.projected_planes == ()


def test_a_basis_with_no_stored_motion_recovers_it_from_the_request_or_is_refused() -> None:
    from server.solver.pressure_basis import export_pressure_basis

    base = {"source_ids": ["a"], "source_tags": [101], "source_normalization": "unit_normal_acceleration"}
    npz, _ = _bases_with(base)
    # An archived pre-v2 axial basis: motion comes from the archived request, and
    # with no recorded contract it is labelled as the frame-axis rule.
    archive = np.load(io.BytesIO(export_pressure_basis(npz, {}, "c", {"c": "axial"}).content))
    assert str(archive["source_motion"]) == "axial"
    assert str(archive["axial_contract"]) == "legacy-frame-axis-v1"
    # The results' own channel metadata also establishes it.
    results = {"channels": {"c": {"metadata": {"source_motion": "axial"}}}}
    archive = np.load(io.BytesIO(export_pressure_basis(npz, results, "c").content))
    assert str(archive["axial_contract"]) == "legacy-frame-axis-v1"
    archive = np.load(io.BytesIO(export_pressure_basis(npz, {}, "c", {"c": "normal"}).content))
    assert str(archive["source_motion"]) == "normal" and "axial_contract" not in archive.files
    # Nothing establishes it: refuse, never guess normal.
    with pytest.raises(ValueError, match="cannot be established"):
        export_pressure_basis(npz, {}, "c")


def test_a_v2_result_whose_basis_lost_its_metadata_exports_as_v2_with_its_axes() -> None:
    from server.solver.pressure_basis import export_pressure_basis

    npz, _ = _bases_with(
        {"source_ids": ["a"], "source_tags": [101], "source_normalization": "unit_normal_acceleration"}
    )
    results = {
        "channels": {
            "c": {
                "metadata": {
                    "source_motion": "axial",
                    "axial_contract": AXIAL_CONTRACT_VERSION,
                    "source_axes": [{"tag": 101, "axis": [0.0, 0.0, -1.0], "raw_axis": [0.001, 0.0, -1.0]}],
                }
            }
        }
    }
    archive = np.load(io.BytesIO(export_pressure_basis(npz, results, "c").content))
    assert str(archive["axial_contract"]) == AXIAL_CONTRACT_VERSION
    np.testing.assert_array_equal(archive["source_axes"], [[0.0, 0.0, -1.0]])
    np.testing.assert_array_equal(archive["source_axes_raw"], [[0.001, 0.0, -1.0]])
