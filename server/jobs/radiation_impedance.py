"""Safe, presentation-oriented reads of stored radiation-matrix artifacts."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import numpy as np


RADIATION_IMPEDANCE_UNITS = "Pa*s/m^3"
RADIATION_IMPEDANCE_PHASE_CONVENTION = "engineering_exp_plus_jwt"
RADIATION_IMPEDANCE_QUANTITY = (
    "average_aperture_pressure_per_volume_velocity"
)


def _one_dimensional(data: Any, key: str, length: int | None = None) -> np.ndarray:
    value = np.asarray(data)
    if value.ndim != 1 or (length is not None and value.shape != (length,)):
        expected = "one-dimensional" if length is None else f"shape ({length},)"
        raise ValueError(f"radiation-impedance artifact {key} must have {expected}")
    return value


def _finite_float_array(data: Any, key: str) -> np.ndarray:
    value = np.asarray(data, dtype=np.float64)
    if not np.all(np.isfinite(value)):
        raise ValueError(f"radiation-impedance artifact {key} must be finite")
    return value


def _finite_complex_array(data: Any, key: str) -> np.ndarray:
    value = np.asarray(data, dtype=np.complex128)
    if not np.all(np.isfinite(value.real) & np.isfinite(value.imag)):
        raise ValueError(f"radiation-impedance artifact {key} must be finite")
    return value


def radiation_impedance_presentation(matrix_npz: bytes) -> dict[str, Any]:
    """Return the engineering-convention matrix and reduced plot curves.

    The NPZ deliberately stores both the Metal solver convention and the
    engineering ``exp(+j omega t)`` convention. Presentation and interchange
    must use the latter; exposing the solver matrix here would make a sign flip
    look like a second physical result.
    """

    try:
        archive = np.load(BytesIO(matrix_npz), allow_pickle=False)
    except Exception as exc:
        raise ValueError("radiation-impedance artifact is not a readable NPZ") from exc

    with archive:
        required = {
            "frequencies_hz",
            "aperture_names",
            "aperture_area_m2",
            "aperture_tag",
            "engineering_impedance_matrix",
            "in_phase_aperture_names",
            "in_phase_termination_load",
        }
        missing = sorted(required.difference(archive.files))
        if missing:
            raise ValueError(
                "radiation-impedance artifact is missing " + ", ".join(missing)
            )

        frequencies = _finite_float_array(
            _one_dimensional(archive["frequencies_hz"], "frequencies_hz"),
            "frequencies_hz",
        )
        aperture_names_raw = _one_dimensional(
            archive["aperture_names"], "aperture_names"
        )
        aperture_names = [str(value) for value in aperture_names_raw.tolist()]
        if not aperture_names or any(not name for name in aperture_names):
            raise ValueError(
                "radiation-impedance artifact aperture_names must not be empty"
            )
        count = len(aperture_names)
        areas = _finite_float_array(
            _one_dimensional(archive["aperture_area_m2"], "aperture_area_m2", count),
            "aperture_area_m2",
        )
        if np.any(areas <= 0.0):
            raise ValueError(
                "radiation-impedance artifact aperture_area_m2 must be positive"
            )
        tags = np.asarray(
            _one_dimensional(archive["aperture_tag"], "aperture_tag", count),
            dtype=np.int64,
        )

        engineering = _finite_complex_array(
            archive["engineering_impedance_matrix"],
            "engineering_impedance_matrix",
        )
        expected_matrix_shape = (frequencies.size, count, count)
        if engineering.shape != expected_matrix_shape:
            raise ValueError(
                "radiation-impedance artifact engineering_impedance_matrix must "
                f"have shape {expected_matrix_shape}, got {engineering.shape}"
            )

        in_phase_names_raw = _one_dimensional(
            archive["in_phase_aperture_names"], "in_phase_aperture_names"
        )
        in_phase_names = [str(value) for value in in_phase_names_raw.tolist()]
        if any(name not in aperture_names for name in in_phase_names):
            raise ValueError(
                "radiation-impedance artifact in-phase aperture names must name "
                "matrix apertures"
            )
        in_phase = _finite_complex_array(
            archive["in_phase_termination_load"], "in_phase_termination_load"
        )
        expected_load_shape = (frequencies.size, len(in_phase_names))
        if in_phase.shape != expected_load_shape:
            raise ValueError(
                "radiation-impedance artifact in_phase_termination_load must "
                f"have shape {expected_load_shape}, got {in_phase.shape}"
            )

        return {
            "schema_version": 1,
            "quantity": RADIATION_IMPEDANCE_QUANTITY,
            "units": RADIATION_IMPEDANCE_UNITS,
            "phase_time_convention": RADIATION_IMPEDANCE_PHASE_CONVENTION,
            "frequencies_hz": frequencies.tolist(),
            "apertures": [
                {"name": name, "area_m2": float(areas[index]), "tag": int(tags[index])}
                for index, name in enumerate(aperture_names)
            ],
            "engineering_matrix": {
                "real": engineering.real.tolist(),
                "imaginary": engineering.imag.tolist(),
            },
            "in_phase_termination": {
                "aperture_names": in_phase_names,
                "real": in_phase.real.tolist(),
                "imaginary": in_phase.imag.tolist(),
            },
        }
