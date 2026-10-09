import copy
import os

import numpy as np
import pytest

from scripts.beat_conformance.reference_sphere import (
    CENTRE_M,
    EXPECTED_BEAT_REVISION,
    FREQUENCIES_HZ,
    RADIUS_M,
    analytic_impedance,
    analytic_pressure,
    make_sphere_mesh,
    observation_sets,
    score_outcome,
    write_report,
)
from scripts.beat_conformance.reference_support import FrequencyResult, Quantity, SolveOutcome
from scripts.beat_conformance.reference_support import POSITIVE_TIME, NEGATIVE_TIME


def synthetic_outcome(convention, transform=lambda values: values):
    results = []
    points = observation_sets()
    for frequency in FREQUENCIES_HZ:
        quantities = []
        for name, coordinates in points.items():
            values = transform(
                analytic_pressure(
                    frequency,
                    np.linalg.norm(np.asarray(coordinates) - CENTRE_M, axis=1),
                    convention,
                )
            )
            quantities.append(
                Quantity(
                    f"pressure:{name}",
                    "exterior_pressure",
                    "Pa",
                    ("excitation", "observation"),
                    values[None, :],
                    None,
                    {},
                )
            )
        impedance = transform(np.array([analytic_impedance(frequency, convention)]))
        quantities.append(
            Quantity(
                "radiation_impedance",
                "radiation_impedance",
                "N*s/m",
                ("radiator",),
                impedance,
                None,
                {},
            )
        )
        results.append(
            FrequencyResult(
                frequency,
                ("excitation:sphere",),
                tuple(quantities),
                {
                    "phasor_convention": convention,
                    "precision": "float64",
                    "bem_backend": "cpu",
                    "symmetry": "off",
                },
            )
        )
    return SolveOutcome(
        "completed",
        results=results,
        engine_runs=[
            {"engine": {"repository_revision": EXPECTED_BEAT_REVISION, "repository_dirty": False}}
        ],
    )


@pytest.mark.parametrize("convention", [POSITIVE_TIME, NEGATIVE_TIME])
def test_analytic_scoring_and_propagation_branches(convention):
    score = score_outcome(synthetic_outcome(convention), convention, observation_sets())
    assert score["passed"]
    for item in score["frequencies"]:
        np.testing.assert_allclose(
            item["propagation_1m_to_2m"]["positive_propagation_delay_rad"],
            item["propagation_1m_to_2m"]["k_delta_r_rad"],
            atol=1e-14,
        )
    assert (
        abs(
            score["frequencies"][-1]["propagation_1m_to_2m"]["expected_signed_phase_difference_rad"]
        )
        > 2 * np.pi
    )


@pytest.mark.parametrize(
    "transform",
    [
        np.conj,
        lambda values: values / np.sqrt(2),
        lambda values: values * 1j,
        lambda values: -values,
    ],
)
def test_gate_detects_conjugation_amplitude_and_phase_errors(transform):
    score = score_outcome(
        synthetic_outcome(POSITIVE_TIME, transform), POSITIVE_TIME, observation_sets()
    )
    assert not score["passed"]


def test_impedance_equals_surface_pressure_integral_and_negative_time_is_conjugate():
    surface_pressure = analytic_pressure(300, RADIUS_M, POSITIVE_TIME)
    np.testing.assert_allclose(
        surface_pressure * 4 * np.pi * RADIUS_M**2,
        analytic_impedance(300, POSITIVE_TIME),
        rtol=1e-14,
    )
    np.testing.assert_allclose(
        analytic_pressure(300, [1, 2], NEGATIVE_TIME),
        analytic_pressure(300, [1, 2], POSITIVE_TIME).conj(),
        rtol=1e-14,
    )
    np.testing.assert_allclose(
        analytic_impedance(300, NEGATIVE_TIME), analytic_impedance(300, POSITIVE_TIME).conjugate()
    )


