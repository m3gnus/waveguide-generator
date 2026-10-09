import base64

import numpy as np
import pytest
from beat_engine.beat_contract import validate_solve_request

from scripts.beat_conformance import reference_lem_sphere as ref
from scripts.beat_conformance.reference_support import FrequencyResult, Quantity, SolveOutcome
from scripts.beat_conformance.reference_support import POSITIVE_TIME, NEGATIVE_TIME
from scripts.beat_conformance.lem_fixture import (
    COMPONENT_ID,
    PORT_ID,
    DRIVER_PARAMETERS,
    REFERENCE_VOLTAGE_V,
    build_lem_reference_request,
)


@pytest.mark.parametrize("convention", [POSITIVE_TIME, NEGATIVE_TIME])
@pytest.mark.parametrize("frequency", ref.FREQUENCIES_HZ)
def test_exact_field_boundary_load_and_far_field(frequency, convention):
    """Check Euler sign, Gauss-integrated opposing force, and outgoing far field."""
    a, rho, c = ref.RADIUS_M, ref.DENSITY, ref.SOUND_SPEED
    w = 2 * np.pi * frequency
    k = w / c
    step = a * 1e-5
    derivative = (
        ref.analytic_pressure(frequency, a + step, 0.73, convention)
        - ref.analytic_pressure(frequency, a - step, 0.73, convention)
    ) / (2 * step)
    s = 1 if convention == POSITIVE_TIME else -1
    np.testing.assert_allclose(derivative, -s * 1j * w * rho * 0.73, rtol=1e-8)
    mu, weight = np.polynomial.legendre.leggauss(32)
    # dS=2*pi*a^2*d(cos(theta)); p*cos(theta) is the +z load.
    integrated = (
        2 * np.pi * a * a * np.dot(weight, ref.analytic_pressure(frequency, a, mu, convention) * mu)
    )
    np.testing.assert_allclose(
        integrated, ref.analytic_radiation_impedance(frequency, convention), rtol=1e-13
    )
    load_hankel = (
        -s
        * 1j
        * rho
        * c
        * 4
        * np.pi
        * a
        * a
        / 3
        * ref.hankel1(k * a, convention)
        / ref.hankel1(k * a, convention, derivative=True)
    )
    np.testing.assert_allclose(integrated, load_hankel, rtol=1e-13)
    r = 1e5
    far = (
        s
        * 1j
        * rho
        * c
        * np.exp(-s * 1j * k * r)
        / (k * r * ref.hankel1(k * a, convention, derivative=True))
    )
    np.testing.assert_allclose(ref.analytic_pressure(frequency, r, 1, convention), far, rtol=2e-5)
    assert ref.analytic_radiation_impedance(frequency, convention).real > 0
    assert abs(ref.analytic_pressure(frequency, 1, 0, convention)) == 0


def test_compact_sphere_added_mass_and_dipole_limit():
    f = 0.001
    w = 2 * np.pi * f
    mass = 2 * np.pi * ref.DENSITY * ref.RADIUS_M**3 / 3  # half displaced fluid mass
    np.testing.assert_allclose(
        ref.analytic_radiation_impedance(f, POSITIVE_TIME).imag, w * mass, rtol=1e-10
    )
    r = 1e8
    compact_far = (
        -ref.DENSITY
        * w
        * w
        * ref.RADIUS_M**3
        / (2 * ref.SOUND_SPEED * r)
        * np.exp(-1j * w * r / ref.SOUND_SPEED)
    )
    np.testing.assert_allclose(
        ref.analytic_pressure(f, r, 1, POSITIVE_TIME), compact_far, rtol=0.001
    )


