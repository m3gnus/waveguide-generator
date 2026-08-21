"""Combination-core math: LR4 chain, level match, alignment, conventions."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from server.solver.combine import combine_drive_channels, lr4_chain_weights


def _freqs() -> np.ndarray:
    return np.geomspace(100.0, 10_000.0, 64)


def _member(
    eng_on_axis: np.ndarray,
    *,
    angles: np.ndarray | None = None,
    planes: list[str] | None = None,
    sphere: np.ndarray | None = None,
) -> SimpleNamespace:
    """Build a native-shaped result from an engineering on-axis spectrum.

    The raw solver field is the conjugate of the engineering one; every
    angle of every plane carries the same spectrum so on-axis assertions
    describe the whole field.
    """

    angles = np.asarray([-30.0, 0.0, 30.0]) if angles is None else angles
    planes = ["horizontal", "vertical"] if planes is None else planes
    raw = np.conjugate(np.asarray(eng_on_axis, dtype=np.complex128))
    field = np.tile(raw[:, None, None], (1, len(planes), angles.size))
    return SimpleNamespace(
        frequencies_hz=np.asarray(_freqs()),
        observation_angles_deg=angles,
        observation_points=None,
        observation_planes=list(planes),
        pressure_complex=field,
        directivity_db=np.zeros_like(field, dtype=float),
        impedance=np.zeros(_freqs().size, dtype=np.complex128),
        solver_log=[],
        timings={},
        native_diagnostics=[],
        sphere_pressure_complex=sphere,
        sphere_points=None,
        sphere_theta_deg=np.asarray([0.0, 90.0]) if sphere is not None else None,
        sphere_phi_deg=np.asarray([0.0, 180.0]) if sphere is not None else None,
    )


def test_lr4_pair_is_allpass_and_minus_six_db_at_fc() -> None:
    freqs = _freqs()
    weights = lr4_chain_weights(freqs, ["low", "high"], [1000.0])
    summed = weights["low"] + weights["high"]
    assert np.allclose(np.abs(summed), 1.0, atol=1e-9)
    at_fc = np.interp(1000.0, freqs, np.abs(weights["low"]))
    assert at_fc == pytest.approx(0.5, abs=1e-3)


def test_three_way_chain_has_bandpass_middle() -> None:
    freqs = _freqs()
    weights = lr4_chain_weights(freqs, ["lf", "mf", "hf"], [300.0, 3000.0])
    mid = np.abs(weights["mf"])
    assert mid[0] < 0.05 and mid[-1] < 0.05
    assert np.interp(1000.0, freqs, mid) > 0.9


def test_identical_members_combine_to_allpass_magnitude() -> None:
    base = (1.0 + 0.2j) * np.ones(_freqs().size)
    results = {"low": _member(base), "high": _member(base)}
    combined, payload = combine_drive_channels(
        results, members=["low", "high"], crossovers_hz=[1000.0]
    )
    on_axis = combined.pressure_complex[:, 0, 1]
    assert np.allclose(np.abs(on_axis), np.abs(base), rtol=1e-6)
    assert payload["delays_ms"]["low"] == pytest.approx(0.0, abs=1e-6)
    assert payload["delays_ms"]["high"] == pytest.approx(0.0, abs=1e-6)
    assert all(
        value == pytest.approx(0.0, abs=1e-9)
        for value in payload["level_match"]["gains_db"].values()
    )


def test_weights_are_conjugated_onto_raw_fields() -> None:
    ones = np.ones(_freqs().size, dtype=np.complex128)
    results = {"low": _member(ones), "high": _member(ones)}
    combined, _payload = combine_drive_channels(
        results,
        members=["low", "high"],
        crossovers_hz=[1000.0],
        level_match=False,
        align=False,
    )
    weights = lr4_chain_weights(_freqs(), ["low", "high"], [1000.0])
    expected_raw = np.conjugate(weights["low"] + weights["high"])
    assert np.allclose(combined.pressure_complex[:, 0, 1], expected_raw, atol=1e-12)


def test_member_roles_are_reported_parallel_to_the_members() -> None:
    ones = np.ones(_freqs().size, dtype=np.complex128)
    results = {"low": _member(ones), "high": _member(ones)}
    payload = combine_drive_channels(
        results,
        members=["low", "high"],
        crossovers_hz=[1000.0],
        member_roles={"low": "LF", "high": "HF"},
    )[1]
    assert payload["members"] == ["low", "high"]
    assert payload["member_roles"] == ["LF", "HF"]

    unroled = combine_drive_channels(
        results, members=["low", "high"], crossovers_hz=[1000.0]
    )[1]
    assert unroled["member_roles"] == [None, None]

    partial = combine_drive_channels(
        results,
        members=["low", "high"],
        crossovers_hz=[1000.0],
        member_roles={"high": "HF"},
    )[1]
    assert partial["member_roles"] == [None, "HF"]


@pytest.mark.parametrize("delay_s", [100e-6, 600e-6])
def test_alignment_recovers_a_known_arrival_offset(delay_s: float) -> None:
    # 600 us exceeds half a period at the 1.2 kHz crossover, so recovering it
    # exercises the group-delay period-branch selection, not just the
    # principal value.
    freqs = _freqs()
    base = np.ones(freqs.size, dtype=np.complex128)
    delayed = base * np.exp(-1j * 2.0 * np.pi * freqs * delay_s)
    results = {"low": _member(base), "high": _member(delayed)}
    combined, payload = combine_drive_channels(
        results, members=["low", "high"], crossovers_hz=[1200.0]
    )
    assert payload["delays_ms"]["low"] == pytest.approx(delay_s * 1000.0, rel=1e-3)
    assert payload["delays_ms"]["high"] == pytest.approx(0.0, abs=1e-9)
    # The delay is measured through interpolated phase at fc, so the aligned
    # sum carries a small residual ripple rather than being an exact allpass.
    on_axis = combined.pressure_complex[:, 0, 1]
    assert np.allclose(np.abs(on_axis), 1.0, atol=1e-2)


def test_level_match_gain_tracks_a_member_level_change() -> None:
    freqs = _freqs()
    base = np.ones(freqs.size, dtype=np.complex128)
    equal = combine_drive_channels(
        {"low": _member(base), "high": _member(base)},
        members=["low", "high"],
        crossovers_hz=[1000.0],
    )[1]["level_match"]["gains_db"]
    halved = combine_drive_channels(
        {"low": _member(base), "high": _member(base * 0.5)},
        members=["low", "high"],
        crossovers_hz=[1000.0],
    )[1]["level_match"]["gains_db"]
    assert halved["high"] - equal["high"] == pytest.approx(3.0102, abs=1e-3)
    assert halved["low"] - equal["low"] == pytest.approx(-3.0102, abs=1e-3)


def test_level_match_disabled_keeps_unit_gains() -> None:
    base = np.ones(_freqs().size, dtype=np.complex128)
    _combined, payload = combine_drive_channels(
        {"low": _member(base), "high": _member(base * 0.25)},
        members=["low", "high"],
        crossovers_hz=[1000.0],
        level_match=False,
    )
    assert all(value == 0.0 for value in payload["level_match"]["gains_db"].values())
    assert payload["level_match"]["enabled"] is False


def test_crossover_above_member_validity_warns() -> None:
    base = np.ones(_freqs().size, dtype=np.complex128)
    _combined, payload = combine_drive_channels(
        {"low": _member(base), "high": _member(base)},
        members=["low", "high"],
        crossovers_hz=[2000.0],
        member_validity_hz={"low": 1500.0},
    )
    assert any("validity limit 1500" in warning for warning in payload["warnings"])


def test_mismatched_frequency_axes_refuse() -> None:
    base = np.ones(_freqs().size, dtype=np.complex128)
    other = _member(base)
    other.frequencies_hz = np.asarray(other.frequencies_hz) * 2.0
    with pytest.raises(ValueError, match="frequency axis"):
        combine_drive_channels(
            {"low": _member(base), "high": other},
            members=["low", "high"],
            crossovers_hz=[1000.0],
        )


def test_balloon_sums_with_the_same_weights() -> None:
    freqs = _freqs()
    base = np.ones(freqs.size, dtype=np.complex128)
    sphere = np.tile(np.conjugate(base)[:, None], (1, 2))
    results = {
        "low": _member(base, sphere=sphere.copy()),
        "high": _member(base, sphere=sphere.copy()),
    }
    combined, _payload = combine_drive_channels(
        results,
        members=["low", "high"],
        crossovers_hz=[1000.0],
        level_match=False,
        align=False,
    )
    weights = lr4_chain_weights(freqs, ["low", "high"], [1000.0])
    expected = np.conjugate(weights["low"] + weights["high"])[:, None] * sphere
    assert combined.sphere_pressure_complex is not None
    assert np.allclose(combined.sphere_pressure_complex, expected, atol=1e-12)


def test_partial_balloon_coverage_warns_and_drops_the_balloon() -> None:
    base = np.ones(_freqs().size, dtype=np.complex128)
    sphere = np.ones((_freqs().size, 2), dtype=np.complex128)
    results = {"low": _member(base, sphere=sphere), "high": _member(base)}
    combined, payload = combine_drive_channels(
        results, members=["low", "high"], crossovers_hz=[1000.0]
    )
    assert combined.sphere_pressure_complex is None
    assert any("balloon" in warning for warning in payload["warnings"])


def test_channel_bases_roundtrip() -> None:
    from server.solver.combine import (
        deserialize_channel_bases,
        serialize_channel_bases,
    )

    base = np.ones(_freqs().size, dtype=np.complex128) * (0.5 - 0.25j)
    sphere = np.tile(np.conjugate(base)[:, None], (1, 2))
    results = {
        "low": _member(base, sphere=sphere.copy()),
        "high": _member(base * 2.0, sphere=sphere * 2.0),
    }
    bundle = deserialize_channel_bases(serialize_channel_bases(results))
    assert bundle["channel_ids"] == ["low", "high"]
    assert bundle["has_balloon"] is True
    assert np.array_equal(bundle["frequencies_hz"], _freqs())
    restored = bundle["results_by_id"]
    assert np.allclose(restored["low"].pressure_complex, results["low"].pressure_complex)
    assert np.allclose(
        restored["high"].sphere_pressure_complex,
        results["high"].sphere_pressure_complex,
    )
    combined, payload = combine_drive_channels(
        restored, members=["low", "high"], crossovers_hz=[1000.0]
    )
    assert combined.pressure_complex.shape == results["low"].pressure_complex.shape
    assert payload["members"] == ["low", "high"]


def test_channel_bases_without_balloon_roundtrip() -> None:
    from server.solver.combine import (
        deserialize_channel_bases,
        serialize_channel_bases,
    )

    base = np.ones(_freqs().size, dtype=np.complex128)
    bundle = deserialize_channel_bases(
        serialize_channel_bases({"low": _member(base), "high": _member(base)})
    )
    assert bundle["has_balloon"] is False
    assert bundle["results_by_id"]["low"].sphere_pressure_complex is None
