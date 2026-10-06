"""Diagnostic-only HBB Float32 wire simulation; never used by agreement gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .recorder import write_record
from .runners import output_directory


def hbb_pressure_wire(pressure: np.ndarray, frequencies_hz: np.ndarray) -> np.ndarray:
    """Undo acceleration scaling, round/decimal-encode Float32, then restore it."""
    pressure = np.asarray(pressure, dtype=complex)
    frequencies = np.asarray(frequencies_hz, dtype=float)
    if pressure.shape[0] != len(frequencies) or not np.isfinite(pressure).all() or np.any(frequencies <= 0):
        raise ValueError("Finite frequency-major pressure and positive frequencies required")
    scale = 1 / (-1j * 2 * np.pi * frequencies.reshape((-1,) + (1,) * (pressure.ndim - 1)))
    velocity = pressure / scale

    def decimal(values):
        rounded = values.astype(np.float32)
        return np.asarray([float(str(value)) for value in rounded.flat]).reshape(values.shape)

    return (decimal(velocity.real) + 1j * decimal(velocity.imag)) * scale


def diagnose(official: np.ndarray, hbb: np.ndarray, frequencies_hz: np.ndarray) -> dict:
    transformed = hbb_pressure_wire(official, frequencies_hz)
    denominator = np.linalg.norm(hbb)
    if np.shape(hbb) != transformed.shape or denominator == 0:
        raise ValueError("Matching nonzero reference pressure required")
    return {"raw_relative_l2": float(np.linalg.norm(official - hbb) / denominator),
            "after_shortest_float32_decimal_relative_l2": float(np.linalg.norm(transformed - hbb) / denominator),
            "shortest_decimal_equal_samples": int(np.sum(transformed == hbb)), "total_samples": int(transformed.size)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official", type=Path, required=True, help="Official engine-only recorder JSON")
    parser.add_argument("--hbb", type=Path, required=True, help="HBB engine-only recorder JSON")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output_directory(args.output)
    official, hbb = (json.loads(path.read_text()) for path in (args.official, args.hbb))

    def complex_array(values):
        pairs = np.asarray(values, dtype=float)
        return pairs[..., 0] + 1j * pairs[..., 1]

    record = {"kind": "diagnostic only; transformed arrays never feed gate inputs",
              "official_sha256": official["record_sha256"], "hbb_sha256": hbb["record_sha256"],
              "diagnostic": diagnose(complex_array(official["result"]["pressure_complex"]),
                                     complex_array(hbb["pressure_complex"]), hbb["requested_frequencies_hz"])}
    write_record(args.output, record)


if __name__ == "__main__":
    main()
