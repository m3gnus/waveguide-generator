"""Maximum-output ceilings: Xmax, rated power, amplifier, and the sum of them."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from server.solver.combine import combine_drive_channels
from server.solver.driver_limits import (
    MemberLimits,
    gain_ceiling_db,
    headroom,
    member_limits_from_channel,
)


def _freqs() -> np.ndarray:
    return np.geomspace(100.0, 10_000.0, 64)


def _member(eng_on_axis: np.ndarray, freqs: np.ndarray | None = None) -> SimpleNamespace:
    angles = np.asarray([-30.0, 0.0, 30.0])
    planes = ["horizontal", "vertical"]
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
        sphere_pressure_complex=None,
        sphere_points=None,
        sphere_theta_deg=None,
        sphere_phi_deg=None,
    )


def _flat_limits(
    size: int,
    *,
    excursion_mm: float = 1.0,
    power_w: float = 1.0,
    xmax_mm: float | None = 4.0,
    rated_power_w: float | None = None,
    drive_voltage_v: float | None = 2.83,
    max_voltage_v: float | None = None,
) -> MemberLimits:
    return MemberLimits(
        excursion_mm=np.full(size, excursion_mm),
        power_w=np.full(size, power_w),
        xmax_mm=xmax_mm,
        rated_power_w=rated_power_w,
        drive_voltage_v=drive_voltage_v,
        max_voltage_v=max_voltage_v,
    )


def test_excursion_ceiling_is_the_ratio_of_xmax_to_displacement() -> None:
    limits = _flat_limits(8, excursion_mm=1.0, xmax_mm=4.0, rated_power_w=None)
    room = headroom(limits, np.ones(8))
    assert np.allclose(room.scale, 4.0)
    assert set(room.reason) == {"xmax"}
    # 12.04 dB is exactly a factor of four in voltage.
    ceiling_db, reason, index = gain_ceiling_db(limits, np.ones(8))
    assert ceiling_db == pytest.approx(20.0 * np.log10(4.0))
    assert reason == "xmax"
    assert index == 0


def test_power_ceiling_takes_the_square_root_because_power_goes_as_voltage_squared() -> None:
    limits = _flat_limits(8, power_w=1.0, xmax_mm=None, rated_power_w=100.0)
    room = headroom(limits, np.ones(8))
    assert np.allclose(room.scale, 10.0)
    assert set(room.reason) == {"power"}


def test_the_smallest_ceiling_is_the_one_reported() -> None:
    limits = _flat_limits(
        8,
        excursion_mm=1.0,
        xmax_mm=4.0,
        power_w=1.0,
        rated_power_w=100.0,
        drive_voltage_v=2.83,
        max_voltage_v=28.3,
    )
    room = headroom(limits, np.ones(8))
    # Xmax allows x4, power allows x10, the amplifier allows x10: Xmax wins.
    assert np.allclose(room.scale, 4.0)
    assert set(room.reason) == {"xmax"}
    assert room.excursion_fraction == pytest.approx(0.25)
    assert room.power_fraction == pytest.approx(0.01)
    assert room.voltage_fraction == pytest.approx(0.1)


def test_a_crossover_that_attenuates_a_member_buys_it_headroom() -> None:
    limits = _flat_limits(8, excursion_mm=1.0, xmax_mm=4.0, rated_power_w=None)
    # Half the voltage through the filter is twice the room before Xmax.
    room = headroom(limits, np.full(8, 0.5))
    assert np.allclose(room.scale, 8.0)


def test_a_member_with_nothing_to_limit_it_never_binds() -> None:
    limits = MemberLimits(
        excursion_mm=np.ones(8),
        power_w=np.ones(8),
        xmax_mm=None,
        rated_power_w=None,
        drive_voltage_v=2.83,
        max_voltage_v=None,
    )
    assert limits.known is False
    scale, reason, index = headroom(limits, np.ones(8)).worst_case()
    assert (scale, reason, index) == (None, None, None)


def test_worst_case_is_the_frequency_the_driver_works_hardest_at() -> None:
    excursion = np.array([1.0, 1.0, 8.0, 1.0])
    limits = MemberLimits(
        excursion_mm=excursion,
        power_w=None,
        xmax_mm=4.0,
        rated_power_w=None,
        drive_voltage_v=2.83,
        max_voltage_v=None,
    )
    scale, reason, index = headroom(limits, np.ones(4)).worst_case()
    assert scale == pytest.approx(0.5)
    assert reason == "xmax"
    assert index == 2


def _channel(
    freqs: np.ndarray,
    *,
    excursion_mm: float,
    re_ohm: float,
    xmax_mm: float | None,
    power_w: float | None,
    count: int = 1,
) -> dict:
    spec: dict = {"sd_cm2": 200.0, "bl_t_m": 12.0, "re_ohm": re_ohm, "count": count}
    if xmax_mm is not None:
        spec["xmax_mm"] = xmax_mm
    if power_w is not None:
        spec["power_w"] = power_w
    return {
        "impedance": {
            "frequencies": freqs.tolist(),
            "real": np.full(freqs.size, re_ohm).tolist(),
            "imaginary": np.zeros(freqs.size).tolist(),
        },
        "metadata": {
            "driver": {
                "drive_voltage_v": 2.83,
                "rg_ohm": 0.0,
                "spec": spec,
                "cone_excursion_mm": {
                    "frequencies": freqs.tolist(),
                    "values": np.full(freqs.size, excursion_mm).tolist(),
                    "peak_mm": excursion_mm,
                },
            },
        },
    }


def test_limits_are_read_from_the_channel_the_solve_built() -> None:
    freqs = _freqs()
    channel = _channel(freqs, excursion_mm=0.5, re_ohm=8.0, xmax_mm=5.0, power_w=100.0)
    limits = member_limits_from_channel(channel, frequencies_hz=freqs, max_voltage_v=40.0)
    assert limits is not None
    assert limits.xmax_mm == 5.0
    assert limits.rated_power_w == 100.0
    assert limits.max_voltage_v == 40.0
    # 2.83 V into 8 ohm resistive is the rounded one-watt reference.
    assert limits.power_w is not None
    assert limits.power_w[0] == pytest.approx(2.83 ** 2 / 8.0)


def test_parallel_drivers_share_the_channel_power_rating() -> None:
    freqs = _freqs()
    channel = _channel(
        freqs, excursion_mm=0.5, re_ohm=8.0, xmax_mm=5.0, power_w=100.0, count=2
    )
    limits = member_limits_from_channel(channel, frequencies_hz=freqs)
    assert limits is not None
    assert limits.rated_power_w == 200.0


def test_a_channel_with_no_driver_model_has_no_ceiling() -> None:
    freqs = _freqs()
    assert member_limits_from_channel({"metadata": {}}, frequencies_hz=freqs) is None
    assert member_limits_from_channel(None, frequencies_hz=freqs) is None


def test_combine_resolves_a_max_gain_and_reports_what_bound_it() -> None:
    freqs = _freqs()
    results = {
        "lf": _member(np.ones(freqs.size, dtype=np.complex128)),
        "hf": _member(np.ones(freqs.size, dtype=np.complex128)),
    }
    channels = {
        "lf": {
            "hp": None,
            "lp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "gain": {"mode": "max"},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
        "hf": {
            "hp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "lp": None,
            "gain": {"mode": "manual", "db": 0.0},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
    }
    limits = {
        "lf": _flat_limits(freqs.size, excursion_mm=1.0, xmax_mm=4.0, rated_power_w=None),
        "hf": _flat_limits(
            freqs.size, excursion_mm=1.0, xmax_mm=None, power_w=1.0, rated_power_w=100.0
        ),
    }
    _, payload = combine_drive_channels(
        results,
        members=["lf", "hf"],
        channels=channels,
        reference="hf",
        member_limits=limits,
    )
    lf = payload["channels"]["lf"]
    # The low-pass passband is unity, so the ceiling is the flat Xmax ratio.
    assert lf["gain_max_db"] == pytest.approx(20.0 * np.log10(4.0), abs=0.01)
    assert lf["max_limit"] == "xmax"
    assert lf["gain_db"] == pytest.approx(lf["gain_max_db"])
    # A member left on manual still reports the ceiling it is not spending.
    hf = payload["channels"]["hf"]
    assert hf["gain_db"] == 0.0
    assert hf["gain_max_db"] == pytest.approx(20.0, abs=0.01)
    assert hf["max_limit"] == "power"


def test_max_gain_is_absolute_so_applying_it_twice_does_not_creep() -> None:
    freqs = _freqs()
    results = {
        "lf": _member(np.ones(freqs.size, dtype=np.complex128)),
        "hf": _member(np.ones(freqs.size, dtype=np.complex128)),
    }

    def spec(gain: dict) -> dict:
        return {
            "lf": {
                "hp": None,
                "lp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
                "gain": gain,
                "delay": {"mode": "manual", "ms": 0.0},
                "invert": False,
            },
            "hf": {
                "hp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
                "lp": None,
                "gain": {"mode": "manual", "db": 0.0},
                "delay": {"mode": "manual", "ms": 0.0},
                "invert": False,
            },
        }

    limits = {
        "lf": _flat_limits(freqs.size, excursion_mm=1.0, xmax_mm=4.0, rated_power_w=None),
    }
    _, first = combine_drive_channels(
        results, members=["lf", "hf"], channels=spec({"mode": "max"}),
        reference="hf", member_limits=limits,
    )
    _, second = combine_drive_channels(
        results, members=["lf", "hf"], channels=spec({"mode": "max"}),
        reference="hf", member_limits=limits,
    )
    assert first["channels"]["lf"]["gain_db"] == pytest.approx(
        second["channels"]["lf"]["gain_db"]
    )


def test_max_gain_without_a_driver_limit_warns_and_stays_at_zero() -> None:
    freqs = _freqs()
    results = {
        "lf": _member(np.ones(freqs.size, dtype=np.complex128)),
        "hf": _member(np.ones(freqs.size, dtype=np.complex128)),
    }
    channels = {
        "lf": {
            "hp": None,
            "lp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "gain": {"mode": "max"},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
        "hf": {
            "hp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "lp": None,
            "gain": {"mode": "manual", "db": 0.0},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
    }
    _, payload = combine_drive_channels(
        results, members=["lf", "hf"], channels=channels, reference="hf"
    )
    assert payload["channels"]["lf"]["gain_db"] == 0.0
    assert payload["channels"]["lf"]["gain_max_db"] is None
    assert any("maximum gain" in warning for warning in payload["warnings"])
    assert "max_output" not in payload


def test_the_combined_max_spl_follows_the_member_with_the_least_headroom() -> None:
    freqs = _freqs()
    results = {
        "lf": _member(np.ones(freqs.size, dtype=np.complex128)),
        "hf": _member(np.ones(freqs.size, dtype=np.complex128)),
    }
    channels = {
        "lf": {
            "hp": None,
            "lp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "gain": {"mode": "manual", "db": 0.0},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
        "hf": {
            "hp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "lp": None,
            "gain": {"mode": "manual", "db": 0.0},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
    }
    limits = {
        # The LF driver is the weak one: 2x before Xmax against the HF's 10x.
        "lf": _flat_limits(freqs.size, excursion_mm=2.0, xmax_mm=4.0, rated_power_w=None),
        "hf": _flat_limits(
            freqs.size, excursion_mm=0.4, xmax_mm=4.0, rated_power_w=None
        ),
    }
    _, payload = combine_drive_channels(
        results, members=["lf", "hf"], channels=channels,
        reference="hf", member_limits=limits,
    )
    max_output = payload["max_output"]
    limiting = max_output["combined"]["limiting_member"]
    headroom_db = np.asarray(max_output["combined"]["headroom_db"], dtype=float)
    low = freqs < 400.0
    high = freqs > 3000.0
    # Well below the crossover the LF carries the band and holds the system to
    # its own 6 dB; well above it the HF does, with its 20 dB.
    assert {limiting[index] for index in np.flatnonzero(low)} == {"lf"}
    assert {limiting[index] for index in np.flatnonzero(high)} == {"hf"}
    assert headroom_db[low].max() == pytest.approx(20.0 * np.log10(2.0), abs=0.2)
    assert headroom_db[high].max() == pytest.approx(20.0 * np.log10(10.0), abs=0.2)


def test_max_output_arrays_hold_no_infinities() -> None:
    freqs = _freqs()
    results = {
        "lf": _member(np.ones(freqs.size, dtype=np.complex128)),
        "hf": _member(np.ones(freqs.size, dtype=np.complex128)),
    }
    channels = {
        "lf": {
            "hp": None,
            "lp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "gain": {"mode": "manual", "db": 0.0},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
        "hf": {
            "hp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "lp": None,
            "gain": {"mode": "manual", "db": 0.0},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
    }
    # Only the LF has a ceiling, so above the crossover nothing limits the sum.
    limits = {
        "lf": _flat_limits(freqs.size, excursion_mm=1.0, xmax_mm=4.0, rated_power_w=None),
    }
    _, payload = combine_drive_channels(
        results, members=["lf", "hf"], channels=channels,
        reference="hf", member_limits=limits,
    )
    for block in (
        payload["max_output"]["combined"],
        payload["max_output"]["members"]["lf"],
    ):
        for key in ("spl_max_db", "headroom_db"):
            assert all(
                value is None or np.isfinite(value) for value in block[key]
            ), f"{key} must be JSON-safe"


def test_an_unrated_member_makes_the_system_maximum_unknown_where_it_plays() -> None:
    freqs = _freqs()
    results = {
        "lf": _member(np.ones(freqs.size, dtype=np.complex128)),
        "hf": _member(np.ones(freqs.size, dtype=np.complex128)),
    }
    channels = {
        "lf": {
            "hp": None,
            "lp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "gain": {"mode": "manual", "db": 0.0},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
        "hf": {
            "hp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "lp": None,
            "gain": {"mode": "manual", "db": 0.0},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
    }
    # A compression driver with no Xmax and no rated power, which is what the
    # driver library actually publishes for most of them.
    limits = {
        "lf": _flat_limits(freqs.size, excursion_mm=1.0, xmax_mm=4.0, rated_power_w=None),
    }
    _, payload = combine_drive_channels(
        results, members=["lf", "hf"], channels=channels,
        reference="hf", member_limits=limits,
    )
    combined = payload["max_output"]["combined"]
    headroom_db = np.asarray(
        [np.nan if value is None else value for value in combined["headroom_db"]],
        dtype=float,
    )
    low = freqs < 300.0
    high = freqs > 3000.0
    # Below the crossover the rated woofer carries it and the answer is known.
    assert np.all(np.isfinite(headroom_db[low]))
    assert {combined["limiting_member"][i] for i in np.flatnonzero(low)} == {"lf"}
    # Above it the unrated driver sets the level, so there is no answer -- and
    # emphatically not the enormous one an out-of-band woofer would give.
    assert np.all(np.isnan(headroom_db[high]))
    assert {combined["limit"][i] for i in np.flatnonzero(high)} == {None}
    assert payload["max_output"]["unlimited_members"] == ["hf"]
    assert any("maximum output is unknown" in warning for warning in payload["warnings"])


def test_a_member_trace_stops_where_the_member_stops_playing() -> None:
    freqs = _freqs()
    results = {
        "lf": _member(np.ones(freqs.size, dtype=np.complex128)),
        "hf": _member(np.ones(freqs.size, dtype=np.complex128)),
    }
    channels = {
        "lf": {
            "hp": None,
            "lp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "gain": {"mode": "manual", "db": 0.0},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
        "hf": {
            "hp": {"family": "lr", "order": 4, "fc_hz": 1000.0},
            "lp": None,
            "gain": {"mode": "manual", "db": 0.0},
            "delay": {"mode": "manual", "ms": 0.0},
            "invert": False,
        },
    }
    limits = {
        "lf": _flat_limits(freqs.size, excursion_mm=1.0, xmax_mm=4.0, rated_power_w=None),
        "hf": _flat_limits(freqs.size, excursion_mm=1.0, xmax_mm=4.0, rated_power_w=None),
    }
    _, payload = combine_drive_channels(
        results, members=["lf", "hf"], channels=channels,
        reference="hf", member_limits=limits,
    )
    lf = payload["max_output"]["members"]["lf"]
    values = np.asarray(
        [np.nan if value is None else value for value in lf["spl_max_db"]], dtype=float
    )
    # Inside its own band the woofer's ceiling is a number.
    assert np.all(np.isfinite(values[freqs < 300.0]))
    # Two decades above its low-pass the cone barely moves, so the arithmetic
    # would grant it an absurd level. It is not reported at all.
    assert np.all(np.isnan(values[freqs > 5_000.0]))
    assert {lf["limit"][i] for i in np.flatnonzero(freqs > 5_000.0)} == {None}
