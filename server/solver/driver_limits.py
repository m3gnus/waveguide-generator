"""How loud one drive channel can be run before its driver stops it.

A solved driver-coupled channel already records everything a maximum-output
question needs, and none of it was ever read back: the one-way peak cone
excursion at the solve voltage, the electrical terminal impedance in ohms, and
the generator voltage and source resistance those two were solved at. The
channel's response is linear in the drive voltage, so the whole question is a
single scale factor per frequency:

    turn the channel up by ``s`` and excursion scales by ``s``, real power by
    ``s**2``, and terminal voltage by ``s``

which makes each ceiling one division:

    s_x(f) = Xmax / x(f)                one-way peak displacement vs the rating
    s_p(f) = sqrt(P_rated / P(f))       real power into the driver vs the rating
    s_v(f) = V_max / V(f)               generator EMF vs the amplifier

and the honest answer the smallest of whichever of them are known. Everything
here is small-signal: no thermal compression, no voice-coil heating, no
inductance nonlinearity, no programme material. It is the same ceiling the
``hornlab-research`` ``max_spl`` helper reports, evaluated against a solved
field instead of a lumped bandpass.

The excursion and power arrays this module divides are per-frequency swept-sine
values, so ``s(f)`` is a per-frequency ceiling; ``worst_case`` collapses it to
the one gain a channel can actually be set to, which is the smallest over the
band -- a single gain has to survive its worst frequency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

#: What stopped the channel at one frequency, or ``None`` where nothing did.
LimitReason = str

XMAX: LimitReason = "xmax"
POWER: LimitReason = "power"
VOLTAGE: LimitReason = "voltage"

#: Reported instead of a ratio when a channel has no ceiling at all. Finite so
#: it survives JSON, and far enough above any real headroom to read as "none".
_NO_LIMIT = float("inf")


@dataclass(frozen=True)
class MemberLimits:
    """One member's ceilings, on the combine's own frequency axis."""

    #: One-way peak cone displacement per frequency at ``drive_voltage_v``, mm.
    excursion_mm: np.ndarray | None
    #: Real power into the driver terminals per frequency at that voltage, W.
    power_w: np.ndarray | None
    xmax_mm: float | None
    #: Already multiplied by the driver count: parallel drivers share the
    #: channel's power the same way they share its impedance.
    rated_power_w: float | None
    drive_voltage_v: float | None
    max_voltage_v: float | None
    label: str | None = None

    @property
    def known(self) -> bool:
        """Whether any ceiling at all can be computed for this member."""

        return bool(
            (self.xmax_mm is not None and self.excursion_mm is not None)
            or (self.rated_power_w is not None and self.power_w is not None)
            or (self.max_voltage_v is not None and self.drive_voltage_v is not None)
        )


@dataclass(frozen=True)
class Headroom:
    """The scale a member may still be turned up by, per frequency."""

    #: Linear voltage scale before the first ceiling binds. ``inf`` where no
    #: ceiling is known, which sums into "this member never limits anything".
    scale: np.ndarray
    #: Which ceiling bound, per frequency; empty string where none did.
    reason: np.ndarray
    #: Peak fraction of each rating actually used, for a one-line readout.
    excursion_fraction: float | None
    power_fraction: float | None
    voltage_fraction: float | None

    def worst_case(self) -> tuple[float | None, LimitReason | None, float | None]:
        """The single scale a fixed gain must survive: smallest over the band.

        Returns the scale, what bound it, and the frequency index it bound at,
        or ``(None, None, None)`` when nothing limits this member anywhere.
        """

        finite = np.isfinite(self.scale)
        if not np.any(finite):
            return None, None, None
        index = int(np.argmin(np.where(finite, self.scale, np.inf)))
        reason = str(self.reason[index]) or None
        return float(self.scale[index]), reason, index


def _finite_array(value: Any) -> np.ndarray | None:
    if value is None:
        return None
    array = np.asarray(value, dtype=np.float64).reshape(-1)
    return array if array.size else None


def _positive(value: Any) -> float | None:
    number = float(value) if isinstance(value, (int, float)) else math.nan
    return number if math.isfinite(number) and number > 0.0 else None


def _resample(values: np.ndarray | None, source_hz: np.ndarray | None, target_hz: np.ndarray) -> np.ndarray | None:
    """Put a stored per-frequency array on the combine's frequency axis.

    A recombine reads arrays a solve wrote, so the two axes are the same one in
    practice. Interpolating anyway costs nothing and means a member whose grid
    was resampled between the two is a slightly blurred ceiling rather than a
    crash or, worse, a silently misaligned one.
    """

    if values is None:
        return None
    if source_hz is None or source_hz.size != values.size:
        return values if values.size == target_hz.size else None
    if source_hz.size == target_hz.size and np.array_equal(source_hz, target_hz):
        return values
    return np.interp(target_hz, source_hz, values)


def member_limits_from_channel(
    channel: Mapping[str, Any] | None,
    *,
    frequencies_hz: np.ndarray,
    max_voltage_v: float | None = None,
) -> MemberLimits | None:
    """Read one channel's ceilings out of its built result payload.

    Takes the whole channel rather than its metadata because the two halves of
    the answer are stored apart: the cone lives in ``metadata.driver`` while
    the terminal impedance is lifted out into the channel's own ``impedance``
    block, so that a driver-coupled channel charts ohms like any other result.

    Returns ``None`` when the channel carries no driver model at all -- an
    acceleration-driven waveguide channel has no cone and no terminals, so it
    has no maximum output to report and must not be given a fabricated one.
    """

    if not isinstance(channel, Mapping):
        return None
    metadata = channel.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    driver = metadata.get("driver")
    if not isinstance(driver, Mapping):
        return None
    target = np.asarray(frequencies_hz, dtype=np.float64).reshape(-1)

    spec = driver.get("spec")
    spec = spec if isinstance(spec, Mapping) else {}
    count = spec.get("count")
    count = int(count) if isinstance(count, (int, float)) and count >= 1 else 1
    rated_one = _positive(spec.get("power_w"))

    block = driver.get("cone_excursion_mm")
    block = block if isinstance(block, Mapping) else {}
    excursion = _resample(
        _finite_array(block.get("values")),
        _finite_array(block.get("frequencies")),
        target,
    )

    drive_voltage = _positive(driver.get("drive_voltage_v"))
    rg_ohm = driver.get("rg_ohm")
    rg_ohm = float(rg_ohm) if isinstance(rg_ohm, (int, float)) and math.isfinite(float(rg_ohm)) else 0.0
    # ``electrical_impedance_ohm`` is where the LEM leaves it; ``impedance`` is
    # where the response builder moves it to. Read both, so a channel from
    # either side of that move answers the same question.
    impedance = driver.get("electrical_impedance_ohm")
    if not isinstance(impedance, Mapping):
        impedance = channel.get("impedance")
    power = _terminal_power_w(
        impedance,
        frequencies_hz=target,
        drive_voltage_v=drive_voltage,
        rg_ohm=rg_ohm,
    )

    limits = MemberLimits(
        excursion_mm=excursion,
        power_w=power,
        xmax_mm=_positive(spec.get("xmax_mm")),
        rated_power_w=None if rated_one is None else rated_one * count,
        drive_voltage_v=drive_voltage,
        max_voltage_v=_positive(max_voltage_v),
        label=str(driver.get("label")) if driver.get("label") else None,
    )
    return limits if limits.known else None


def _terminal_power_w(
    impedance: Any,
    *,
    frequencies_hz: np.ndarray,
    drive_voltage_v: float | None,
    rg_ohm: float,
) -> np.ndarray | None:
    """Real power into the driver at the solve voltage, per frequency.

    The same generator algebra the drive-power chart uses, and for the same
    reason it is spelled out there: the stored impedance is the *terminal*
    impedance and excludes Rg, so the current is set by ``Z + Rg`` while the
    power is set by ``Re(Z)``. Dividing ``|V|^2`` by ``|Z|`` instead would
    overstate the power everywhere the load is reactive, which is most of the
    band -- and overstating the power understates the headroom.
    """

    if drive_voltage_v is None or not isinstance(impedance, Mapping):
        return None
    real = _finite_array(impedance.get("real"))
    imaginary = _finite_array(impedance.get("imaginary"))
    if real is None or imaginary is None or real.size != imaginary.size:
        return None
    source_hz = _finite_array(impedance.get("frequencies"))
    real = _resample(real, source_hz, frequencies_hz)
    imaginary = _resample(imaginary, source_hz, frequencies_hz)
    if real is None or imaginary is None:
        return None
    loop_squared = (real + rg_ohm) ** 2 + imaginary ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        current_squared = np.where(loop_squared > 0.0, drive_voltage_v ** 2 / loop_squared, 0.0)
    # A passive load cannot have negative resistance; a solver artefact that
    # produced one would otherwise read as a channel that generates power.
    return np.where(real > 0.0, current_squared * np.maximum(real, 0.0), 0.0)


def headroom(limits: MemberLimits, applied_magnitude: np.ndarray) -> Headroom:
    """How much further a member can be turned up, per frequency.

    ``applied_magnitude`` is the linear voltage factor the combine already
    applies to this member -- crossover filter magnitude times gain. Delay and
    polarity are excluded on purpose: neither changes how hard the cone works.
    """

    magnitude = np.abs(np.asarray(applied_magnitude, dtype=np.float64)).reshape(-1)
    scale = np.full(magnitude.shape, _NO_LIMIT, dtype=np.float64)
    reason = np.full(magnitude.shape, "", dtype=object)
    fractions: dict[str, float | None] = {XMAX: None, POWER: None, VOLTAGE: None}

    def apply(candidate: np.ndarray, tag: LimitReason, used: np.ndarray) -> None:
        finite = np.isfinite(candidate)
        binds = finite & (candidate < scale)
        scale[binds] = candidate[binds]
        reason[binds] = tag
        present = used[np.isfinite(used)]
        fractions[tag] = float(np.max(present)) if present.size else None

    with np.errstate(divide="ignore", invalid="ignore"):
        if limits.xmax_mm is not None and limits.excursion_mm is not None:
            used = limits.excursion_mm * magnitude
            apply(np.where(used > 0.0, limits.xmax_mm / used, _NO_LIMIT), XMAX, used / limits.xmax_mm)
        if limits.rated_power_w is not None and limits.power_w is not None:
            used = limits.power_w * magnitude ** 2
            apply(
                np.where(used > 0.0, np.sqrt(limits.rated_power_w / np.maximum(used, 0.0)), _NO_LIMIT),
                POWER,
                used / limits.rated_power_w,
            )
        if limits.max_voltage_v is not None and limits.drive_voltage_v is not None:
            used = limits.drive_voltage_v * magnitude
            apply(np.where(used > 0.0, limits.max_voltage_v / used, _NO_LIMIT), VOLTAGE, used / limits.max_voltage_v)

    return Headroom(
        scale=scale,
        reason=reason,
        excursion_fraction=fractions[XMAX],
        power_fraction=fractions[POWER],
        voltage_fraction=fractions[VOLTAGE],
    )


def gain_ceiling_db(limits: MemberLimits, band_magnitude: np.ndarray) -> tuple[float | None, LimitReason | None, int | None]:
    """The absolute gain, in dB, at which this member first hits a ceiling.

    ``band_magnitude`` is the crossover filter magnitude alone, so the answer
    is an absolute channel gain rather than an increment on whatever gain
    happens to be set -- setting the mode twice must not creep the level up.
    """

    scale, reason, index = headroom(limits, band_magnitude).worst_case()
    if scale is None or not (scale > 0.0):
        return None, None, None
    return 20.0 * math.log10(scale), reason, index


__all__ = [
    "Headroom",
    "MemberLimits",
    "POWER",
    "VOLTAGE",
    "XMAX",
    "gain_ceiling_db",
    "headroom",
    "member_limits_from_channel",
]
