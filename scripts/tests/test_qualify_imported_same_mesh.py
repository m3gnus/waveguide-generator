"""The imported-solve qualification harness must be able to fail.

``scripts/qualify_imported_same_mesh.py`` judges Metal and BEAT-CPU against
analytic spheres and against each other. Its verdicts are only as good as its
geometry, its analytic references and its error measure, so each of those is
pinned here with an input whose answer is known: an inward-wound sphere, a
reference with the wrong time convention or sign, or a metric that cannot see a
sign flip would each pass every engine comparison and be caught only here.

The fixture test guards the one mistake that has already happened: a return
whose throat contract bound to the plug's rear face instead of the throat disc,
which made WG's quarter arrive inside out and both engines agree on nothing.

The real run is opt-in (``WG2_QUALIFY_IMPORTED=1``): it needs Metal and a
provisioned BEAT CPU runtime, and takes minutes.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from scripts import qualify_imported_same_mesh as qual


def _signed_volume(points: np.ndarray, triangles: np.ndarray) -> float:
    corners = points[triangles]
    return float(
        np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum() / 6.0
    )


@pytest.mark.parametrize(("rings", "segments"), qual.LADDER)
def test_every_ladder_sphere_is_closed_and_wound_outward(rings: int, segments: int) -> None:
    points, triangles = qual.uv_sphere(qual.SPHERE_RADIUS_M, rings, segments)

    exact = 4.0 / 3.0 * math.pi * qual.SPHERE_RADIUS_M**3
    assert 0.85 * exact < _signed_volume(points, triangles) <= exact
    edges = np.sort(
        np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1
    )
    _, uses = np.unique(edges, axis=0, return_counts=True)
    assert set(uses.tolist()) == {2}


def test_a_sphere_that_cannot_be_cut_on_both_planes_is_refused() -> None:
    with pytest.raises(ValueError):
        qual.uv_sphere(qual.SPHERE_RADIUS_M, 7, 16)
    with pytest.raises(ValueError):
        qual.uv_sphere(qual.SPHERE_RADIUS_M, 8, 18)


def test_the_quarter_is_exactly_a_quarter_on_the_positive_side() -> None:
    points, triangles, tags = qual.sphere_mesh(qual.REFERENCE_LEVEL, split=False)

    kept_points, kept_triangles, _ = qual.keep_side(points, triangles, tags, ["x0", "y0"])

    assert 4 * len(kept_triangles) == len(triangles)
    assert kept_points[:, 0].min() >= -1e-12
    assert kept_points[:, 1].min() >= -1e-12


def test_rotation_is_proper_and_leaves_its_axis_alone() -> None:
    axis = np.asarray([0.3, 1.0, 0.2])
    rotation = qual.rotation_matrix(axis, 70.0)

    assert rotation @ rotation.T == pytest.approx(np.eye(3), abs=1e-12)
    assert np.linalg.det(rotation) == pytest.approx(1.0, abs=1e-12)
    assert rotation @ axis == pytest.approx(axis, abs=1e-12)


def _radial_derivative(pressure, radius: float, step: float = 1e-6) -> complex:
    return (pressure(radius + step) - pressure(radius - step)) / (2.0 * step)


@pytest.mark.parametrize("frequency", [100.0, 700.0, 1500.0])
def test_the_pulsating_reference_meets_its_boundary_condition(frequency: float) -> None:
    # e^{-iwt}: rho dv/dt = -grad p, so a unit outward acceleration needs
    # dp/dr = -rho on the surface; a wrong sign reads +rho. The time
    # convention itself is caught by the outgoing-wave test below.
    a = qual.SPHERE_RADIUS_M

    slope = _radial_derivative(lambda r: qual.pulsating_sphere(a, frequency, r), a)

    assert slope == pytest.approx(-qual.AIR_DENSITY, rel=1e-5)


@pytest.mark.parametrize("cos_theta", [1.0, 0.5, -0.8])
def test_the_oscillating_reference_meets_its_boundary_condition(cos_theta: float) -> None:
    pytest.importorskip("scipy")
    a = qual.SPHERE_RADIUS_M

    slope = _radial_derivative(
        lambda r: complex(qual.oscillating_sphere(a, 700.0, r, np.asarray(cos_theta))), a
    )

    assert slope == pytest.approx(-qual.AIR_DENSITY * cos_theta, rel=1e-5)


def test_both_references_radiate_outgoing_waves_for_e_minus_i_omega_t() -> None:
    pytest.importorskip("scipy")
    a, frequency = qual.SPHERE_RADIUS_M, 3000.0
    k = 2.0 * math.pi * frequency / qual.SOUND_SPEED
    near, far = 10.0, 10.5

    def phase_advance(pressure) -> float:
        return float(np.angle((pressure(far) * far) / (pressure(near) * near)))

    expected = float(np.angle(np.exp(1j * k * (far - near))))
    assert phase_advance(lambda r: qual.pulsating_sphere(a, frequency, r)) == pytest.approx(expected, abs=1e-9)
    oscillating = phase_advance(lambda r: complex(qual.oscillating_sphere(a, frequency, r, np.asarray(1.0))))
    assert oscillating == pytest.approx(expected, abs=2e-3)


def test_the_oscillating_reference_reduces_to_the_incompressible_dipole() -> None:
    pytest.importorskip("scipy")
    a, r, cos_theta = qual.SPHERE_RADIUS_M, 0.2, 0.6

    low = complex(qual.oscillating_sphere(a, 1.0, r, np.asarray(cos_theta)))

    assert low == pytest.approx(qual.AIR_DENSITY * a**3 * cos_theta / (2.0 * r**2), rel=1e-3)


def test_the_error_measure_sees_a_sign_flip_and_nothing_else() -> None:
    reference = np.asarray([[1.0 + 2.0j, -0.5j], [3.0, 4.0 - 1.0j]])

    assert qual.relative_error(reference, reference) == pytest.approx([0.0, 0.0])
    assert qual.relative_error(-reference, reference) == pytest.approx([2.0, 2.0])
    halved = reference.copy()
    halved[1] *= 0.5
    assert qual.relative_error(halved, reference) == pytest.approx([0.0, 0.5])


def test_a_row_without_a_tolerance_is_reported_not_judged() -> None:
    reported = qual.Row("defect", "metal", "full", "complex", [1.04])
    passing = qual.Row("same mesh", "metal", "beat-cpu", "complex", [0.01], tolerance=0.05)
    failing = qual.Row("same mesh", "metal", "beat-cpu", "complex", [0.06], tolerance=0.05)

    assert reported.passed is None
    assert passing.passed is True
    assert failing.passed is False


def test_a_non_vacuity_row_fails_when_the_fixture_stops_distinguishing() -> None:
    # A lower bound is judged at the least frequency: a fixture that turned
    # symmetric at one frequency would let the rows it guards pass vacuously.
    distinct = qual.Row("asymmetry", "metal", "mirror", "complex", [0.2, 1.9], minimum=0.01)
    vacuous = qual.Row("asymmetry", "metal", "mirror", "complex", [0.0005, 1.9], minimum=0.01)

    assert distinct.passed is True
    assert vacuous.passed is False


def test_a_host_without_metal_is_refused_before_anything_is_compared(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("the qualification ran with one engine and nothing to compare")

    monkeypatch.setattr(qual, "available_engines", lambda: {"metal": "unavailable", "beat-cpu": "available"})
    monkeypatch.setattr(qual, "run", must_not_run)

    assert qual.main([]) == 2


def test_the_markdown_record_marks_each_verdict_and_carries_no_local_path(tmp_path: Path) -> None:
    levels = range(len(qual.LADDER))
    result = {
        "rows": [
            qual.Row("defect row", "metal", "full", "complex", [1.04]),
            qual.Row("passing row", "metal", "beat-cpu", "complex", [0.01], tolerance=0.05),
            qual.Row("failing row", "beat-cpu", "metal", "complex", [0.06], tolerance=0.05),
            qual.Row("bounded row", "metal", "mirror", "complex", [0.2, 1.9], minimum=0.01),
        ],
        "level_errors": {
            (kind, "metal", level): np.asarray([0.01]) for kind in ("pulsating", "oscillating") for level in levels
        },
        "same_mesh_tolerance": {level: 0.05 for level in levels},
    }
    path = tmp_path / "report.md"

    qual.write_markdown(path, result, {"metal": "available"}, {"generated_at": "now", "platform": "test"})

    text = path.read_text(encoding="utf-8")
    rows = {line.split(" | ")[0].lstrip("| "): line for line in text.splitlines() if line.startswith("| ")}
    assert rows["defect row"].endswith("| 1.04e+00 |  |  |")
    assert rows["passing row"].endswith("| pass |")
    assert rows["failing row"].endswith("| **FAIL** |")
    assert rows["bounded row"].endswith("| ≥ 1.00e-02 (least 2.00e-01) | pass |")
    assert str(tmp_path) not in text


def test_the_horn_is_symmetric_on_both_planes_until_it_is_skewed() -> None:
    from scripts import imported_ingest_fixtures as fixtures

    def mirrored(points: np.ndarray, axis: int) -> bool:
        flat = np.round(points.reshape(-1, 3), 9)
        image = flat.copy()
        image[:, axis] *= -1.0
        return {tuple(row) for row in flat} == {tuple(row) for row in image}

    round_inner, _ = fixtures.horn_points()
    skewed_inner, _ = fixtures.horn_points(skew_mm=12.0)

    assert mirrored(round_inner, 0) and mirrored(round_inner, 1)
    assert mirrored(skewed_inner, 1) and not mirrored(skewed_inner, 0)


def test_the_return_contract_binds_to_the_throat_disc_that_faces_the_bore(tmp_path: Path) -> None:
    # The source a real return tags is the planar throat disc in the throat
    # plane (the add-in's `_throat_faces`, WG's `geometry_candidate_matches`).
    # A rounded-cap membrane leaves only the plug's rear planar -- 4 mm behind
    # the throat and facing away from the bore -- and a contract bound to that
    # face turned WG's quarter inside out.
    pytest.importorskip("gmsh")
    pytest.importorskip("hornlab_mesher")
    from scripts import imported_ingest_fixtures as fixtures

    bundle = fixtures.linked_return(tmp_path, "round")

    manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    contract = manifest["instances"][0]["source_contract"]
    assert contract["throat_z_mm"] == pytest.approx(0.0, abs=1e-6)
    assert contract["expected_disc_area_mm2"] == pytest.approx(math.pi * 10.0**2, rel=0.01)


@pytest.mark.skipif(
    os.environ.get("WG2_QUALIFY_IMPORTED") != "1",
    reason="needs Metal and a provisioned BEAT CPU runtime; set WG2_QUALIFY_IMPORTED=1",
)
def test_the_real_qualification_passes_on_this_host(tmp_path: Path) -> None:
    code = qual.main(["--json", str(tmp_path / "result.json"), "--markdown", str(tmp_path / "result.md")])

    assert code == 0