@pytest.mark.parametrize("frequency", ref.FREQUENCIES_HZ)
def test_driver_circuit_and_power_balance_and_phasor_conjugation(frequency):
    u, current, zin = ref.analytic_driver(frequency, POSITIVE_TIME)
    d, w = DRIVER_PARAMETERS, 2 * np.pi * frequency
    ze = d["re_ohm"] + 1j * w * d["le_h"]
    zm = d["rms_n_s_per_m"] + 1j * w * d["mmd_kg"] + 1 / (1j * w * d["cms_m_per_n"])
    zr = ref.analytic_radiation_impedance(frequency, POSITIVE_TIME)
    np.testing.assert_allclose(ze * current + d["bl_n_per_a"] * u, REFERENCE_VOLTAGE_V, rtol=1e-14)
    np.testing.assert_allclose((zm + zr) * u, d["bl_n_per_a"] * current, rtol=1e-14)
    np.testing.assert_allclose(zin, ze + d["bl_n_per_a"] ** 2 / (zm + zr), rtol=1e-14)
    np.testing.assert_allclose(
        (REFERENCE_VOLTAGE_V * np.conj(current)).real,
        d["re_ohm"] * abs(current) ** 2 + (d["rms_n_s_per_m"] + zr.real) * abs(u) ** 2,
        rtol=1e-14,
    )
    negative = ref.analytic_driver(frequency, NEGATIVE_TIME)
    np.testing.assert_allclose(negative, np.conj([u, current, zin]), rtol=1e-14)
    np.testing.assert_allclose(
        ref.analytic_pressure(frequency, [1, 2], [1, -1], NEGATIVE_TIME, velocity=negative[0]),
        ref.analytic_pressure(frequency, [1, 2], [1, -1], POSITIVE_TIME, velocity=u).conj(),
        rtol=1e-14,
    )
    doubled = ref.analytic_driver(frequency, POSITIVE_TIME, voltage_v=2 * REFERENCE_VOLTAGE_V)
    np.testing.assert_allclose(doubled, [2 * u, 2 * current, zin])


def unpack(array):
    return np.frombuffer(base64.b64decode(array["data"]), dtype=array["dtype"]).reshape(
        array["shape"]
    )


@pytest.fixture(scope="module")
def shell():
    pytest.importorskip("gmsh")
    return ref.make_shell_mesh()


def test_shell_topology_and_contract_are_conforming_and_placed(shell):
    fem, bem, topology, info = shell
    p, bp = unpack(fem["points"]), unpack(bem["points"])
    inner, outer, tets = [unpack(b["connectivity"]) for b in fem["cells"]]
    bf = unpack(bem["cells"][0]["connectivity"])
    np.testing.assert_array_equal(
        p[topology["fem_vertex_indices"]], bp[topology["fem_to_bem_vertex_indices"]]
    )
    np.testing.assert_array_equal(p[outer], bp[bf])
    assert topology["fem_face_indices"] == list(range(len(inner), len(inner) + len(outer)))
    assert topology["bem_face_indices"] == list(range(len(outer)))
    assert topology["normal_sign"] == [1] * len(outer)
    for faces, sign, radius in ((inner, -1, ref.RADIUS_M), (outer, 1, ref.OUTER_RADIUS_M)):
        v = p[faces]
        cross = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
        assert np.all(sign * np.einsum("ij,ij->i", cross, v.mean(axis=1)) > 0)
        np.testing.assert_allclose(np.linalg.norm(v, axis=2), radius, atol=1e-12)
    v = p[tets]
    assert np.all(
        np.linalg.det(np.stack([v[:, 1] - v[:, 0], v[:, 2] - v[:, 0], v[:, 3] - v[:, 0]], axis=2))
        > 0
    )
    assert info["minimum_elements_per_wavelength"] >= 6
    assert info["outer_triangles"] <= 2500 and info["tetrahedra"] <= 10000
    assert abs(info["inner_area_m2"] / (4 * np.pi * ref.RADIUS_M**2) - 1) < 0.01
    assert abs(info["outer_area_m2"] / (4 * np.pi * ref.OUTER_RADIUS_M**2) - 1) < 0.01
    assert (
        abs(
            info["tetrahedral_volume_m3"]
            / (4 * np.pi * (ref.OUTER_RADIUS_M**3 - ref.RADIUS_M**3) / 3)
            - 1
        )
        < 0.01
    )
    for convention in (POSITIVE_TIME, NEGATIVE_TIME):
        request = build_lem_reference_request(
            fem,
            bem,
            topology,
            ref.FREQUENCIES_HZ,
            ref.observation_points(),
            centre_m=ref.CENTRE_M,
            phasor_convention=convention,
        )
        validate_solve_request(request)
        assert request["solver_options"]["transducer_reference_voltage_v"] == 2.83
        assert request["compiled_system"]["components"][0]["parameters"]["motion_axis"] == [0, 0, 1]
        assert request["compiled_system"]["components"][0]["boundary_ids"] == ["boundary:diaphragm"]
        assert all(
            m["translation_m"] == ref.CENTRE_M.tolist()
            for m in request["compiled_system"]["meshes"]
        )
        assert all(not m["file"] for m in request["compiled_system"]["meshes"])
        assert request["excitation_port_ids"] == [PORT_ID]
    points = ref.observation_points() - ref.CENTRE_M
    np.testing.assert_allclose(np.linalg.norm(points, axis=1), [1] * 5 + [2] * 5)
    np.testing.assert_allclose(
        points[:, 2] / np.linalg.norm(points, axis=1),
        np.tile(np.cos(np.deg2rad(ref.ANGLES_DEG)), 2),
        atol=1e-15,
    )