def test_gmsh_reference_is_small_closed_outward_and_wavelength_resolved(tmp_path):
    pytest.importorskip("gmsh")
    info = make_sphere_mesh(tmp_path / "sphere.msh")
    assert 100 < info["triangle_count"] <= 1500
    assert info["outward_winding"] and info["closed_manifold"]
    assert info["minimum_elements_per_wavelength"] >= 6
    assert abs(info["area_m2"] / info["analytic_area_m2"] - 1) < 0.01


def test_portable_report_keeps_hashes_and_excludes_local_identity(tmp_path):
    outcome = synthetic_outcome(POSITIVE_TIME)
    outcome.engine_runs = [
        {
            "schema_version": 1,
            "engine": {"source_sha256": "source-hash"},
            "runtime": {
                "executable": "private-executable-path",
                "machine": "private-host",
                "project_sha256": "project-hash",
            },
            "execution": {"backend": "cpu", "device": "private-device"},
            "meshes": [{"id": "mesh:exterior", "file": "private-mesh-path", "sha256": "mesh-hash"}],
        }
    ]
    original = copy.deepcopy(outcome.engine_runs)
    score = score_outcome(outcome, POSITIVE_TIME, observation_sets())
    destination = tmp_path / "report.json"
    write_report(score, destination)
    text = destination.read_text(encoding="utf-8")
    assert "private-" not in text
    assert all(value in text for value in ("source-hash", "project-hash", "mesh-hash"))
    assert outcome.engine_runs == original
    assert destination.read_bytes().endswith(b"\n")
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("part,index", [("real", 0), ("imag", 2)])
def test_impedance_real_and_imag_are_gated_separately(part, index):
    from scripts.beat_conformance.reference_sphere import _errors

    outcome = synthetic_outcome(POSITIVE_TIME)
    values = outcome.results[index].quantities[-1].values
    exact = values.copy()
    if part == "real":
        values.real *= 1.05
    else:
        values.imag *= 1.035
    assert _errors(values, exact)["passed"], (
        "Magnitude/phase alone must leave this regression invisible."
    )
    score = score_outcome(outcome, POSITIVE_TIME, observation_sets())
    assert not score["passed"]
    assert not score["frequencies"][index]["radiation_impedance"]["passed"]
    assert score["frequencies"][index]["radiation_impedance"][f"relative_{part}_error"][0] > 0.03


def test_report_is_readable_before_atomic_publication(tmp_path, monkeypatch):
    from scripts.beat_conformance import reference_sphere as ref

    original_replace = ref.os.replace
    checked = []

    def replace(source, destination):
        if os.name != "nt":
            assert source.stat().st_mode & 0o777 == 0o644
        checked.append(True)
        original_replace(source, destination)

    monkeypatch.setattr(ref.os, "replace", replace)
    write_report({"passed": True}, tmp_path / "reference.json")
    assert checked == [True]


