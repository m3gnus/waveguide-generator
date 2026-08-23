"""Crossover filter families for the drive-channel combine.

Every transfer function here is stated in the engineering ``e^{+jωt}``
convention with the normalised Laplace variable ``s = j f / fc``. That is the
same convention ``server/solver/combine.py`` uses for its weights, so a
response from this module can be multiplied straight onto an engineering
spectrum; the conjugation onto raw ``exp(+ikr)`` solver fields happens once,
in ``combine.raw_channel_weights``.

Definitions (CADLINK-CROSSOVER-DRIVERS.md §2):

- Butterworth order ``n``: poles ``exp(jπ(2k+n−1)/(2n))`` for ``k = 1..n``,
  ``H_lp(s) = Π 1/(s − p_k)``.
- Linkwitz-Riley order ``n`` (even): ``H_lp = Butterworth_{n/2}²``.
- Bessel order ``n``: the analog Bessel low-pass normalised to −3 dB at
  ``fc`` (``scipy.signal.bessel(..., analog=True, norm="mag")``).
- Linear phase order ``n``: ``|LR_n|`` with zero phase.
- High-pass of any family: ``H_hp(s) = H_lp(1/s)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
from typing import Any, Mapping

import numpy as np

# The registry: a family and the orders it is offered in. Anything outside
# this table refuses at the wire, so the solver never has to guess what a
# "Bessel 7" would be.
FAMILY_ORDERS: dict[str, tuple[int, ...]] = {
    "lr": (2, 4, 6, 8),
    "butterworth": (1, 2, 3, 4, 5, 6, 7, 8),
    "bessel": (2, 3, 4),
    "linear_phase": (2, 4, 8),
}

FAMILY_LABELS: dict[str, str] = {
    "lr": "Linkwitz-Riley",
    "butterworth": "Butterworth",
    "bessel": "Bessel",
    "linear_phase": "Linear phase",
}

FAMILIES: tuple[str, ...] = tuple(FAMILY_ORDERS)


def validate_family_order(family: str, order: int) -> tuple[str, int]:
    """Return the normalised ``(family, order)`` or raise ``ValueError``."""

    normalized = str(family).strip().lower()
    if normalized not in FAMILY_ORDERS:
        raise ValueError(
            f"unknown filter family {family!r}; known families are "
            f"{', '.join(sorted(FAMILY_ORDERS))}"
        )
    try:
        as_int = int(order)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"filter order must be an integer, got {order!r}") from exc
    allowed = FAMILY_ORDERS[normalized]
    if as_int not in allowed:
        raise ValueError(
            f"filter family {normalized!r} supports orders "
            f"{', '.join(str(value) for value in allowed)}, not {as_int}"
        )
    return normalized, as_int


@dataclass(frozen=True)
class Filter:
    """One high-pass or low-pass section: family, order and corner."""

    family: str
    order: int
    fc_hz: float

    def __post_init__(self) -> None:
        family, order = validate_family_order(self.family, self.order)
        fc = float(self.fc_hz)
        if not math.isfinite(fc) or fc <= 0.0:
            raise ValueError("filter fc_hz must be finite and positive")
        object.__setattr__(self, "family", family)
        object.__setattr__(self, "order", order)
        object.__setattr__(self, "fc_hz", fc)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "Filter | None":
        if value is None:
            return None
        if isinstance(value, Filter):
            return value
        try:
            family = value["family"]
            order = value["order"]
            fc_hz = value["fc_hz"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                "a filter section needs family, order and fc_hz"
            ) from exc
        return cls(family=family, order=order, fc_hz=fc_hz)

    def as_payload(self) -> dict[str, Any]:
        return {"family": self.family, "order": self.order, "fc_hz": float(self.fc_hz)}


@lru_cache(maxsize=64)
def _butterworth_ba(order: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    k = np.arange(1, order + 1, dtype=np.float64)
    poles = np.exp(1j * np.pi * (2.0 * k + order - 1.0) / (2.0 * order))
    denominator = np.real(np.poly(poles))
    return (1.0,), tuple(float(value) for value in denominator)


@lru_cache(maxsize=64)
def _lowpass_ba(family: str, order: int) -> tuple[np.ndarray, np.ndarray]:
    """Low-pass ``(numerator, denominator)`` coefficients, highest power first."""

    if family == "butterworth":
        num, den = _butterworth_ba(order)
        return np.asarray(num, dtype=np.float64), np.asarray(den, dtype=np.float64)
    if family in ("lr", "linear_phase"):
        num, den = _butterworth_ba(order // 2)
        return (
            np.convolve(np.asarray(num, dtype=np.float64), np.asarray(num, dtype=np.float64)),
            np.convolve(np.asarray(den, dtype=np.float64), np.asarray(den, dtype=np.float64)),
        )
    if family == "bessel":
        from scipy.signal import bessel  # imported lazily: scipy is slow to load

        num, den = bessel(order, 1.0, btype="low", analog=True, norm="mag", output="ba")
        return np.asarray(num, dtype=np.float64), np.asarray(den, dtype=np.float64)
    raise ValueError(f"unknown filter family {family!r}")


def _evaluate(coefficients: np.ndarray, s: np.ndarray) -> np.ndarray:
    return np.polyval(coefficients.astype(np.complex128), s)


def _response(family: str, order: int, s: np.ndarray) -> np.ndarray:
    num, den = _lowpass_ba(family, order)
    return _evaluate(num, s) / _evaluate(den, s)


def lowpass(
    freqs: np.ndarray, family: str, order: int, fc_hz: float
) -> np.ndarray:
    """Low-pass response of ``family``/``order`` at ``freqs`` (engineering)."""

    family, order = validate_family_order(family, order)
    frequencies = np.asarray(freqs, dtype=np.float64)
    s = 1j * frequencies / float(fc_hz)
    response = _response(family, order, s)
    if family == "linear_phase":
        return np.abs(response).astype(np.complex128)
    return response.astype(np.complex128)


def highpass(
    freqs: np.ndarray, family: str, order: int, fc_hz: float
) -> np.ndarray:
    """High-pass response: the family's low-pass evaluated at ``1/s``."""

    family, order = validate_family_order(family, order)
    frequencies = np.asarray(freqs, dtype=np.float64)
    s = 1j * frequencies / float(fc_hz)
    nonzero = frequencies != 0.0
    response = np.zeros(frequencies.shape, dtype=np.complex128)
    if np.any(nonzero):
        response[nonzero] = _response(family, order, 1.0 / s[nonzero])
    if family == "linear_phase":
        return np.abs(response).astype(np.complex128)
    return response


