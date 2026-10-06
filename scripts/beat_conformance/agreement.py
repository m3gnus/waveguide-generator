"""PLAN §5 numerical agreement, with resonance locations scored first."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

import numpy as np
from scipy.signal import find_peaks

from server.contracts.conventions import SOLVER_TIME_CONVENTION

from .recorder import record_sha256

REFERENCE_COMPLEX_RELATIVE_L2 = 1e-5
REFERENCE_MASKED_SPL_DB = 0.001
PRODUCTION_COMPLEX_RELATIVE_L2 = 1e-4
PRODUCTION_MASKED_SPL_DB = 0.01
PRODUCTION_PHASE_DEG = 0.1
NORMALIZED_IMPEDANCE_DB = 0.01
NORMALIZED_IMPEDANCE_PHASE_DEG = 0.1
DI_POWER_DB = 0.02
REFERENCE_MASK_DB = 30.
RESONANCE_MAX_STEPS = 1.
RESONANCE_LOCAL_STEP_FRACTION = 0.005
FROZEN_SETTINGS = ("tags", "normals", "axes", "observation_points", "quadrature", "backend", "precision",
                   "time_convention", "threads", "density_kg_per_m3", "sound_speed_m_per_s")


@dataclass(frozen=True)
class ResultSet:
    mesh_bytes: bytes
    frequencies_hz: np.ndarray
    pressure_complex: np.ndarray
    impedance_per_acceleration: np.ndarray
    di_db: np.ndarray
    power_w: np.ndarray
    settings: dict[str, Any]
    revision: str = ""
    recorder_record: dict[str, Any] | None = None
    recorder_sha256: str = ""
    setting_evidence: dict[str, Any] | None = None


def _canonical(settings: dict[str, Any]) -> str:
    def encode(value: Any) -> Any:
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        raise TypeError(f"Unsupported frozen setting {type(value).__name__}")
    return json.dumps(settings, sort_keys=True, allow_nan=False, default=encode)


def _columns(value: np.ndarray, count: int, name: str) -> np.ndarray:
    value = np.asarray(value)
    if value.ndim < 1 or value.shape[0] != count or not value.size or not np.isfinite(value).all():
        raise ValueError(f"{name} must be finite, nonempty and frequency-major")
    return value.reshape(count, -1)


def _extrema(levels: np.ndarray, prominence_db: float) -> dict[str, list[int]]:
    """Topographic prominence crosses smaller ripples; dips use inverted levels."""
    return {kind: find_peaks(sign * levels, prominence=prominence_db)[0].tolist()
            for kind, sign in (("peaks", 1), ("dips", -1))}


def resonance_gate(frequencies: np.ndarray, reference: np.ndarray, candidate: np.ndarray, *,
                   frequency_step_hz: float, prominence_db: float,
                   expected_columns: tuple[int, ...] = ()) -> dict[str, Any]:
    failures, columns, unstructured = [], [], []
    if any(type(column) is not int or not 0 <= column < reference.shape[1] for column in expected_columns):
        raise ValueError("Expected resonance column is outside the result shape")
    for column in range(reference.shape[1]):
        # No mask at this gate: a dip below the SPL mask still matters.
        levels = [20 * np.log10(np.maximum(np.abs(value[:, column]), np.finfo(float).tiny))
                  for value in (reference, candidate)]
        ref, got = (_extrema(value, prominence_db) for value in levels)
        if not any(ref.values()):
            unstructured.append(column)
            if column in expected_columns:
                failures.append(f"column {column}: reference has no expected resonances")
        entry = {"column": column, "reference": {}, "candidate": {}, "shifts_hz": {}}
        for kind in ("peaks", "dips"):
            a, b = ref[kind], got[kind]
            entry["reference"][kind] = frequencies[a].tolist()
            entry["candidate"][kind] = frequencies[b].tolist()
            if len(a) != len(b):
                failures.append(f"column {column}: missing/extra {kind} ({len(a)} vs {len(b)})")
                continue
            shifts = np.abs(frequencies[a] - frequencies[b])
            entry["shifts_hz"][kind] = shifts.tolist()
            if np.any(shifts > RESONANCE_MAX_STEPS * frequency_step_hz + 1e-12):
                failures.append(f"column {column}: {kind} moved more than one declared frequency step")
            for index in (*a, *b):
                local_step = max(frequencies[index] - frequencies[index - 1],
                                 frequencies[index + 1] - frequencies[index])
                if local_step / frequencies[index] >= RESONANCE_LOCAL_STEP_FRACTION:
                    failures.append(f"column {column}: {kind} requires local refinement below 0.5%")
                    break
        columns.append(entry)
    return {"passed": not failures, "failures": failures, "columns": columns,
            "frequency_step_hz": frequency_step_hz, "prominence_db": prominence_db,
            "reference_columns_without_extrema": unstructured}


def _masked_complex(reference: np.ndarray, candidate: np.ndarray) -> dict[str, Any]:
    # Reference-defined mask, independently for each frequency across its
    # observation/channel samples. Candidate amplitudes never change the mask.
    maximum = np.max(np.abs(reference), axis=1, keepdims=True)
    mask = (maximum > 0) & (np.abs(reference) >= maximum * 10**(-REFERENCE_MASK_DB / 20))
    nulls = ~mask
    ref_norm = float(np.linalg.norm(reference))
    if ref_norm == 0 or not np.any(mask):
        return {"passed": False, "limitation": "Reference contains no nonzero pressure",
                "masked_count": 0, "null_count": int(nulls.sum())}
    difference = candidate - reference
    ratio = candidate[mask] / reference[mask]
    zero = np.any(np.abs(ratio) == 0)
    return {"relative_l2": float(np.linalg.norm(difference) / ref_norm),
            "masked_spl_db": None if zero else float(np.max(np.abs(20 * np.log10(np.abs(ratio))))),
            "phase_deg": None if zero else float(np.max(np.abs(np.degrees(np.angle(ratio))))),
            "masked_count": int(mask.sum()), "null_count": int(nulls.sum()),
            "null_difference_l2": float(np.linalg.norm(difference[nulls])),
            "reference_null_l2": float(np.linalg.norm(reference[nulls])),
            "candidate_null_l2": float(np.linalg.norm(candidate[nulls])),
            "zero_in_mask": bool(zero)}


def compare_results(reference: ResultSet, candidate: ResultSet, *, frequency_step_hz: float,
                    resonance_prominence_db: float,
                    expected_resonance_columns: dict[str, tuple[int, ...]] | None = None) -> dict[str, Any]:
    """Return a strict-JSON verdict; missing quantities/identity cannot pass.

    Inputs carry identical original mesh bytes and frozen settings. Impedance
    is WG's mean pressure per acceleration; normalize its velocity basis by
    rho*c. DI is in dB and acoustic power in positive linear watts. Thresholds
    are fixed here; the scan step and physical prominence are declared by the
    corpus owner before running either engine.
    """
    report: dict[str, Any] = {"passed": False, "failures": [], "limitations": [], "metrics": {},
                             "reference_revision": reference.revision, "candidate_revision": candidate.revision}
    try:
        if (not reference.revision.strip() or not candidate.revision.strip()
                or reference.revision.strip() == candidate.revision.strip()):
            raise ValueError("Reference/candidate revisions must be nonempty and different")
        forced_lu_reference = False
        if reference.recorder_record is not None or candidate.recorder_record is not None:
            for result in (reference, candidate):
                record = result.recorder_record
                if record is None or not result.recorder_sha256:
                    raise ValueError("Both results require recorder record/hash bindings")
                digest = record_sha256(record)
                if digest != result.recorder_sha256:
                    raise ValueError("Recorder record hash differs")
                if (record.get("evidence_mode") != "real"
                        or record.get("engine_status", record.get("status")) != "passed"):
                    raise ValueError("Recorder records must be passed real-solve records")
                case = record.get("case", {})
                for name in ("backend", "precision"):
                    if case.get(name) != result.settings.get(name):
                        raise ValueError(f"Recorder case {name} differs from ResultSet settings")
                if record.get("runtime", {}).get("engine_revision") != result.revision:
                    raise ValueError("Recorder revision differs from ResultSet revision")
            # The recorder hashes the packed request mesh; both runs must share it.
            meshes = {r.recorder_record.get("mesh_sha256") for r in (reference, candidate)}
            if len(meshes) != 1 or None in meshes:
                raise ValueError("Recorder mesh hashes differ or are missing")
            rows = reference.recorder_record.get("result", {}).get("solver_log", [])
            forced_lu_reference = bool(rows) and all(_actual_lu(row.get("native_diagnostics", {})) for row in rows)
            report["reference_record_sha256"] = reference.recorder_sha256
            report["candidate_record_sha256"] = candidate.recorder_sha256
        report["evidence_binding"] = "recorded" if reference.recorder_record is not None else "attested"
        report["forced_lu_reference"] = forced_lu_reference
        if (not np.isfinite(frequency_step_hz) or frequency_step_hz <= 0
                or not np.isfinite(resonance_prominence_db) or resonance_prominence_db < 0):
            raise ValueError("Declare positive frequency step and non-negative physical prominence")
        if not reference.mesh_bytes or reference.mesh_bytes != candidate.mesh_bytes:
            raise ValueError("Original mesh bytes differ or are empty")
        report["mesh_sha256"] = hashlib.sha256(reference.mesh_bytes).hexdigest()
        if reference.setting_evidence is not None or candidate.setting_evidence is not None:
            if reference.setting_evidence is None or candidate.setting_evidence is None:
                raise ValueError("Both engines require observed/declared setting evidence")
            a, b = reference.settings, candidate.settings
            missing = set()
            for key in FROZEN_SETTINGS:
                if not any(name == key or name.startswith(key + ".") for name in a):
                    missing.add(key)
            shared = a.keys() & b.keys()
            if missing or set(a) != set(b) or any(
                    not settings_equal(key, a[key], b[key]) for key in shared):
                raise ValueError(f"Frozen settings differ or are incomplete (missing {sorted(missing)})")
            report["settings_observed_equal"] = sorted(key for key in shared if all(
                result.setting_evidence.get(key, {}).get("status") == "observed" for result in (reference, candidate)))
            report["settings_declared"] = sorted(shared - set(report["settings_observed_equal"]))
            report["limitations"].append("Settings with declared evidence are not verified equality: "
                                          + ", ".join(report["settings_declared"]))
        else:
            missing = set(FROZEN_SETTINGS) - reference.settings.keys()
            if missing or _canonical(reference.settings) != _canonical(candidate.settings):
                raise ValueError(f"Frozen settings differ or are incomplete (missing {sorted(missing)})")
            report["limitations"].append("Settings are declared; no observed equality is verified")
        if reference.settings["time_convention"] != SOLVER_TIME_CONVENTION:
            raise ValueError("Expected negative-time phasor convention")
        frequencies = np.asarray(reference.frequencies_hz, dtype=float)
        if (frequencies.ndim != 1 or len(frequencies) < 3 or not np.isfinite(frequencies).all()
                or np.any(frequencies <= 0) or len(np.unique(frequencies)) != len(frequencies)
                or not np.array_equal(frequencies, candidate.frequencies_hz)):
            raise ValueError("Frequency axes must be identical, positive and distinct")
        order = np.argsort(frequencies)
        frequencies = frequencies[order]
        report["frequency_sort_indices"] = order.tolist()
        if np.max(np.diff(frequencies)) > frequency_step_hz + 1e-12:
            raise ValueError("Frequency axis is coarser than the declared dense step")
        count = len(frequencies)
        arrays = {}
        for name in ("pressure_complex", "impedance_per_acceleration", "di_db", "power_w"):
            a, b = (_columns(getattr(result, name), count, name) for result in (reference, candidate))
            if np.shape(getattr(reference, name)) != np.shape(getattr(candidate, name)):
                raise ValueError(f"{name} shapes differ")
            if name in {"di_db", "power_w"} and (np.iscomplexobj(a) or np.iscomplexobj(b)):
                raise ValueError(f"{name} must be real")
            arrays[name] = a[order], b[order]
        rho_c = float(reference.settings["density_kg_per_m3"]) * float(reference.settings["sound_speed_m_per_s"])
        if not np.isfinite(rho_c) or rho_c <= 0:
            raise ValueError("Medium impedance must be positive and finite")
        arrays["normalized_impedance"] = tuple(
            value * (-1j * 2 * np.pi * frequencies[:, None]) / rho_c
            for value in arrays["impedance_per_acceleration"])
        for name in ("pressure_complex", "normalized_impedance"):
            gate = resonance_gate(frequencies, *arrays[name], frequency_step_hz=frequency_step_hz,
                                  prominence_db=resonance_prominence_db,
                                  expected_columns=(expected_resonance_columns or {}).get(name, ()))
            report.setdefault("resonances", {})[name] = gate
            report["failures"].extend(f"{name}: {failure}" for failure in gate["failures"])
        if expected_resonance_columns and set(expected_resonance_columns) - {"pressure_complex", "normalized_impedance"}:
            raise ValueError("Unknown expected resonance quantity")
        report["limitations"].append("Resonances use interior sampled extrema and predeclared prominence; endpoints must bracket features")
        if report["failures"]:
            report["limitations"].append("Error norms not scored because the resonance gate failed")
            return report
        pressure = _masked_complex(*arrays["pressure_complex"])
        report["metrics"]["pressure"] = pressure
        l2_limit = REFERENCE_COMPLEX_RELATIVE_L2 if forced_lu_reference else PRODUCTION_COMPLEX_RELATIVE_L2
        spl_limit = REFERENCE_MASKED_SPL_DB if forced_lu_reference else PRODUCTION_MASKED_SPL_DB
        limits = {"relative_l2": l2_limit, "masked_spl_db": spl_limit, "phase_deg": PRODUCTION_PHASE_DEG}
        report["thresholds"] = {"pressure": limits, "mask_db": REFERENCE_MASK_DB,
                                "impedance_db": NORMALIZED_IMPEDANCE_DB,
                                "impedance_phase_deg": NORMALIZED_IMPEDANCE_PHASE_DEG, "di_power_db": DI_POWER_DB}
        for name, limit in limits.items():
            if pressure.get(name) is None or pressure[name] > limit:
                report["failures"].append(f"pressure {name} exceeds {limit}")
        a, b = arrays["normalized_impedance"]
        zero = (np.abs(a) == 0) | (np.abs(b) == 0)
        if np.any(zero):
            report["failures"].append("Normalized impedance has zero samples; phase/level undefined")
            report["metrics"]["normalized_impedance"] = {"zero_count": int(zero.sum())}
        else:
            ratio = b / a
            level = float(np.max(np.abs(20 * np.log10(np.abs(ratio)))))
            phase = float(np.max(np.abs(np.degrees(np.angle(ratio)))))
            report["metrics"]["normalized_impedance"] = {
                "level_db": level, "phase_deg": phase,
                "reference": np.stack((a.real, a.imag), axis=-1).tolist(),
                "candidate": np.stack((b.real, b.imag), axis=-1).tolist()}
            if level > NORMALIZED_IMPEDANCE_DB or phase > NORMALIZED_IMPEDANCE_PHASE_DEG:
                report["failures"].append("Normalized impedance exceeds level/phase budget")
        di = float(np.max(np.abs(arrays["di_db"][1] - arrays["di_db"][0])))
        power_a, power_b = arrays["power_w"]
        if np.any(power_a <= 0) or np.any(power_b <= 0):
            raise ValueError("Power must be positive for a comparable dB metric")
        power = float(np.max(np.abs(10 * np.log10(power_b / power_a))))
        report["metrics"].update(di_db=di, power_db=power)
        if di > DI_POWER_DB or power > DI_POWER_DB:
            report["failures"].append("DI/power exceeds 0.02 dB")
        report["passed"] = not report["failures"]
    except (ValueError, TypeError, KeyError) as exc:
        report["failures"].append(str(exc))
    return report


def settings_equal(key: str, a: Any, b: Any) -> bool:
    if key == "mesh.tag_areas_m2":
        return set(a) == set(b) and all(np.isclose(a[tag], b[tag], rtol=1e-12, atol=0) for tag in a)
    return _canonical({key: a}) == _canonical({key: b})


def _actual_lu(diagnostic: dict) -> bool:
    if "dense_solve_method" in diagnostic:
        return diagnostic["dense_solve_method"] == "lu"
    return diagnostic.get("linear_solver") in {"cpu_dense_lu", "metal_assembly_cpu_dense_lu"}
