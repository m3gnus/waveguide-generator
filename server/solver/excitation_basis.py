"""The pressure-basis conversion shared by impedance mapping and driver LEM."""

import numpy as np


def pressure_per_velocity_scale(
    frequencies_hz: np.ndarray | list[float], normalization: str = "acceleration_legacy",
) -> np.ndarray:
    """Convert pressure in the stated solver basis to pressure per 1 m/s RMS.

    Untagged results retain acceleration-consumer semantics. An old assembly
    replay (no ``excitation_normalization``) decodes to RMS velocity untagged:
    ``normalize_prescribed_result`` scales its pressure fields to the velocity
    basis but leaves its impedance and per-tag means in the acceleration basis,
    so this conversion is their only one. Explicit requests tag the result and
    opt into the velocity basis contract here.
    """
    frequencies = np.asarray(frequencies_hz, dtype=float)
    if normalization == "rms_velocity":
        return np.ones(frequencies.shape, dtype=complex)
    if normalization == "acceleration_legacy":
        return -2j * np.pi * frequencies
    raise ValueError(f"Unknown result excitation basis: {normalization!r}")