def evaluate(freqs: np.ndarray, section: Filter, kind: str) -> np.ndarray:
    """Evaluate one section; ``kind`` is ``"lp"`` or ``"hp"``."""

    if kind == "lp":
        return lowpass(freqs, section.family, section.order, section.fc_hz)
    if kind == "hp":
        return highpass(freqs, section.family, section.order, section.fc_hz)
    raise ValueError(f"filter kind must be 'lp' or 'hp', not {kind!r}")


def channel_weight(
    freqs: np.ndarray, *, hp: Filter | None = None, lp: Filter | None = None
) -> np.ndarray:
    """The band-limiting weight of one channel: its high-pass times low-pass."""

    frequencies = np.asarray(freqs, dtype=np.float64)
    weight = np.ones(frequencies.shape, dtype=np.complex128)
    if hp is not None:
        weight = weight * evaluate(frequencies, hp, "hp")
    if lp is not None:
        weight = weight * evaluate(frequencies, lp, "lp")
    return weight


def pair_inverts(lp: Filter | None, hp: Filter | None, eval_hz: float) -> bool:
    """Whether the ideal pair sums better with the upper member inverted.

    ``|LP(fc) − HP(fc)| > |LP(fc) + HP(fc)|`` at the pair's evaluation
    frequency: LR2, LR6, BW2 and BW6 answer yes; LR4, LR8, BW1 and BW3 no.
    """

    if lp is None or hp is None:
        return False
    point = np.asarray([float(eval_hz)], dtype=np.float64)
    low = complex(evaluate(point, lp, "lp")[0])
    high = complex(evaluate(point, hp, "hp")[0])
    return abs(low - high) > abs(low + high)
