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

import dataclasses
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

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


def test_bempp_joins_the_qualification_only_where_it_assembles_on_opencl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # numba is never qualified: a host that would assemble on it is reported,
    # and every BEMPP row is simply absent there.
    from server.engines import registry
    from server.solver import bempp

    detected = [SimpleNamespace(name=name, available=True, reason="ok") for name in ("metal", "beat-cpu", "bempp")]
    monkeypatch.setattr(registry, "detect_engines", lambda: detected)

    monkeypatch.setattr(bempp, "bempp_status", lambda: {"assembly_backend": "numba"})
    assert qual.available_engines()["bempp"] == "not qualified here: assembly backend numba is not OpenCL"
    monkeypatch.setattr(bempp, "bempp_status", lambda: {"assembly_backend": "opencl"})
    assert qual.available_engines()["bempp"] == "available"


def test_every_record_level_fixture_carries_the_open_edge_evidence_bempp_needs() -> None:
    # Closed spheres, and cuts whose rims lie on their mirror planes: none has a
    # free rim, and a record that did not say so would be refused by BEMPP.
    from server.solver.bempp_imported import imported_bempp_preflight

    points, triangles, tags = qual.sphere_mesh(0, split=True)
    whole = qual.record_for(qual.gmsh22(points, triangles, tags), qual.HEMISPHERE_TAGS)
    cut_points, cut_triangles, cut_tags = qual.keep_side(points, triangles, tags, ["x0", "y0"])
    quarter = qual.record_for(
        qual.gmsh22(cut_points, cut_triangles, cut_tags), qual.HEMISPHERE_TAGS, domain_planes=("x0", "y0")
    )

    assert imported_bempp_preflight(whole) is None
    assert imported_bempp_preflight(quarter) is None


def test_bempp_meets_the_analytic_sphere_on_an_opencl_device() -> None:
    # The BEMPP arm of the qualification, where it can run at all. Checked
    # inside the test rather than at collection: the OpenCL probe is slow, and
    # a host without the opt-in must not pay it.
    if os.environ.get("WG2_QUALIFY_IMPORTED") != "1":
        pytest.skip("real BEMPP solve; set WG2_QUALIFY_IMPORTED=1 on a host with an OpenCL device")
    from server.solver.bempp import bempp_status

    if bempp_status().get("assembly_backend") != "opencl":
        pytest.skip("BEMPP is qualified on an OpenCL device only; numba is never used")

    points, triangles, tags = qual.sphere_mesh(qual.REFERENCE_LEVEL, split=True)
    record = qual.record_for(qual.gmsh22(points, triangles, tags), qual.HEMISPHERE_TAGS)
    solved = qual.solve("bempp", record, qual.ONE_CHANNEL)
    errors = qual.relative_error(solved.observations(), qual.analytic_observations(solved, "pulsating"))

    # The fixed ceiling every engine is judged against on this sphere: BEMPP
    # solves the same complex_k formulation as Metal on the same mesh.
    assert float(np.max(errors)) <= qual.ANALYTIC_CEILINGS["pulsating"][qual.REFERENCE_LEVEL]


@pytest.mark.skipif(
    os.environ.get("WG2_QUALIFY_IMPORTED") != "1",
    reason="needs Metal and a provisioned BEAT CPU runtime; set WG2_QUALIFY_IMPORTED=1",
)
def test_the_real_qualification_passes_on_this_host(tmp_path: Path) -> None:
    code = qual.main(["--json", str(tmp_path / "result.json"), "--markdown", str(tmp_path / "result.md")])

    assert code == 0


# ---------------------------------------------------------------- the real run(), synthetic engines
#
# The real run needs Metal and a provisioned BEAT CPU runtime, so its verdicts
# were never seen to fail: a BEAT that answered minus Metal, or 30 % high,
# passed every row, because the same-mesh tolerance was built from the
# engines' own analytic errors -- which nothing judged. These drive the real
# ``run()`` with a synthetic linear solver in place of both engines, and a
# known difference between them.