@pytest.mark.parametrize(
    "defect", ["missing_face", "duplicate_face", "bad_vertex", "degenerate_tetra"]
)
def test_shell_builder_rejects_invalid_topology(shell, defect):
    fem = shell[0]
    p = unpack(fem["points"]).copy()
    inner, outer, tets = [unpack(b["connectivity"]).copy() for b in fem["cells"]]
    if defect == "missing_face":
        outer = outer[:-1]
    elif defect == "duplicate_face":
        outer = np.concatenate([outer, outer[:1]])
    elif defect == "bad_vertex":
        tets[0, 0] = len(p)
    else:
        tets[0, 0] = tets[0, 1]
    with pytest.raises(ValueError):
        ref.shell_mesh_buffers(p, inner, outer, tets)


def synthetic_outcome(convention, *, u_scale=1, i_scale=1, p_scale=1, node_error=0):
    points = ref.observation_points() - ref.CENTRE_M
    r = np.linalg.norm(points, axis=1)
    mu = points[:, 2] / r
    results = []
    for frequency in ref.FREQUENCIES_HZ:
        u, current, _ = ref.analytic_driver(frequency, convention)
        p = ref.analytic_pressure(frequency, r, mu, convention, velocity=u) * p_scale
        p[np.abs(mu) < 1e-12] += node_error * np.abs(
            ref.analytic_pressure(frequency, r[np.abs(mu) < 1e-12], 1, convention, velocity=u)
        )
        q = [
            Quantity(
                "diaphragm_velocity",
                "diaphragm_velocity",
                "m/s",
                ("excitation", "transducer"),
                np.array([[u * u_scale]]),
                None,
                {"component_ids": [COMPONENT_ID]},
            ),
            Quantity(
                "voice_coil_current",
                "voice_coil_current",
                "A",
                ("excitation", "transducer"),
                np.array([[current * i_scale]]),
                None,
                {"component_ids": [COMPONENT_ID]},
            ),
            Quantity(
                "pressure",
                "exterior_pressure",
                "Pa",
                ("excitation", "observation"),
                p[None, :],
                None,
                {},
            ),
        ]
        results.append(
            FrequencyResult(
                frequency,
                (PORT_ID,),
                tuple(q),
                {
                    "phasor_convention": convention,
                    "precision": "float64",
                    "bem_backend": "cpu",
                    "symmetry": "off",
                    "transducer_reference_voltage_v": REFERENCE_VOLTAGE_V,
                },
            )
        )
    return SolveOutcome(
        "completed",
        results=results,
        engine_runs=[
            {
                "engine": {
                    "repository_revision": ref.EXPECTED_BEAT_REVISION,
                    "repository_dirty": False,
                }
            }
        ],
    )