@pytest.mark.parametrize("zero_default", [False, True])
def test_reference_runner_audits_defaults_and_exercises_translation(
    tmp_path, monkeypatch, zero_default
):
    from scripts.beat_conformance import reference_sphere as ref

    requests = []

    class FakeRuntime:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def solve(self, request):
            requests.append(copy.deepcopy(request))
            convention = request["solver_options"]["phasor_convention"]
            default = "quadrature_order" not in request["solver_options"]
            outcome = synthetic_outcome(
                convention,
                (lambda p: p * 0 if zero_default else p / np.sqrt(2)) if default else (lambda p: p),
            )
            return outcome

    monkeypatch.setattr(ref, "ReferenceSession", FakeRuntime)
    report = ref.run_reference(julia="unused-portable-julia", out=tmp_path)
    assert report["passed"]
    assert report["wg_reference_amplitude_convention"] == "rms"
    assert len(requests) == 3
    assert all(
        {item["id"] for item in request["outputs"]}
        == {"pressure:r1m", "pressure:r2m", "radiation_impedance"}
        for request in requests
    )
    assert requests[0]["compiled_system"]["meshes"][0]["translation_m"] == [0, 0, 0]
    assert requests[1]["compiled_system"]["meshes"][0]["translation_m"] == CENTRE_M.tolist()
    assert "quadrature_order" not in requests[2]["solver_options"]
    assert "singular_order" not in requests[2]["solver_options"]
    assert "regular_quadrature_mode" not in requests[2]["solver_options"]
    assert report["runs"][2]["quadrature"] == "default"
    assert report["runs"][2]["default_quadrature_qualified"] is False
    assert report["runs"][2]["gate_policy"].startswith("informational")
    if zero_default:
        zero_score = report["runs"][2]["frequencies"][0]
        assert zero_score["pressure"]["r1m"]["spl_db_if_input_rms_abs_p"] == [None] * 7
        assert zero_score["propagation_1m_to_2m"]["undefined_reason"]
        assert not zero_score["propagation_1m_to_2m"]["passed"]
    text = (tmp_path / "reference-sphere.json").read_text(encoding="utf-8")
    assert "spl_db_if_input_peak_abs_p_over_sqrt2" in text
    assert "spl_db_if_input_rms_abs_p" in text


@pytest.mark.parametrize(
    "defect", ["dirty", "missing_dirty", "wrong_revision", "missing_provenance", "second_dirty"]
)
def test_reference_requires_clean_pinned_provenance(defect):
    outcome = synthetic_outcome(POSITIVE_TIME)
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
            {"engine": {"repository_revision": EXPECTED_BEAT_REVISION, "repository_dirty": True}}
        )
    score = score_outcome(outcome, POSITIVE_TIME, observation_sets())
    assert not score["provenance_revision_passed"]
    assert not score["passed"]


@pytest.mark.parametrize(
    "failure",
    [
        "dirty_fixed",
        "dirty_default",
        "default_error",
        "default_missing_output",
        "default_empty_completed_error",
    ],
)
def test_runner_provenance_and_informational_failure_policy(tmp_path, monkeypatch, failure):
    from scripts.beat_conformance import reference_sphere as ref
    from scripts.beat_conformance.reference_support import ReferenceSolveError

    class FakeRuntime:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def solve(self, request):
            default = "quadrature_order" not in request["solver_options"]
            outcome = synthetic_outcome(request["solver_options"]["phasor_convention"])
            if failure == ("dirty_default" if default else "dirty_fixed"):
                outcome.engine_runs[0]["engine"]["repository_dirty"] = True
            if default and failure.startswith("default_"):
                outcome.status = "failed"
                if failure == "default_missing_output":
                    outcome.results[0] = FrequencyResult(outcome.results[0].freq_hz, (), (), {})
                elif failure == "default_empty_completed_error":
                    # Decoding can fail even if the worker later emits completed.
                    outcome = SolveOutcome("completed")
                raise ReferenceSolveError("private-executable-path", outcome)
            return outcome

    monkeypatch.setattr(ref, "ReferenceSession", FakeRuntime)

    def fake_mesh(destination, **kwargs):
        from scripts.beat_conformance.analytic import gmsh_surface, icosphere

        vertices, faces = icosphere(RADIUS_M, 0)
        destination.write_text(gmsh_surface(vertices, faces, tag=1))
        return {}

    monkeypatch.setattr(ref, "make_sphere_mesh", fake_mesh)
    report = ref.run_reference(julia="unused-portable-julia", out=tmp_path)
    assert report["passed"] is failure.startswith("default_")
    assert len(report["runs"]) == 3
    if failure.startswith("default_"):
        audit = report["runs"][2]
        assert audit["failure_type"] == "ReferenceSolveError"
        assert not audit["passed"] and not audit["default_quadrature_qualified"]
        assert audit["gate_policy"].startswith("informational")
    assert "private-" not in (tmp_path / "reference-sphere.json").read_text(encoding="utf-8")