def _mesh(text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lines = text.split("\n")
    start = lines.index("$Nodes")
    count = int(lines[start + 1])
    points = np.asarray([line.split()[1:4] for line in lines[start + 2 : start + 2 + count]], dtype=float)
    start = lines.index("$Elements")
    count = int(lines[start + 1])
    rows = np.asarray([line.split() for line in lines[start + 2 : start + 2 + count]], dtype=int)
    return points, rows[:, 5:8] - 1, rows[:, 3]


def _radiation(motion: str, frequency: float) -> complex:
    """The factor that makes a continuous layer on the 0.1 m sphere radiate the analytic field.

    A uniform layer radiates ``a^2 j0(ka) e^{ikr}/r`` and a ``cos(theta)``
    layer ``ik a^2 j1(ka) h1(kr) cos(theta)`` (the addition theorem keeps one
    term of each), so dividing the analytic answers by those leaves one factor
    per frequency. On the sphere only the quadrature then errs: second order in
    the element size, as a boundary-element solve does.
    """

    from scipy.special import spherical_jn, spherical_yn

    k = 2.0 * math.pi * frequency / qual.SOUND_SPEED
    ka = k * qual.SPHERE_RADIUS_M
    if motion == "normal":
        return qual.AIR_DENSITY * np.exp(-1j * ka) / ((1.0 - 1j * ka) * spherical_jn(0, ka))
    slope = spherical_jn(1, ka, derivative=True) + 1j * spherical_yn(1, ka, derivative=True)
    return 1j * qual.AIR_DENSITY / (k**2 * qual.SPHERE_RADIUS_M**2 * spherical_jn(1, ka) * slope)


_BASES: dict[str, dict[str, np.ndarray]] = {}


def _synthetic_pressure(record: dict, channels: list[dict], frequencies: tuple[float, ...]) -> dict[str, np.ndarray]:
    """Per channel, a source sum over vertex quadrature points: (frequency, plane, angle)."""

    key = json.dumps(
        [record["mesh_content_sha256"], record["anchor"]["throat_frame"], record["symmetry"]["domain_planes"],
         record["source_tags"], channels, list(frequencies)],
        sort_keys=True,
    )
    if key in _BASES:
        return _BASES[key]
    points, triangles, tags = _mesh(record["_execution_msh_text"])
    frame = record["anchor"]["throat_frame"]
    axis, u, v, origin = (np.asarray(frame[name], dtype=float) for name in ("axis", "u", "v", "origin_m"))
    corners = points[triangles]
    doubled = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    areas = 0.5 * np.linalg.norm(doubled, axis=1)
    normals = doubled / (2.0 * areas[:, None])
    # A third of each triangle at each of its corners: the corners lie on the
    # sphere, where a centroid would sit inside it.
    nodes = corners.reshape(-1, 3)
    weights = np.repeat(areas / 3.0, 3)
    normals, tags = np.repeat(normals, 3, axis=0), np.repeat(tags, 3)
    # A reduced domain is solved with its mirror images, as the engines solve
    # it. The record fixtures cut in the identity frame.
    for plane in record["symmetry"]["domain_planes"]:
        flip = np.ones(3)
        flip[{"x0": 0, "y0": 1}[plane]] = -1.0
        nodes, normals = np.concatenate([nodes, nodes * flip]), np.concatenate([normals, normals * flip])
        weights, tags = np.concatenate([weights, weights]), np.concatenate([tags, tags])
    theta = np.radians(np.linspace(-180.0, 180.0, 73))[:, None]
    sides = (u, v, (u + v) / math.sqrt(2.0))
    targets = np.stack([origin + qual.OBSERVATION_DISTANCE_M * (np.cos(theta) * axis + np.sin(theta) * side) for side in sides])
    pressure: dict[str, np.ndarray] = {}
    for channel in channels:
        driven = np.isin(tags, [record["source_tags"][source] for source in channel["source_ids"]])
        motion = channel.get("motion", "normal")
        strength = weights[driven] * (1.0 if motion == "normal" else normals[driven] @ axis)
        distance = np.linalg.norm(targets[:, :, None, :] - nodes[driven][None, None], axis=-1)
        pressure[str(channel["id"])] = np.stack(
            [
                _radiation(motion, frequency)
                * np.sum(strength * np.exp(2j * math.pi * frequency / qual.SOUND_SPEED * distance) / (4.0 * math.pi * distance), axis=-1)
                for frequency in frequencies
            ]
        )
    _BASES[key] = pressure
    return pressure


def _synthetic_solve(engine: str, record: dict, channels: list[dict], frequencies: tuple[float, ...] = qual.FREQUENCIES_HZ) -> qual.Solved:
    ids = [str(channel["id"]) for channel in channels]
    pressure = _synthetic_pressure(record, list(channels), tuple(frequencies))
    return qual.Solved(
        engine=engine,
        channel_ids=ids,
        frequencies_hz=np.asarray(frequencies, dtype=float),
        angles_deg=np.linspace(-180.0, 180.0, 73),
        planes=["horizontal", "vertical", "diagonal"],
        pressure={name: pressure[name].copy() for name in ids},
        sphere={name: None for name in ids},
        sphere_theta_deg=None,
        sphere_phi_deg=None,
        wall_seconds=0.0,
        metadata={"solver_engine": {"engine": engine}},
        impedance={name: None for name in ids},
    )


def _swap_twins(pressure: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    if set(pressure) != {"hf-left", "hf-right"}:
        return pressure
    return {"hf-left": pressure["hf-right"], "hf-right": pressure["hf-left"]}


#: How the synthetic BEAT differs from the synthetic Metal ("both": the two
#: engines alike, and both wrong).
VARIANTS = {
    "agree": ("beat-cpu", lambda pressure: pressure),
    "beat is minus metal": ("beat-cpu", lambda pressure: {name: -value for name, value in pressure.items()}),
    "beat is 30 % high": ("beat-cpu", lambda pressure: {name: 1.3 * value for name, value in pressure.items()}),
    "beat swaps the repeated-HF identities": ("beat-cpu", _swap_twins),
    "both are 30 % high": ("both", lambda pressure: {name: 1.3 * value for name, value in pressure.items()}),
}
ENGINES = ["metal", "beat-cpu"]
_RUNS: dict[str, dict] = {}


def _synthetic_run(variant: str) -> dict:
    pytest.importorskip("scipy")
    if variant not in _RUNS:
        changed, change = VARIANTS[variant]

        def solve(engine: str, record: dict, channels: list[dict], **kwargs: object) -> qual.Solved:
            solved = _synthetic_solve(engine, record, channels, **kwargs)
            if changed in (engine, "both"):
                solved.pressure = change(solved.pressure)
            # A source-average pressure per channel, from the answer itself, so
            # the impedance row is emitted and compared as well.
            solved.impedance = {name: value.mean(axis=(1, 2)) for name, value in solved.pressure.items()}
            return solved

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(qual, "solve", solve)
            _RUNS[variant] = qual.run(ENGINES, report=lambda _line: None)
    return _RUNS[variant]


def _cross_engine(row: qual.Row) -> bool:
    return {row.engine, row.compared_with} == set(ENGINES)


def test_engines_that_agree_and_converge_pass_every_analytic_order_and_cross_engine_row() -> None:
    """The positive control: the judging below fails what it should, not everything."""

    rows = _synthetic_run("agree")["rows"]

    assert sum(row.compared_with == "analytic" for row in rows) >= 2 * 3 * len(qual.LADDER)
    assert sum(row.compared_with == "refinement" for row in rows) == 2 * len(ENGINES)
    assert sum(_cross_engine(row) for row in rows) == 10
    # Every row is judged and passes, except the reduced domains against the
    # whole: the synthetic solver sums quadrature points, and a mirrored half
    # splits each quad along the other diagonal. The engines do not.
    failing = [
        (row.fixture, row.engine, row.compared_with, row.worst, row.tolerance, row.minimum)
        for row in rows
        if row.passed is not True and not row.fixture.startswith(("x0 return", "x0+y0 return"))
    ]
    assert not failing, failing


def _ladder_rows(rows: list[qual.Row], engine: str) -> list[qual.Row]:
    return [row for row in rows if row.engine == engine and row.compared_with == "analytic" and " sphere L" in row.fixture]


@pytest.mark.parametrize("variant", ("beat is minus metal", "beat is 30 % high"))
def test_a_beat_that_disagrees_with_metal_fails_the_same_mesh_rows(variant: str) -> None:
    rows = _synthetic_run(variant)["rows"]

    # Every comparison of the two engines, not just one of them.
    cross = [row for row in rows if _cross_engine(row)]
    assert len(cross) == 10
    assert all(row.passed is False for row in cross), [row.fixture for row in cross if row.passed is not False]
    # And every ladder row of the wrong engine, at every level; Metal's pass.
    ladder = _ladder_rows(rows, "beat-cpu")
    assert len(ladder) == 3 * len(qual.LADDER)
    assert all(row.passed is False for row in ladder), [row.fixture for row in ladder if row.passed is not False]
    assert all(row.passed is True for row in _ladder_rows(rows, "metal"))


def test_engines_wrong_alike_fail_their_analytic_and_order_rows() -> None:
    """Two engines that agree prove nothing about either; only the fixed ceilings can say so."""

    rows = _synthetic_run("both are 30 % high")["rows"]
    failed = [row for row in rows if row.passed is False]

    for engine in ENGINES:
        ladder = _ladder_rows(rows, engine)
        assert len(ladder) == 3 * len(qual.LADDER)
        assert all(row.passed is False for row in ladder), [row.fixture for row in ladder if row.passed is not False]
        assert any(row.engine == engine and row.compared_with == "refinement" for row in failed), engine
        # Moving the body leaves its error alone, so only the ceiling can fail
        # the moved copy against the analytic answer.
        assert any(
            row.engine == engine and row.fixture == "rotated + translated oscillating sphere"
            and row.compared_with == "analytic"
            for row in failed
        ), engine
    assert not any(_cross_engine(row) for row in failed)


def test_a_swap_of_the_repeated_hf_identities_fails() -> None:
    """The two-channel sum cannot see it; an instance solved on its own can."""

    failed = [row for row in _synthetic_run("beat swaps the repeated-HF identities")["rows"] if row.passed is False]

    assert any(row.fixture.startswith("repeated HF") and row.engine == "beat-cpu" for row in failed)
    assert not any(row.fixture.startswith("repeated HF") and row.engine == "metal" for row in failed)


def test_every_engine_pair_is_compared_on_every_same_mesh_fixture() -> None:
    rows = _synthetic_run("agree")["rows"]

    same_mesh = [row for row in rows if row.fixture.startswith("same mesh") and (row.engine, row.compared_with) == tuple(ENGINES)]
    # Normal and axial motion; one channel, then two channels and their sum.
    assert len(same_mesh) == 8
    assert any(row.fixture == "rotated + translated off-axis cap" and _cross_engine(row) for row in rows)


def test_a_run_with_one_engine_is_refused_before_anything_is_solved(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_solve(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a one-engine run solved something it could compare with nothing")

    monkeypatch.setattr(qual, "solve", must_not_solve)

    with pytest.raises(ValueError, match="two engines"):
        qual.run(["metal"], report=lambda _line: None)


def test_observations_are_complex_per_channel_and_for_the_channel_sum() -> None:
    """A magnitude-only comparison cannot see a sign flip."""

    rng = np.random.default_rng(7)

    def field(*shape: int) -> np.ndarray:
        return rng.normal(size=shape) + 1j * rng.normal(size=shape)

    top, bottom, top_sphere, bottom_sphere = field(2, 3, 5), field(2, 3, 5), field(2, 4), field(2, 4)
    solved = qual.Solved(
        engine="metal", channel_ids=["top", "bottom"], frequencies_hz=np.asarray([100.0, 200.0]),
        angles_deg=np.linspace(-180.0, 180.0, 5), planes=["horizontal", "vertical", "diagonal"],
        pressure={"top": top, "bottom": bottom}, sphere={"top": top_sphere, "bottom": bottom_sphere},
        sphere_theta_deg=np.zeros(4), sphere_phi_deg=np.zeros(4), wall_seconds=0.0,
    )

    assert np.array_equal(solved.observations("top"), np.concatenate([top.reshape(2, -1), top_sphere], axis=1))
    assert np.array_equal(
        solved.observations(), np.concatenate([(top + bottom).reshape(2, -1), top_sphere + bottom_sphere], axis=1)
    )
    flipped = dataclasses.replace(
        solved, pressure={"top": -top, "bottom": -bottom}, sphere={"top": -top_sphere, "bottom": -bottom_sphere}
    )
    assert qual.relative_error(flipped.observations(), solved.observations()) == pytest.approx([2.0, 2.0])
    assert qual.relative_error(flipped.observations("top"), solved.observations("top")) == pytest.approx([2.0, 2.0])


class _Adapter:
    """An engine adapter that answers with *answer*'s engine, channels and frequencies."""

    def __init__(self, **answer: object) -> None:
        self.answer = answer

    async def run(self, request: object, **_callbacks: object) -> object:
        from server.solver.base import EngineRunResult
        from server.solver.combine import serialize_channel_bases

        frequencies = np.asarray(self.answer.get("frequencies", request.options.frequencies_hz), dtype=float)
        channels = self.answer.get("channels", [channel.id for channel in request.geometry.drive_channels])
        angles = np.linspace(-180.0, 180.0, 73)
        bases = {
            name: SimpleNamespace(
                frequencies_hz=frequencies, observation_angles_deg=angles,
                observation_planes=["horizontal", "vertical", "diagonal"],
                pressure_complex=np.ones((len(frequencies), 3, len(angles)), dtype=complex),
                sphere_pressure_complex=None,
            )
            for name in channels
        }
        metadata = {} if self.answer.get("engine") is None else {"solver_engine": {"engine": self.answer["engine"]}}
        return EngineRunResult(results={"metadata": metadata, "channels": {}}, channel_bases=serialize_channel_bases(bases))


@pytest.mark.parametrize(
    ("answer", "message"),
    (
        pytest.param({"engine": "metal"}, "'metal'", id="another-engine"),
        pytest.param({"engine": None}, "None", id="no-engine"),
        pytest.param({"engine": "beat-cpu", "channels": ["both", "extra"]}, "channels", id="another-channel-set"),
        pytest.param({"engine": "beat-cpu", "frequencies": qual.FREQUENCIES_HZ[:1]}, "frequencies", id="fewer-frequencies"),
    ),
)
def test_solve_refuses_an_answer_from_another_engine_or_to_another_question(
    monkeypatch: pytest.MonkeyPatch, answer: dict, message: str
) -> None:
    points, triangles, tags = qual.sphere_mesh(0, split=True)
    record = qual.record_for(qual.gmsh22(points, triangles, tags), qual.HEMISPHERE_TAGS)

    monkeypatch.setattr(qual, "engine_adapter", lambda _name: _Adapter(engine="beat-cpu"))
    assert qual.solve("beat-cpu", record, qual.ONE_CHANNEL).channel_ids == ["both"]

    monkeypatch.setattr(qual, "engine_adapter", lambda _name: _Adapter(**answer))
    with pytest.raises(qual.EngineAnswerMismatch, match=message):
        qual.solve("beat-cpu", record, qual.ONE_CHANNEL)


def test_a_failing_row_of_either_kind_is_reported_and_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        qual.Row("off-axis cap: horizontal differs from vertical", "metal", "vertical cut", "complex", [0.0], minimum=0.01),
        qual.Row("same mesh", "metal", "beat-cpu", "complex", [0.3], tolerance=0.05),
    ]
    monkeypatch.setattr(qual, "available_engines", lambda: {"metal": "available", "beat-cpu": "available"})
    monkeypatch.setattr(qual, "environment_facts", lambda: {"generated_at": "now"})
    monkeypatch.setattr(qual, "run", lambda _engines, report=print: {"rows": rows, "timings": {}})

    assert qual.main(["--skip-ingest"]) == 1

    printed = capsys.readouterr().out
    assert "FAIL: off-axis cap: horizontal differs from vertical (metal vs vertical cut): least 0.000e+00 < 1.000e-02" in printed
    assert "FAIL: same mesh (metal vs beat-cpu): 3.000e-01 > 5.000e-02" in printed


RECORD = Path(__file__).resolve().parents[2] / "docs" / "validation" / "2026-09" / "imported-same-mesh-qualification.json"


def test_the_landed_record_passes_the_fixed_bounds_chosen_from_it() -> None:
    """The ceilings come from this run's Metal ladder; the whole record must meet them.

    Its numbers are not re-measured here -- that needs Metal and BEAT-CPU --
    only re-judged: every row whose bound this judging changed.
    """

    from scripts import qualify_ingest_level as ingest

    record = json.loads(RECORD.read_text(encoding="utf-8"))
    worst = {}
    for key, errors in record["discretisation_errors"].items():
        engine, kind, level = key.split("/")
        worst[(engine, kind, int(level.removeprefix("L")))] = max(errors)
    assert sorted({engine for engine, _kind, _level in worst}) == ["beat-cpu", "metal"]
    for (engine, kind, level), value in worst.items():
        assert value <= qual.ANALYTIC_CEILINGS[kind][level], (engine, kind, level, value)
    for engine in ("metal", "beat-cpu"):
        for kind in qual.ANALYTIC_CEILINGS:
            order = qual.observed_order([worst[(engine, kind, level)] for level in range(len(qual.LADDER))])
            assert order >= qual.MINIMUM_ORDER, (engine, kind, order)

    rows = record["rows"]
    spheres = [row for row in rows if row["compared_with"] == "beat-cpu" and "horn" not in row["fixture"]]
    assert len(spheres) == 10
    for row in spheres:
        assert row["worst"] <= qual.SAME_MESH_TOLERANCE[qual.REFERENCE_LEVEL], row["fixture"]
    for row in rows:
        if row["fixture"] == "rotated + translated oscillating sphere" and row["compared_with"] == "analytic":
            assert row["worst"] <= qual.ANALYTIC_CEILINGS["oscillating"][qual.REFERENCE_LEVEL]
    horn = [row for row in rows if row["fixture"].startswith(("same mesh: horn", "horn: quarter return"))]
    assert len(horn) == 4
    for row in horn:
        assert row["worst"] <= ingest.HORN_TOLERANCE, (row["fixture"], row["engine"])
    ladder = [row for row in rows if row["fixture"] == "horn return, reference vs fine density"]
    assert len(ladder) == 2
    for row in ladder:
        assert row["worst"] <= ingest.HORN_LADDER_CEILING, row["engine"]
