"""Synthetic same-mesh comparisons; no numerical engine is invoked."""

from __future__ import annotations

from dataclasses import replace
import json

import numpy as np
import pytest

from scripts.beat_conformance.agreement import (DI_POWER_DB, NORMALIZED_IMPEDANCE_DB,
                                               NORMALIZED_IMPEDANCE_PHASE_DEG,
                                               PRODUCTION_COMPLEX_RELATIVE_L2,
                                               PRODUCTION_MASKED_SPL_DB, PRODUCTION_PHASE_DEG,
                                               REFERENCE_COMPLEX_RELATIVE_L2, REFERENCE_MASKED_SPL_DB,
                                               ResultSet, compare_results)
from server.contracts.conventions import SOLVER_TIME_CONVENTION


@pytest.fixture
def reference():
    f = np.arange(1000., 1100.25, .25)
    amplitude = 1 + .2 * np.exp(-((f - 1040) / 2)**2) - .1 * np.exp(-((f - 1070) / 2)**2)
    return ResultSet(
        b"identical-mesh-bytes", f, amplitude[:, None] * np.exp(.3j),
        (1 + .2j) / (-1j * 2 * np.pi * f), np.zeros_like(f), np.ones_like(f),
        {"tags": [2], "normals": [[0, 0, 1]], "axes": [[0, 0, 1]],
         "observation_points": [[0, 0, 3]], "quadrature": {"regular": 4, "singular": 4},
         "precision": "float64", "time_convention": SOLVER_TIME_CONVENTION, "threads": 4,
         "density_kg_per_m3": 1.2041, "sound_speed_m_per_s": 343.}, "hbb-exact-pin")


def compare(a, b, **kwargs):
    return compare_results(a, b, frequency_step_hz=.25, resonance_prominence_db=.01, **kwargs)


def test_identical_results_pass_every_metric_and_record_resonances_first(reference):
    report = compare(reference, replace(reference, revision="official-exact-sha"))
    assert report["passed"], report
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    assert report["mesh_sha256"]
    features = report["resonances"]["pressure_complex"]["columns"][0]["reference"]
    assert features == {"peaks": [1040.], "dips": [1070.]}
    assert report["metrics"]["pressure"]["relative_l2"] == 0
    expected = (1 + .2j) / (1.2041 * 343)
    normalized = report["metrics"]["normalized_impedance"]["reference"][0][0]
    assert normalized == pytest.approx([expected.real, expected.imag])
    assert report["metrics"]["di_db"] == report["metrics"]["power_db"] == 0


def test_shifted_weak_resonance_fails_before_norms_even_with_tiny_l2(reference):
    f = reference.frequencies_hz
    a = 1 + 1e-6 * np.exp(-((f - 1040) / .5)**2)
    b = 1 + 1e-6 * np.exp(-((f - 1041) / .5)**2)
    assert np.linalg.norm(b - a) / np.linalg.norm(a) < REFERENCE_COMPLEX_RELATIVE_L2
    report = compare_results(replace(reference, pressure_complex=a[:, None]),
                             replace(reference, pressure_complex=b[:, None]),
                             frequency_step_hz=.25, resonance_prominence_db=1e-7)
    assert not report["passed"]
    assert "moved more than one" in " ".join(report["failures"])
    assert report["metrics"] == {}


@pytest.mark.parametrize("feature", ["missing_peak", "extra_peak", "missing_dip"])
def test_no_missing_or_extra_physical_peaks_or_dips(reference, feature):
    f = reference.frequencies_hz
    pressure = reference.pressure_complex.copy()
    if feature == "missing_peak":
        pressure[:, 0] -= .2 * np.exp(-((f - 1040) / 2)**2) * np.exp(.3j)
    elif feature == "missing_dip":
        pressure[:, 0] += .1 * np.exp(-((f - 1070) / 2)**2) * np.exp(.3j)
    else:
        pressure[:, 0] += .2 * np.exp(-((f - 1085) / 2)**2) * np.exp(.3j)
    report = compare(reference, replace(reference, pressure_complex=pressure))
    assert not report["passed"]
    assert "missing/extra" in " ".join(report["failures"])


def test_one_step_resonance_allowed_but_local_refinement_required(reference):
    f = reference.frequencies_hz
    shifted = 1 + .2 * np.exp(-((f - 1040.25) / 2)**2) - .1 * np.exp(-((f - 1070.25) / 2)**2)
    report = compare(reference, replace(reference, pressure_complex=shifted[:, None] * np.exp(.3j)))
    assert report["resonances"]["pressure_complex"]["passed"]
    # The norm budget is independent and still rejects the differing curve.
    assert not report["passed"] and report["metrics"]
    indices = np.arange(0, len(f), 24)
    coarse = replace(reference, frequencies_hz=f[indices], pressure_complex=reference.pressure_complex[indices],
                     impedance_per_acceleration=reference.impedance_per_acceleration[indices],
                     di_db=reference.di_db[indices], power_w=reference.power_w[indices])
    report = compare_results(coarse, coarse, frequency_step_hz=6, resonance_prominence_db=.01)
    assert not report["passed"] and "refinement" in " ".join(report["failures"])


