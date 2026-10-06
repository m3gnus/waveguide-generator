"""Synthetic same-mesh comparisons; no numerical engine is invoked."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import numpy as np
import pytest

from scripts.beat_conformance import agreement
from scripts.beat_conformance.agreement import (DI_POWER_DB, NORMALIZED_IMPEDANCE_DB,
                                               NORMALIZED_IMPEDANCE_PHASE_DEG,
                                               PRODUCTION_COMPLEX_RELATIVE_L2,
                                               PRODUCTION_MASKED_SPL_DB, PRODUCTION_PHASE_DEG,
                                               REFERENCE_COMPLEX_RELATIVE_L2, REFERENCE_MASKED_SPL_DB,
                                               ResultSet, compare_results, resonance_gate)
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
         "backend": "cpu", "precision": "float64", "time_convention": SOLVER_TIME_CONVENTION, "threads": 4,
         "density_kg_per_m3": 1.2041, "sound_speed_m_per_s": 343.}, "hbb-exact-pin")


def compare(a, b, **kwargs):
    if b.revision == a.revision:
        b = replace(b, revision="official-exact-sha")
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
                             replace(reference, pressure_complex=b[:, None], revision="official-exact-sha"),
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
    report = compare_results(coarse, replace(coarse, revision="official-exact-sha"), frequency_step_hz=6, resonance_prominence_db=.01)
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
    lu = bind_record(reference, "lu")
    candidate = bind_record(replace(candidate, revision="official-exact-sha"), "gmres")
    assert not compare(lu, candidate)["passed"]
    assert compare(lu, bind_record(replace(reference, revision="official-exact-sha"), "lu"))["thresholds"]["pressure"] == {
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


def bind_record(result, method, mesh_sha256="packed-request-mesh", **overrides):
    record = {"mesh_sha256": mesh_sha256, "evidence_mode": "real", "status": "passed",
              "case": {"backend": result.settings["backend"], "precision": result.settings["precision"]},
              "runtime": {"engine_revision": result.revision},
              "result": {"solver_log": [{"native_diagnostics": {"linear_solver": "cpu_dense_" + method}}]}}
    record.update(overrides)
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return replace(result, recorder_record=record, recorder_sha256=digest)


@pytest.mark.parametrize("sign", [1, -1], ids=["peaks", "dips"])
@pytest.mark.parametrize("mutation", ["removed", "shifted_four_steps"])
def test_topographic_prominence_crosses_subthreshold_ripples(reference, sign, mutation, monkeypatch):
    f = reference.frequencies_hz
    ripple = .005 * np.cos(np.pi * np.arange(len(f)))
    levels = sign * (5 * np.exp(-((f - 1040) / 10)**2) + ripple)
    changed = sign * ripple if mutation == "removed" else sign * (5 * np.exp(-((f - 1041) / 10)**2) + ripple)
    pressure = lambda level: np.column_stack((np.ones_like(f), 1e-5 * 10**(level / 20)))
    a, b = pressure(levels), pressure(changed)
    # Failing control: the old adjacent-ripple gate detected no physical
    # features, and both existing norm/mask budgets accept these weak columns.
    assert np.linalg.norm(b - a) / np.linalg.norm(a) < REFERENCE_COMPLEX_RELATIVE_L2
    assert np.max(a[:, 1]) < 10**(-30 / 20)
    ref = replace(reference, pressure_complex=a)
    candidate = replace(reference, pressure_complex=b, revision="official-exact-sha")
    with monkeypatch.context() as control:
        control.setattr(agreement, "_extrema", adjacent_extrema_control)
        old_verdict = compare_results(ref, candidate, frequency_step_hz=.25, resonance_prominence_db=.1)
        assert old_verdict["passed"], old_verdict
    report = compare_results(ref, candidate, frequency_step_hz=.25, resonance_prominence_db=.1)
    assert not report["passed"] and not report["metrics"]
    gate = report["resonances"]["pressure_complex"]
    kind = "peaks" if sign == 1 else "dips"
    assert len(gate["columns"][1]["reference"][kind]) == 1
    assert "missing/extra" in " ".join(gate["failures"]) or "moved" in " ".join(gate["failures"])


def test_expected_resonances_prevent_vacuous_monotone_agreement(reference):
    f = reference.frequencies_hz
    monotone = np.linspace(1, 2, len(f))[:, None]
    gate = resonance_gate(f, monotone, monotone, frequency_step_hz=.25, prominence_db=.1)
    assert gate["passed"] and gate["reference_columns_without_extrema"] == [0]
    a = replace(reference, pressure_complex=monotone)
    b = replace(a, revision="official-exact-sha")
    assert compare(a, b)["passed"]  # Old gate's silent pass, valid only without expected structure.
    report = compare(a, b, expected_resonance_columns={"pressure_complex": (0,)})
    assert not report["passed"] and "no expected resonances" in " ".join(report["failures"])


@pytest.mark.parametrize("revision", ["", "hbb-exact-pin", "  "])
def test_distinct_revision_identity_prevents_self_comparison(reference, revision):
    report = compare_results(reference, replace(reference, revision=revision),
                             frequency_step_hz=.25, resonance_prominence_db=.01)
    assert not report["passed"] and "revisions" in " ".join(report["failures"])


def test_backend_freeze_refuses_undeclared_cross_backend_agreement(reference):
    report = compare(reference, replace(reference, settings={**reference.settings, "backend": "metal"}))
    assert not report["passed"] and "Frozen settings" in " ".join(report["failures"])


@pytest.mark.parametrize("mutation", ["hash", "mesh", "revision", "one_binding", "synthetic", "failed",
                                      "backend", "precision"])
def test_recorder_binding_rejects_detached_agreement(reference, mutation):
    a = bind_record(reference, "lu")
    b = bind_record(replace(reference, revision="official-exact-sha"), "lu")
    assert compare(a, b)["passed"]
    if mutation == "hash":
        b = replace(b, recorder_sha256="bad")
    elif mutation == "mesh":
        b = bind_record(replace(reference, revision="official-exact-sha"), "lu", mesh_sha256="other mesh")
    elif mutation == "synthetic":
        b = bind_record(replace(reference, revision="official-exact-sha"), "lu", evidence_mode="synthetic")
    elif mutation == "failed":
        b = bind_record(replace(reference, revision="official-exact-sha"), "lu", status="failed")
    elif mutation in {"backend", "precision"}:
        recorded = {"backend": "metal", "precision": "float32"}[mutation]
        case = {**b.recorder_record["case"], mutation: recorded}
        b = bind_record(replace(reference, revision="official-exact-sha"), "lu", case=case)
    elif mutation == "revision":
        b = replace(b, revision="unrecorded-revision")
    else:
        b = replace(b, recorder_record=None, recorder_sha256="")
    assert not compare(a, b)["passed"]


def test_forced_lu_budget_is_derived_from_record_solve_method(reference):
    candidate = replace(reference, revision="official-exact-sha", pressure_complex=reference.pressure_complex * 1.00005)
    a, b = bind_record(reference, "gmres"), bind_record(candidate, "lu")
    assert compare(a, b)["passed"] and not compare(a, b)["forced_lu_reference"]
    report = compare(bind_record(reference, "lu"), b)
    assert not report["passed"] and report["forced_lu_reference"]


def adjacent_extrema_control(levels, prominence_db):
    """Previous algorithm, retained only as a failing prominence control."""
    slopes = np.diff(levels)
    changing = np.flatnonzero(slopes)
    peaks, dips = [], []
    for left, right in zip(changing[:-1], changing[1:]):
        if slopes[left] * slopes[right] < 0:
            index = int((left + 1 + right) // 2)
            (peaks if slopes[left] > 0 else dips).append(index)
    extrema = sorted([0, *peaks, *dips, len(levels) - 1])
    result = {"peaks": [], "dips": []}
    for kind, indices in (("peaks", peaks), ("dips", dips)):
        sign = 1 if kind == "peaks" else -1
        for index in indices:
            position = extrema.index(index)
            left, right = extrema[position - 1], extrema[position + 1]
            if min(sign * (levels[index] - levels[left]), sign * (levels[index] - levels[right])) >= prominence_db:
                result[kind].append(index)
    return result


def test_required_backend_setting_blocks_unidentified_cross_backend_results(reference, monkeypatch):
    settings = {key: value for key, value in reference.settings.items() if key != "backend"}
    a = replace(reference, settings=settings)
    b = replace(a, revision="official-exact-sha")
    with monkeypatch.context() as control:
        control.setattr(agreement, "FROZEN_SETTINGS", tuple(key for key in agreement.FROZEN_SETTINGS if key != "backend"))
        assert compare(a, b)["passed"]  # Previous gate allowed unidentified backends.
    report = compare(a, b)
    assert not report["passed"] and "backend" in " ".join(report["failures"])


def test_recorder_to_agreement_binding_uses_hbb_actual_dense_solve_method(reference):
    from scripts.beat_conformance.recorder import record_sha256
    ref = bind_record(reference, "gmres")
    ref.recorder_record["result"]["solver_log"] = [{"native_diagnostics": {"dense_solve_method": "lu"}}]
    ref = replace(ref, recorder_sha256=record_sha256(ref.recorder_record))
    candidate = bind_record(replace(reference, revision="official-exact-sha"), "lu")
    report = compare(ref, candidate)
    assert report["passed"] and report["evidence_binding"] == "recorded" and report["forced_lu_reference"]
    assert report["thresholds"]["pressure"]["relative_l2"] == REFERENCE_COMPLEX_RELATIVE_L2


def test_vacuous_settings_equality_declared_values_are_never_verified(reference):
    from scripts.beat_conformance.settings import flatten
    values = {**flatten(reference.settings), "blas_threads": 4}
    evidence = {key: {"status": "declared", "value": value} for key, value in values.items()}
    for name in ("backend", "blas_threads"):
        evidence[name]["status"] = "observed"
    a = replace(reference, settings=values, setting_evidence=evidence)
    b = replace(a, revision="official-exact-sha")
    report = compare(a, b)
    assert report["passed"] and report["settings_observed_equal"] == ["backend", "blas_threads"]
    assert "precision" in report["settings_declared"]
    assert any("not verified equality" in text for text in report["limitations"])
    changed = replace(b, settings={**values, "precision": "float32"})
    assert not compare(a, changed)["passed"]


def test_vacuous_settings_equality_detects_different_actual_quadrature_orders(reference):
    from scripts.beat_conformance.settings import flatten
    values = {**flatten(reference.settings), "blas_threads": 4}
    evidence = {key: {"status": "observed", "value": values[key]} for key in ("backend", "blas_threads")}
    name = "quadrature.regular_quadrature_order"
    a = replace(reference, settings={**values, name: [2] * len(reference.frequencies_hz)},
                setting_evidence={**evidence, name: {"status": "observed"}})
    b = replace(a, revision="official-exact-sha", settings={**a.settings, name: [4] * len(reference.frequencies_hz)})
    report = compare(a, b)
    assert not report["passed"] and "Frozen settings" in " ".join(report["failures"])


def test_recorder_to_agreement_binding_actual_method_overrides_linear_solver_label(reference):
    from scripts.beat_conformance.recorder import record_sha256
    ref = bind_record(reference, "lu")
    ref.recorder_record["result"]["solver_log"][0]["native_diagnostics"]["dense_solve_method"] = "gmres"
    ref = replace(ref, recorder_sha256=record_sha256(ref.recorder_record))
    candidate = bind_record(replace(reference, revision="official-exact-sha"), "lu")
    report = compare(ref, candidate)
    assert report["passed"] and not report["forced_lu_reference"]
    assert report["reference_record_sha256"] == ref.recorder_sha256
    assert report["candidate_record_sha256"] == candidate.recorder_sha256


@pytest.mark.parametrize("backend", ["cpu", "metal"])
@pytest.mark.parametrize("missing", ["backend", "blas_threads"])
@pytest.mark.parametrize("defect", ["absent_both", "declared_both", "absent_candidate"])
def test_agreement_missing_blas_or_backend_observations_cannot_compare_equal(reference, backend, missing, defect):
    settings = {**reference.settings, "backend": backend, "blas_threads": 4}
    evidence = {name: {"status": "observed", "value": value} for name, value in settings.items()}
    a = replace(reference, settings=dict(settings), setting_evidence=dict(evidence))
    b = replace(a, revision="official-exact-sha", settings=dict(settings), setting_evidence=dict(evidence))
    assert compare(a, b)["passed"]
    affected = (b,) if defect == "absent_candidate" else (a, b)
    for result in affected:
        if defect == "declared_both":
            result.setting_evidence[missing] = {"status": "declared", "value": settings[missing]}
        else:
            result.setting_evidence.pop(missing)
            if missing == "blas_threads":
                result.settings.pop(missing)
    report = compare(a, b)
    assert not report["passed"] and f"observed {missing}" in " ".join(report["failures"])


def test_metal_agreement_without_any_observation_evidence_fails_closed(reference):
    a = replace(reference, settings={**reference.settings, "backend": "metal"})
    assert not compare(a, a)["passed"]


@pytest.mark.parametrize("context", [False, True])
@pytest.mark.parametrize("missing_side", [0, 1, "both"])
def test_agreement_electrical_presence_is_a_structural_error(reference, context, missing_side):
    electrical = np.ones(len(reference.frequencies_hz), complex)
    pair = [replace(reference, electrical_impedance_ohm=electrical),
            replace(reference, revision="official-exact-sha", electrical_impedance_ohm=electrical)]
    damaged = [replace(r, electrical_impedance_ohm=None) if missing_side == "both" or index == missing_side else r
               for index, r in enumerate(pair)]
    with pytest.raises(ValueError, match="electrical_impedance_ohm"):
        compare(*(pair if context else damaged),
                resonance_context=tuple(damaged) if context else None,
                expected_resonance_columns={"electrical_impedance_ohm": (0,)})


@pytest.mark.parametrize("context", [False, True])
def test_agreement_one_sided_optional_electrical_raises(reference, context):
    other = replace(reference, electrical_impedance_ohm=np.ones(len(reference.frequencies_hz)))
    with pytest.raises(ValueError, match="electrical_impedance_ohm"):
        compare(reference, reference, resonance_context=(reference, other)) if context else compare(reference, other)


@pytest.mark.parametrize("field", ["pressure_complex", "impedance_per_acceleration", "di_db", "power_w", "electrical_impedance_ohm"])
@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_agreement_context_arrays_must_be_finite(reference, field, bad):
    if field == "electrical_impedance_ohm":
        reference = replace(reference, electrical_impedance_ohm=np.ones(len(reference.frequencies_hz), complex))
    value = np.array(getattr(reference, field), copy=True)
    value.flat[-1] = bad
    damaged = replace(reference, **{field: value})
    with pytest.raises(ValueError, match="finite"):
        compare(reference, reference, resonance_context=(reference, damaged))


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_agreement_electrical_arrays_must_be_finite(reference, bad):
    value = np.ones(len(reference.frequencies_hz), complex)
    value[0] = bad
    with pytest.raises(ValueError, match="finite"):
        compare(replace(reference, electrical_impedance_ohm=value), replace(reference, electrical_impedance_ohm=value))


def test_agreement_window_axis_must_be_on_context_axis(reference):
    shifted = replace(reference, frequencies_hz=reference.frequencies_hz + .125)
    with pytest.raises(ValueError, match="Window axis.*context axis"):
        compare(shifted, shifted, resonance_context=(reference, reference))