@pytest.mark.parametrize("convention", [POSITIVE_TIME, NEGATIVE_TIME])
def test_exact_scoring_preserves_both_spl_labels(convention):
    score = ref.score_outcome(synthetic_outcome(convention), convention)
    assert score["passed"]
    for result in score["frequencies"]:
        spl = result["spl_1m_on_axis"]
        assert spl["client_amplitude_convention"] == "rms"
        assert spl["voltage_label"] == "2.83 V"
        np.testing.assert_allclose(
            spl["db_if_input_rms_abs_p"] - spl["db_if_input_peak_abs_p_over_sqrt2"],
            20 * np.log10(np.sqrt(2)),
        )
        assert result["exterior_pressure"]["node"]["observation_indices"] == [2, 7]
        assert result["returned_amplitude_metadata"] == {
            "diaphragm_velocity": None,
            "voice_coil_current": None,
            "pressure": None,
        }


@pytest.mark.parametrize("scale", [np.sqrt(2), 1 / np.sqrt(2), 0.5, 2, -1, 1j])
@pytest.mark.parametrize("stage", ["u", "i", "p"])
def test_gates_detect_net_amplitude_convention_and_sign_errors(scale, stage):
    outcome = synthetic_outcome(POSITIVE_TIME, **{f"{stage}_scale": scale})
    assert not ref.score_outcome(outcome, POSITIVE_TIME)["passed"]


def test_node_absolute_gate_and_frequency_completeness():
    assert ref.score_outcome(synthetic_outcome(POSITIVE_TIME, node_error=0.02), POSITIVE_TIME)[
        "passed"
    ]
    assert not ref.score_outcome(synthetic_outcome(POSITIVE_TIME, node_error=0.04), POSITIVE_TIME)[
        "passed"
    ]
    outcome = synthetic_outcome(POSITIVE_TIME)
    outcome.results.pop()
    with pytest.raises(ValueError, match="every declared frequency"):
        ref.score_outcome(outcome, POSITIVE_TIME)


@pytest.mark.parametrize("convention", [POSITIVE_TIME, NEGATIVE_TIME])
def test_half_radiation_resistance_feedback_passes_driver_gates(convention):
    """Sensitivity control: the u/i/p gates cannot qualify feedback resistance."""
    outcome = synthetic_outcome(convention)
    points = ref.observation_points() - ref.CENTRE_M
    radii = np.linalg.norm(points, axis=1)
    mu = points[:, 2] / radii
    d = DRIVER_PARAMETERS
    s = 1 if convention == POSITIVE_TIME else -1
    for result in outcome.results:
        w = 2 * np.pi * result.freq_hz
        ze = d["re_ohm"] + s * 1j * w * d["le_h"]
        zm = d["rms_n_s_per_m"] + s * 1j * w * d["mmd_kg"] + 1 / (s * 1j * w * d["cms_m_per_n"])
        zr = ref.analytic_radiation_impedance(result.freq_hz, convention)
        faulty_load = 0.5 * zr.real + 1j * zr.imag
        u = d["bl_n_per_a"] * REFERENCE_VOLTAGE_V / (ze * (zm + faulty_load) + d["bl_n_per_a"] ** 2)
        current = (REFERENCE_VOLTAGE_V - d["bl_n_per_a"] * u) / ze
        q = {item.id: item for item in result.quantities}
        q["diaphragm_velocity"].values[:] = u
        q["voice_coil_current"].values[:] = current
        q["pressure"].values[0] = ref.analytic_pressure(
            result.freq_hz, radii, mu, convention, velocity=u
        )
        assert np.isclose(faulty_load.real / zr.real, 0.5)
    score = ref.score_outcome(outcome, convention)
    assert score["passed"]
    assert all(
        f["diaphragm_velocity"]["passed"]
        and f["voice_coil_current"]["passed"]
        and f["exterior_pressure"]["passed"]
        for f in score["frequencies"]
    )


