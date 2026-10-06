"""Diagnostic controls use synthetic data and never modify gate arrays."""

from __future__ import annotations

import numpy as np

from scripts.beat_conformance.wire_quantization import diagnose, hbb_pressure_wire


def test_wire_quantization_reproduces_shortest_float32_decimal_without_changing_inputs():
    frequencies = np.array([500., 502., 504.])
    velocity = np.array([1.234567891 + .123456789j, .100000001 - 2.123456789j, -3.765432198 + .987654321j])
    scale = 1 / (-1j * 2 * np.pi * frequencies)
    official = (velocity * scale)[:, None]
    original = official.copy()
    hbb = (np.array([1.2345679 + .12345679j, .1 - 2.1234567j, -3.7654321 + .9876543j]) * scale)[:, None]
    assert np.array_equal(hbb_pressure_wire(official, frequencies), hbb)
    report = diagnose(official, hbb, frequencies)
    assert report["raw_relative_l2"] > 0
    assert report["after_shortest_float32_decimal_relative_l2"] == 0
    assert report["shortest_decimal_equal_samples"] == report["total_samples"] == 3
    assert np.array_equal(official, original)
