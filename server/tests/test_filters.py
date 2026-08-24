"""Crossover filter families against their textbook sums.

Every oracle here is a statement about the *ideal* pair — two coincident,
flat drivers — so a failure means the transfer function is wrong, not that
the alignment or the level match drifted.
"""

from __future__ import annotations

import numpy as np
import pytest

from server.solver import filters
from server.solver.combine import _lr4_highpass, _lr4_lowpass


def _freqs() -> np.ndarray:
    return np.geomspace(20.0, 50_000.0, 2001)


FC = 1_000.0


def _pair(family: str, order: int) -> tuple[np.ndarray, np.ndarray]:
    freqs = _freqs()
    return (
        filters.lowpass(freqs, family, order, FC),
        filters.highpass(freqs, family, order, FC),
    )


def _db(values: np.ndarray) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(np.abs(values), 1.0e-30))


def _at_fc(values: np.ndarray) -> complex:
    return complex(values[int(np.argmin(np.abs(_freqs() - FC)))])


def _db_at_fc(values: np.ndarray) -> float:
    return 20.0 * np.log10(max(abs(_at_fc(values)), 1.0e-30))


def test_the_registry_states_the_offered_orders() -> None:
    assert filters.FAMILY_ORDERS["lr"] == (2, 4, 6, 8)
    assert filters.FAMILY_ORDERS["butterworth"] == (1, 2, 3, 4, 5, 6, 7, 8)
    assert filters.FAMILY_ORDERS["bessel"] == (2, 3, 4)
    assert filters.FAMILY_ORDERS["linear_phase"] == (2, 4, 8)


@pytest.mark.parametrize(
    "family,order",
    [("lr", 3), ("lr", 10), ("butterworth", 9), ("bessel", 5), ("linear_phase", 6)],
)
def test_an_unoffered_order_refuses(family: str, order: int) -> None:
    with pytest.raises(ValueError, match="supports orders"):
        filters.validate_family_order(family, order)


def test_an_unknown_family_refuses() -> None:
    with pytest.raises(ValueError, match="unknown filter family"):
        filters.validate_family_order("chebyshev", 4)


def test_lr_order_four_reproduces_the_legacy_pair() -> None:
    freqs = _freqs()
    assert np.allclose(
        filters.lowpass(freqs, "lr", 4, FC), _lr4_lowpass(freqs, FC), atol=1e-12
    )
    assert np.allclose(
        filters.highpass(freqs, "lr", 4, FC), _lr4_highpass(freqs, FC), atol=1e-12
    )


@pytest.mark.parametrize("order", [4, 8])
def test_lr4_and_lr8_sum_in_phase_to_zero_db(order: int) -> None:
    low, high = _pair("lr", order)
    assert np.allclose(np.abs(low + high), 1.0, atol=1e-9)
    assert _db_at_fc(low) == pytest.approx(-6.021, abs=1e-3)


def test_lr2_sums_flat_with_one_side_inverted() -> None:
    low, high = _pair("lr", 2)
    assert np.allclose(np.abs(low - high), 1.0, atol=1e-9)
    # In phase it is a null at the crossover, which is why it needs the flip.
    assert _db_at_fc(low + high) < -100.0


def test_bw2_needs_the_flip_and_then_peaks_three_db() -> None:
    # The plan groups BW2 with LR2 as "needs one side inverted". That is true
    # of the null: in phase BW2 cancels at fc. Inverted it does not sum flat
    # like LR2 does — the textbook result is +3 dB at fc and never below the
    # passband — so that is what is asserted here.
    low, high = _pair("butterworth", 2)
    assert _db_at_fc(low + high) < -100.0
    inverted = low - high
    assert _db_at_fc(inverted) == pytest.approx(3.0103, abs=1e-3)
    assert _db(inverted).min() >= -1e-6


