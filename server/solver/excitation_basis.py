"""The pressure-basis conversion shared by impedance mapping and driver LEM."""

import numpy as np


def pressure_per_velocity_scale(
    frequencies_hz: np.ndarray | list[float], normalization: str = "acceleration_legacy",
) -> np.ndarray:
    """Convert pressure in the stated solver basis to pressure per 1 m/s RMS.

    Untagged results retain acceleration-consumer semantics, including historical
    assembly runs whose fields were scaled without informing these consumers.
    Only explicit requests opt into the corrected basis contract.
    """
    frequencies = np.asarray(frequencies_hz, dtype=float)
    if normalization == "rms_velocity":
        return np.ones(frequencies.shape, dtype=complex)
    if normalization == "acceleration_legacy":
        return -2j * np.pi * frequencies
    raise ValueError(f"Unknown result excitation basis: {normalization!r}")
