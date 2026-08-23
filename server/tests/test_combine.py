"""Combination-core math: LR4 chain, level match, alignment, conventions."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from server.solver.combine import (
    combine_drive_channels,
    expand_legacy_channels,
    lr4_chain_weights,
)


def _freqs() -> np.ndarray:
    return np.geomspace(100.0, 10_000.0, 64)


def _member(
    eng_on_axis: np.ndarray,
    *,
    angles: np.ndarray | None = None,
    planes: list[str] | None = None,
    sphere: np.ndarray | None = None,
    freqs: np.ndarray | None = None,
) -> SimpleNamespace:
    """Build a native-shaped result from an engineering on-axis spectrum.

    The raw solver field is the conjugate of the engineering one; every
    angle of every plane carries the same spectrum so on-axis assertions
    describe the whole field.
    """

    angles = np.asarray([-30.0, 0.0, 30.0]) if angles is None else angles
    planes = ["horizontal", "vertical"] if planes is None else planes
    freqs = _freqs() if freqs is None else freqs
    raw = np.conjugate(np.asarray(eng_on_axis, dtype=np.complex128))
    field = np.tile(raw[:, None, None], (1, len(planes), angles.size))
    return SimpleNamespace(
        frequencies_hz=np.asarray(freqs),
        observation_angles_deg=angles,
        observation_points=None,
        observation_planes=list(planes),
        pressure_complex=field,
        directivity_db=np.zeros_like(field, dtype=float),
        impedance=np.zeros(np.asarray(freqs).size, dtype=np.complex128),
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


# --- Per-channel spec: filters, gains, delays, polarity, metrics -----------

_SOUND_SPEED_M_PER_S = 343.0


def _section(family: str, order: int, fc_hz: float) -> dict[str, object]:
    return {"family": family, "order": order, "fc_hz": fc_hz}


def _two_way(
    family: str = "lr",
    order: int = 4,
    fc_hz: float = 1_000.0,
    *,
    low: dict[str, object] | None = None,
    high: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    return {
        "low": {"lp": _section(family, order, fc_hz), **(low or {})},
        "high": {"hp": _section(family, order, fc_hz), **(high or {})},
    }


def _delayed(freqs: np.ndarray, delay_s: float) -> np.ndarray:
    return np.exp(-1j * 2.0 * np.pi * np.asarray(freqs) * delay_s)


def test_a_legacy_spec_and_its_expansion_produce_the_same_payload() -> None:
    freqs = _freqs()
    base = np.ones(freqs.size, dtype=np.complex128)
    results = {"low": _member(base), "high": _member(base * _delayed(freqs, 220e-6))}
    legacy = combine_drive_channels(
        results, members=["low", "high"], crossovers_hz=[1_000.0]
    )[1]
    expanded = combine_drive_channels(
        results,
        members=["low", "high"],
        channels=expand_legacy_channels(["low", "high"], [1_000.0]),
    )[1]
    assert legacy == expanded
    assert legacy["type"] == "filtered_time_aligned_sum"
    assert legacy["reference"] == "high"
    assert legacy["channels"]["low"]["lp"] == {
        "family": "lr",
        "order": 4,
        "fc_hz": 1_000.0,
    }
    assert legacy["channels"]["low"]["hp"] is None

    off = combine_drive_channels(
        results,
        members=["low", "high"],
        crossovers_hz=[1_000.0],
        level_match=False,
        align=False,
    )[1]
    off_expanded = combine_drive_channels(
        results,
        members=["low", "high"],
        channels=expand_legacy_channels(
            ["low", "high"], [1_000.0], level_match=False, align=False
        ),
    )[1]
    assert off == off_expanded
    assert off["align"] is False
    assert off["level_match"]["enabled"] is False
    assert off["delays_ms"] == {"low": 0.0, "high": 0.0}
    # The auto values stay visible so the UI can offer "reset to auto".
    assert off["channels"]["low"]["delay_auto_ms"] == pytest.approx(0.22, rel=1e-3)


def test_alignment_resolves_an_offset_longer_than_one_period() -> None:
    # 450 mm is 1.31 ms — 1.3 periods at the 1 kHz crossover, so the phase
    # value at fc alone cannot say which cycle it is. A 30-point log grid is
    # coarse enough that the unwrap has to be handled deliberately.
    freqs = np.geomspace(200.0, 5_000.0, 30)
    delay_s = 0.450 / _SOUND_SPEED_M_PER_S
    base = np.ones(freqs.size, dtype=np.complex128)
    results = {
        "low": _member(base, freqs=freqs),
        "high": _member(base * _delayed(freqs, delay_s), freqs=freqs),
    }
    _combined, payload = combine_drive_channels(
        results, members=["low", "high"], crossovers_hz=[1_000.0]
    )
    assert payload["delays_ms"]["low"] == pytest.approx(delay_s * 1_000.0, rel=1e-6)
    assert payload["delays_ms"]["high"] == pytest.approx(0.0, abs=1e-12)
    assert payload["pairs"]["low-high"]["fit_residual_deg"] == pytest.approx(
        0.0, abs=1e-6
    )


@pytest.mark.parametrize(
    "family,order", [("lr", 4), ("lr", 2), ("butterworth", 3), ("bessel", 4)]
)
def test_every_family_recovers_the_same_physical_offset(
    family: str, order: int
) -> None:
    freqs = _freqs()
    delay_s = 380e-6
    base = np.ones(freqs.size, dtype=np.complex128)
    results = {
        "low": _member(base),
        "high": _member(base * _delayed(freqs, delay_s)),
    }
    _combined, payload = combine_drive_channels(
        results, members=["low", "high"], channels=_two_way(family, order)
    )
    assert payload["delays_ms"]["low"] == pytest.approx(delay_s * 1_000.0, rel=1e-6)


def test_manual_gain_and_delay_are_verbatim_and_auto_is_still_reported() -> None:
    freqs = _freqs()
    delay_s = 300e-6
    base = np.ones(freqs.size, dtype=np.complex128)
    results = {
        "low": _member(base * 0.5),
        "high": _member(base * _delayed(freqs, delay_s)),
    }
    _combined, payload = combine_drive_channels(
        results,
        members=["low", "high"],
        channels=_two_way(
            low={
                "gain": {"mode": "manual", "db": -2.5},
                "delay": {"mode": "manual", "ms": 0.45},
            }
        ),
    )
    low = payload["channels"]["low"]
    assert low["gain_mode"] == "manual"
    assert low["gain_db"] == pytest.approx(-2.5)
    assert low["gain_auto_db"] == pytest.approx(3.0103, abs=1e-3)
    assert low["delay_mode"] == "manual"
    assert low["delay_ms"] == pytest.approx(0.45)
    assert low["delay_auto_ms"] == pytest.approx(delay_s * 1_000.0, rel=1e-6)
    assert payload["delays_ms"]["low"] == pytest.approx(0.45)
    assert payload["gains_db"]["low"] == pytest.approx(-2.5)
    # The high channel is still auto on both counts.
    high = payload["channels"]["high"]
    assert high["gain_mode"] == "auto"
    assert high["gain_db"] == pytest.approx(high["gain_auto_db"])
    # A mixed spec still counts as level matched and aligned for the strip.
    assert payload["level_match"]["enabled"] is True
    assert payload["align"] is True


def test_polarity_follows_the_ideal_pair_unless_it_is_stated() -> None:
    base = np.ones(_freqs().size, dtype=np.complex128)
    results = {"low": _member(base), "high": _member(base)}

    lr4 = combine_drive_channels(
        results, members=["low", "high"], channels=_two_way("lr", 4)
    )[1]
    assert lr4["channels"]["low"]["inverted"] is False
    assert lr4["channels"]["high"]["inverted"] is False
    assert lr4["channels"]["high"]["invert_mode"] == "auto"

    lr2 = combine_drive_channels(
        results, members=["low", "high"], channels=_two_way("lr", 2)
    )[1]
    assert lr2["channels"]["high"]["inverted"] is True
    assert lr2["channels"]["high"]["invert_mode"] == "auto"

    forced = combine_drive_channels(
        results,
        members=["low", "high"],
        channels=_two_way("lr", 2, high={"invert": False}),
    )[1]
    assert forced["channels"]["high"]["inverted"] is False
    assert forced["channels"]["high"]["invert_mode"] == "manual"

    # An inverted LR2 pair sums flat; leaving it in phase nulls at fc.
    combined, _payload = combine_drive_channels(
        results, members=["low", "high"], channels=_two_way("lr", 2)
    )
    assert np.allclose(np.abs(combined.pressure_complex[:, 0, 1]), 1.0, atol=1e-6)


def test_three_way_polarity_accumulates_along_the_chain() -> None:
    base = np.ones(_freqs().size, dtype=np.complex128)
    results = {name: _member(base) for name in ("lf", "mf", "hf")}
    channels = {
        "lf": {"lp": _section("lr", 2, 300.0)},
        "mf": {"hp": _section("lr", 2, 300.0), "lp": _section("lr", 2, 3_000.0)},
        "hf": {"hp": _section("lr", 2, 3_000.0)},
    }
    payload = combine_drive_channels(
        results, members=["lf", "mf", "hf"], channels=channels
    )[1]
    inverted = {
        name: payload["channels"][name]["inverted"] for name in ("lf", "mf", "hf")
    }
    assert inverted == {"lf": False, "mf": True, "hf": False}


def test_the_reference_channel_is_pinned_at_zero_delay() -> None:
    freqs = _freqs()
    base = np.ones(freqs.size, dtype=np.complex128)
    results = {
        "lf": _member(base),
        "mf": _member(base * _delayed(freqs, 200e-6)),
        "hf": _member(base * _delayed(freqs, 500e-6)),
    }
    members = ["lf", "mf", "hf"]
    channels = {
        "lf": {"lp": _section("lr", 4, 300.0)},
        "mf": {"hp": _section("lr", 4, 300.0), "lp": _section("lr", 4, 3_000.0)},
        "hf": {"hp": _section("lr", 4, 3_000.0)},
    }
    top = combine_drive_channels(results, members=members, channels=channels)[1]
    assert top["reference"] == "hf"
    assert top["delays_ms"]["hf"] == pytest.approx(0.0, abs=1e-12)
    assert top["delays_ms"]["mf"] == pytest.approx(0.3, rel=1e-4)
    assert top["delays_ms"]["lf"] == pytest.approx(0.5, rel=1e-4)

    middle = combine_drive_channels(
        results, members=members, channels=channels, reference="mf"
    )[1]
    assert middle["reference"] == "mf"
    assert middle["delays_ms"]["mf"] == pytest.approx(0.0, abs=1e-12)
    assert middle["delays_ms"]["hf"] == pytest.approx(-0.3, rel=1e-4)
    assert middle["delays_ms"]["lf"] == pytest.approx(0.2, rel=1e-4)
    # Pinning only shifts the chain; the pair-to-pair differences hold.
    for name in members:
        assert middle["delays_ms"][name] - top["delays_ms"][name] == pytest.approx(
            -0.3, rel=1e-4
        )

    with pytest.raises(ValueError, match="is not a member"):
        combine_drive_channels(
            results, members=members, channels=channels, reference="sub"
        )


def test_an_unlinked_pair_reports_no_crossover_and_a_geometric_mean() -> None:
    freqs = _freqs()
    base = np.ones(freqs.size, dtype=np.complex128)
    results = {"low": _member(base), "high": _member(base)}
    payload = combine_drive_channels(
        results,
        members=["low", "high"],
        channels={
            "low": {"lp": _section("lr", 4, 800.0)},
            "high": {"hp": _section("lr", 4, 1_200.0)},
        },
    )[1]
    assert payload["crossovers_hz"] == [None]
    assert payload["pairs"]["low-high"]["eval_hz"] == pytest.approx(
        float(np.sqrt(800.0 * 1_200.0))
    )

    linked = combine_drive_channels(
        results, members=["low", "high"], channels=_two_way("lr", 4, 900.0)
    )[1]
    assert linked["crossovers_hz"] == [900.0]


def test_pair_metrics_show_the_alignment_and_its_reverse_null() -> None:
    freqs = _freqs()
    delay_s = 250e-6
    base = np.ones(freqs.size, dtype=np.complex128)
    results = {
        "low": _member(base),
        "high": _member(base * _delayed(freqs, delay_s)),
    }
    aligned = combine_drive_channels(
        results, members=["low", "high"], crossovers_hz=[1_000.0]
    )[1]["pairs"]["low-high"]
    assert aligned["fit_residual_deg"] == pytest.approx(0.0, abs=1e-6)
    assert aligned["phase_error_at_fc_deg"] == pytest.approx(0.0, abs=1e-6)
    assert aligned["points"] >= 3
    # The null depth the log grid can resolve, not an infinite one: the
    # crossover does not land on a sampled frequency.
    assert aligned["reverse_null_db"] < -20.0

    misaligned = combine_drive_channels(
        results,
        members=["low", "high"],
        channels=_two_way(low={"delay": {"mode": "manual", "ms": 0.0}}),
    )[1]["pairs"]["low-high"]
    # Left unaligned, the pair no longer cancels when one side is flipped.
    assert misaligned["reverse_null_db"] > aligned["reverse_null_db"] + 15.0
    assert abs(misaligned["phase_error_at_fc_deg"]) > 45.0


def test_a_coarse_window_without_points_warns_rather_than_guessing() -> None:
    freqs = np.asarray([500.0, 1_000.0, 20_000.0])
    base = np.ones(freqs.size, dtype=np.complex128)
    results = {
        "low": _member(base, freqs=freqs),
        "high": _member(base, freqs=freqs),
    }
    payload = combine_drive_channels(
        results, members=["low", "high"], crossovers_hz=[1_000.0]
    )[1]
    assert any("1/3-octave window" in warning for warning in payload["warnings"])


def test_channels_that_miss_a_member_refuse() -> None:
    base = np.ones(_freqs().size, dtype=np.complex128)
    results = {"low": _member(base), "high": _member(base)}
    with pytest.raises(ValueError, match="miss the members"):
        combine_drive_channels(
            results,
            members=["low", "high"],
            channels={"low": {"lp": _section("lr", 4, 1_000.0)}},
        )
    with pytest.raises(ValueError, match="crossovers_hz or channels"):
        combine_drive_channels(results, members=["low", "high"])
    with pytest.raises(ValueError, match="must sit below its low-pass"):
        combine_drive_channels(
            results,
            members=["low", "high"],
            channels={
                "low": {"lp": _section("lr", 4, 1_000.0)},
                "high": {
                    "hp": _section("lr", 4, 2_000.0),
                    "lp": _section("lr", 4, 1_500.0),
                },
            },
        )