@pytest.mark.parametrize("order", [1, 3])
def test_bw1_and_bw3_sum_flat_with_a_ninety_degree_offset(order: int) -> None:
    low, high = _pair("butterworth", order)
    assert np.allclose(np.abs(low + high), 1.0, atol=1e-9)
    offset = np.degrees(np.angle(_at_fc(low) / _at_fc(high)))
    assert abs(offset) == pytest.approx(90.0, abs=1e-6)


def test_bw4_sums_three_db_up_at_the_crossover() -> None:
    low, high = _pair("butterworth", 4)
    assert _db_at_fc(low + high) == pytest.approx(3.0103, abs=1e-3)


def test_be4_dips_at_the_crossover() -> None:
    low, high = _pair("bessel", 4)
    assert _db_at_fc(low + high) == pytest.approx(-2.79, abs=0.05)


@pytest.mark.parametrize("order", [2, 3, 4])
def test_bessel_is_three_db_down_at_its_corner(order: int) -> None:
    low, _high = _pair("bessel", order)
    assert _db_at_fc(low) == pytest.approx(-3.0103, abs=1e-3)


@pytest.mark.parametrize("order", [2, 4, 8])
def test_linear_phase_has_lr_magnitude_and_no_phase(order: int) -> None:
    freqs = _freqs()
    low = filters.lowpass(freqs, "linear_phase", order, FC)
    high = filters.highpass(freqs, "linear_phase", order, FC)
    assert np.allclose(np.angle(low), 0.0, atol=1e-12)
    assert np.allclose(np.angle(high), 0.0, atol=1e-12)
    assert np.allclose(
        np.abs(low), np.abs(filters.lowpass(freqs, "lr", order, FC)), atol=1e-12
    )
    assert np.allclose(np.abs(low + high), 1.0, atol=1e-9)


def test_highpass_is_the_lowpass_at_the_reciprocal_variable() -> None:
    # H_hp(f) = H_lp(fc^2 / f) is the same statement as H_hp(s) = H_lp(1/s).
    freqs = np.geomspace(50.0, 20_000.0, 257)
    for family, order in (("lr", 6), ("butterworth", 5), ("bessel", 3)):
        mirrored = filters.lowpass(FC * FC / freqs, family, order, FC)
        assert np.allclose(
            np.abs(filters.highpass(freqs, family, order, FC)),
            np.abs(mirrored),
            atol=1e-12,
        )


def test_a_channel_weight_multiplies_its_sections() -> None:
    freqs = _freqs()
    hp = filters.Filter("lr", 4, 300.0)
    lp = filters.Filter("butterworth", 3, 3_000.0)
    weight = filters.channel_weight(freqs, hp=hp, lp=lp)
    expected = filters.highpass(freqs, "lr", 4, 300.0) * filters.lowpass(
        freqs, "butterworth", 3, 3_000.0
    )
    assert np.allclose(weight, expected, atol=1e-12)
    assert np.allclose(filters.channel_weight(freqs), 1.0, atol=0.0)


@pytest.mark.parametrize(
    "family,order,expected",
    [
        ("lr", 2, True),
        ("lr", 4, False),
        ("lr", 6, True),
        ("lr", 8, False),
        ("butterworth", 1, False),
        ("butterworth", 2, True),
        ("butterworth", 3, False),
        ("butterworth", 4, False),
        ("linear_phase", 4, False),
    ],
)
def test_ideal_pair_polarity_matches_the_textbook(
    family: str, order: int, expected: bool
) -> None:
    section = filters.Filter(family, order, FC)
    assert filters.pair_inverts(section, section, FC) is expected


def test_a_filter_normalises_and_refuses_a_bad_corner() -> None:
    section = filters.Filter("LR", 4, 1_000)
    assert section.family == "lr"
    assert section.as_payload() == {"family": "lr", "order": 4, "fc_hz": 1000.0}
    with pytest.raises(ValueError, match="finite and positive"):
        filters.Filter("lr", 4, 0.0)
    with pytest.raises(ValueError, match="family, order and fc_hz"):
        filters.Filter.from_mapping({"family": "lr", "order": 4})
    assert filters.Filter.from_mapping(None) is None