@pytest.mark.parametrize("voltage", [None, 1.0, 2.83 / np.sqrt(2), True, 2.8300001])
def test_returned_reference_voltage_is_gated(voltage):
    outcome = synthetic_outcome(POSITIVE_TIME)
    diagnostics = outcome.results[0].diagnostics
    if voltage is None:
        diagnostics.pop("transducer_reference_voltage_v")
    else:
        diagnostics["transducer_reference_voltage_v"] = voltage
    score = ref.score_outcome(outcome, POSITIVE_TIME)
    assert score["passed"] is bool(voltage == 2.8300001)
    assert score["frequencies"][0]["reference_voltage_passed"] is bool(voltage == 2.8300001)


@pytest.mark.parametrize(
    "defect", ["dirty", "missing_dirty", "wrong_revision", "missing_provenance", "second_dirty"]
)
def test_lem_runner_requires_clean_pinned_provenance(tmp_path, monkeypatch, defect):
    class FakeRuntime:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def solve(self, request):
            outcome = synthetic_outcome(request["solver_options"]["phasor_convention"])
            engine = outcome.engine_runs[0]["engine"]
            if defect == "dirty":
                engine["repository_dirty"] = True
            elif defect == "missing_dirty":
                engine.pop("repository_dirty")
            elif defect == "wrong_revision":
                engine["repository_revision"] = "another-revision"
            elif defect == "missing_provenance":
                outcome.engine_runs.clear()
            else:
                outcome.engine_runs.append(
                    {
                        "engine": {
                            "repository_revision": ref.EXPECTED_BEAT_REVISION,
                            "repository_dirty": True,
                        }
                    }
                )
            return outcome

    monkeypatch.setattr(ref, "ReferenceSession", FakeRuntime)
    monkeypatch.setattr(ref, "make_shell_mesh", lambda: ({}, {}, {}, {}))
    monkeypatch.setattr(
        ref,
        "build_lem_reference_request",
        lambda *args, phasor_convention, **kwargs: {
            "solver_options": {"phasor_convention": phasor_convention}
        },
    )
    report = ref.run_reference(julia="unused-portable-julia", out=tmp_path)
    assert not report["passed"]
    assert all(
        not run["provenance_revision_passed"] and not run["passed"] for run in report["runs"]
    )


@pytest.mark.parametrize("defect", [None, "unit", "axes", "shape", "identity", "current_identity"])
def test_runtime_transducer_output_contract(shell, defect):
    from dataclasses import replace
    from scripts.beat_conformance.reference_support import _verify_outputs

    request = build_lem_reference_request(
        *shell[:3],
        ref.FREQUENCIES_HZ,
        ref.observation_points(),
        centre_m=ref.CENTRE_M,
        phasor_convention=POSITIVE_TIME,
    )
    result = synthetic_outcome(POSITIVE_TIME).results[0]
    quantities = list(result.quantities)
    if defect is not None:
        index = 1 if defect == "current_identity" else 0
        changes = {
            "unit": {"unit": "mm/s"},
            "axes": {"axes": ("transducer", "excitation")},
            "shape": {"values": np.zeros((1, 2), dtype=np.complex128)},
            "identity": {"metadata": {"component_ids": ["component:wrong"]}},
            "current_identity": {"metadata": {"component_ids": ["component:wrong"]}},
        }[defect]
        quantities[index] = replace(quantities[index], **changes)
        with pytest.raises(ValueError, match="Transducer"):
            _verify_outputs(replace(result, quantities=tuple(quantities)), request)
    else:
        _verify_outputs(result, request)