def test_mask_is_reference_defined_and_null_errors_are_separate(reference):
    pressure = np.column_stack((reference.pressure_complex[:, 0], reference.pressure_complex[:, 0] * 1e-3))
    a = replace(reference, pressure_complex=pressure)
    changed = pressure.copy()
    changed[:, 1] *= np.exp(.01j)
    report = compare(a, replace(a, pressure_complex=changed))
    assert report["passed"], report
    metric = report["metrics"]["pressure"]
    assert metric["null_count"] == len(reference.frequencies_hz)
    assert metric["masked_count"] == len(reference.frequencies_hz)
    assert metric["masked_spl_db"] == pytest.approx(0, abs=1e-12)
    assert metric["phase_deg"] == pytest.approx(0, abs=1e-12)
    assert metric["null_difference_l2"] > 0
    assert metric["candidate_null_l2"] == pytest.approx(metric["reference_null_l2"])


@pytest.mark.parametrize("field,factor,error", [
    ("pressure_complex", 1.0002, "relative_l2"),
    ("pressure_complex", np.exp(1j * np.radians(.11)), "phase_deg"),
    ("impedance_per_acceleration", 10**(.011 / 20), "Normalized impedance"),
    ("impedance_per_acceleration", np.exp(1j * np.radians(.11)), "Normalized impedance"),
    ("power_w", 10**(.021 / 10), "DI/power")])
def test_each_production_budget_is_enforced(reference, field, factor, error):
    report = compare(reference, replace(reference, **{field: getattr(reference, field) * factor}))
    assert not report["passed"] and error in " ".join(report["failures"])


def test_di_budget_and_forced_lu_stricter_l2(reference):
    report = compare(reference, replace(reference, di_db=reference.di_db + .021))
    assert not report["passed"]
    candidate = replace(reference, pressure_complex=reference.pressure_complex * 1.00005)
    assert compare(reference, candidate)["passed"]
    assert not compare(reference, candidate, forced_lu_reference=True)["passed"]
    assert compare(reference, reference, forced_lu_reference=True)["thresholds"]["pressure"] == {
        "relative_l2": REFERENCE_COMPLEX_RELATIVE_L2, "masked_spl_db": REFERENCE_MASKED_SPL_DB,
        "phase_deg": PRODUCTION_PHASE_DEG}
    assert (REFERENCE_COMPLEX_RELATIVE_L2, REFERENCE_MASKED_SPL_DB,
            PRODUCTION_COMPLEX_RELATIVE_L2, PRODUCTION_MASKED_SPL_DB,
            PRODUCTION_PHASE_DEG, NORMALIZED_IMPEDANCE_DB, NORMALIZED_IMPEDANCE_PHASE_DEG, DI_POWER_DB) == (
        1e-5, .001, 1e-4, .01, .1, .01, .1, .02)


@pytest.mark.parametrize("mutation", ["mesh", "frequencies", "settings", "missing_settings", "shape",
                                     "nonfinite", "zero_pressure", "zero_impedance", "zero_power"])
def test_uncomparable_or_vacuous_results_fail_explicitly(reference, mutation):
    candidate = reference
    if mutation == "mesh":
        candidate = replace(reference, mesh_bytes=b"different")
    elif mutation == "frequencies":
        candidate = replace(reference, frequencies_hz=reference.frequencies_hz + .01)
    elif mutation == "settings":
        candidate = replace(reference, settings={**reference.settings, "threads": 8})
    elif mutation == "missing_settings":
        reference = replace(reference, settings={})
        candidate = reference
    elif mutation == "shape":
        candidate = replace(reference, pressure_complex=reference.pressure_complex[:, 0])
    elif mutation == "nonfinite":
        candidate = replace(reference, power_w=reference.power_w * np.nan)
    elif mutation == "zero_pressure":
        reference = replace(reference, pressure_complex=reference.pressure_complex * 0)
        candidate = reference
    elif mutation == "zero_impedance":
        reference = replace(reference, impedance_per_acceleration=reference.impedance_per_acceleration * 0)
        candidate = reference
    else:
        candidate = replace(reference, power_w=reference.power_w * 0)
    report = compare(reference, candidate)
    assert not report["passed"] and report["failures"]
    json.dumps(report, allow_nan=False)


def test_impedance_resonance_also_blocks_agreement(reference):
    f = reference.frequencies_hz
    a = reference.impedance_per_acceleration * (1 + .2 * np.exp(-((f - 1040) / 2)**2))
    b = reference.impedance_per_acceleration * (1 + .2 * np.exp(-((f - 1041) / 2)**2))
    report = compare(replace(reference, impedance_per_acceleration=a),
                     replace(reference, impedance_per_acceleration=b))
    assert not report["passed"] and not report["metrics"]
    assert not report["resonances"]["normalized_impedance"]["passed"]


def test_identical_unordered_sweeps_are_sorted_together_for_resonances(reference):
    order = np.arange(len(reference.frequencies_hz))[::-1]
    unordered = replace(reference, frequencies_hz=reference.frequencies_hz[order],
                        pressure_complex=reference.pressure_complex[order],
                        impedance_per_acceleration=reference.impedance_per_acceleration[order],
                        di_db=reference.di_db[order], power_w=reference.power_w[order])
    report = compare(unordered, unordered)
    assert report["passed"]
    assert report["resonances"]["pressure_complex"]["columns"][0]["reference"] == {
        "peaks": [1040.], "dips": [1070.]}
