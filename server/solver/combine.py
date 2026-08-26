"""Combine solved drive-channel bases into one summed response.

Ports the Fusion add-in's LR4 time-aligned on-axis sum
(``solve_fusion_wg_metal.py`` — crossover weights, level match,
phase-equivalent alignment delays) onto WG's solve-time channel results, then
generalises it: any channel may carry its own high-pass and low-pass section
from any supported family, its own gain and delay (auto or manual) and its own
polarity (CADLINK-CROSSOVER-DRIVERS.md §2). A legacy LR4 spec expands into
that per-channel form, so the two paths are one code path.
Because every channel of one job shares a single observation grid, the
add-in's grid harmonisation stage has no equivalent here.

Convention boundary (the only one): weights are defined in the engineering
``e^{+jωt}`` convention, where a filter transfer function and a delay
``e^{-jωτ}`` mean what filter theory says they mean. Raw solver fields are
``e^{-iωt}`` (``exp(+ikr)``), so a weight is applied to a raw field as its
complex conjugate. See CAD-LINK-PHASE3.md §2.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import json
import math
from types import SimpleNamespace
from typing import Any, Mapping

import numpy as np

from .driver_limits import MemberLimits, gain_ceiling_db, headroom
from .filters import Filter, channel_weight, pair_inverts

_TWO_PI = 2.0 * np.pi

# Alignment tuning (CADLINK-CROSSOVER-DRIVERS.md §2 "Alignment").
_OVERLAP_FLOOR_DB = -40.0
_COARSE_HALF_OCTAVE = 1.0 / 3.0
_RESIDUAL_WARN_DEG = 30.0
_MIN_FIT_POINTS = 3
# The payload is serialized with allow_nan=False, so a perfect reverse null
# reports as a floor rather than as -inf.
_REVERSE_NULL_FLOOR_DB = -300.0

# Serialized per-channel complex bases (persisted with a job) so a new
# crossover can recombine in milliseconds without re-solving. The fields stay
# in the solver's exp(+ikr) convention and the file says so.
CHANNEL_BASES_VERSION = 1
_BASES_PHASE_CONVENTION = "solver_exp_plus_ikr"


def serialize_channel_bases(
    results_by_id: Mapping[str, Any],
    *,
    metadata_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> bytes:
    """Pack the frequency-sorted native channel results into a compressed NPZ."""

    channel_ids = list(results_by_id)
    base = results_by_id[channel_ids[0]]
    arrays: dict[str, np.ndarray] = {
        "version": np.asarray(CHANNEL_BASES_VERSION),
        "phase_convention": np.asarray(_BASES_PHASE_CONVENTION),
        "channel_ids": np.asarray(channel_ids, dtype=str),
        "frequencies_hz": np.asarray(base.frequencies_hz, dtype=np.float64).reshape(-1),
        "observation_angles_deg": np.asarray(base.observation_angles_deg, dtype=np.float64),
        "observation_planes": np.asarray(list(base.observation_planes), dtype=str),
    }
    spheres_present = all(
        getattr(results_by_id[name], "sphere_pressure_complex", None) is not None
        for name in channel_ids
    )
    if spheres_present:
        arrays["sphere_theta_deg"] = np.asarray(base.sphere_theta_deg, dtype=np.float64)
        arrays["sphere_phi_deg"] = np.asarray(base.sphere_phi_deg, dtype=np.float64)
    for name in channel_ids:
        result = results_by_id[name]
        arrays[f"pressure::{name}"] = np.asarray(
            result.pressure_complex, dtype=np.complex128
        )
        if spheres_present:
            arrays[f"sphere::{name}"] = np.asarray(
                result.sphere_pressure_complex, dtype=np.complex128
            )
        metadata = (metadata_by_id or {}).get(name)
        if metadata:
            arrays[f"metadata::{name}"] = np.asarray(
                json.dumps(dict(metadata), sort_keys=True, separators=(",", ":"))
            )
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    return buffer.getvalue()


def deserialize_channel_bases(data: bytes) -> dict[str, Any]:
    """Rebuild the duck-typed channel results ``combine_drive_channels`` reads."""

    with np.load(io.BytesIO(data)) as bundle:
        version = int(bundle["version"])
        if version != CHANNEL_BASES_VERSION:
            raise ValueError(f"unsupported channel-bases version {version}")
        convention = str(bundle["phase_convention"])
        if convention != _BASES_PHASE_CONVENTION:
            raise ValueError(f"unexpected channel-bases phase convention {convention!r}")
        channel_ids = [str(name) for name in bundle["channel_ids"]]
        frequencies = bundle["frequencies_hz"]
        angles = bundle["observation_angles_deg"]
        planes = [str(name) for name in bundle["observation_planes"]]
        sphere_theta = bundle["sphere_theta_deg"] if "sphere_theta_deg" in bundle else None
        sphere_phi = bundle["sphere_phi_deg"] if "sphere_phi_deg" in bundle else None
        results: dict[str, Any] = {}
        for name in channel_ids:
            sphere_key = f"sphere::{name}"
            results[name] = SimpleNamespace(
                frequencies_hz=frequencies,
                observation_angles_deg=angles,
                observation_points=None,
                observation_planes=planes,
                pressure_complex=bundle[f"pressure::{name}"],
                impedance=np.zeros(frequencies.size, dtype=np.complex128),
                solver_log=[],
                timings={},
                native_diagnostics=[],
                sphere_pressure_complex=bundle[sphere_key] if sphere_key in bundle else None,
                sphere_points=None,
                sphere_theta_deg=sphere_theta,
                sphere_phi_deg=sphere_phi,
            )
    return {
        "channel_ids": channel_ids,
        "frequencies_hz": frequencies,
        "results_by_id": results,
        "has_balloon": sphere_theta is not None,
    }


def _lr4_lowpass(freqs: np.ndarray, fc_hz: float) -> np.ndarray:
    s = 1j * np.asarray(freqs, dtype=np.float64) / float(fc_hz)
    return 1.0 / (s * s + np.sqrt(2.0) * s + 1.0) ** 2


def _lr4_highpass(freqs: np.ndarray, fc_hz: float) -> np.ndarray:
    s = 1j * np.asarray(freqs, dtype=np.float64) / float(fc_hz)
    return (s * s) ** 2 / (s * s + np.sqrt(2.0) * s + 1.0) ** 2


def lr4_chain_weights(
    freqs: np.ndarray, members: list[str], crossovers_hz: list[float]
) -> dict[str, np.ndarray]:
    """LR4 weights along an ordered chain: LP, BP..., HP (engineering)."""

    weights: dict[str, np.ndarray] = {}
    for index, name in enumerate(members):
        weight = np.ones(np.asarray(freqs).shape, dtype=np.complex128)
        if index > 0:
            weight = weight * _lr4_highpass(freqs, crossovers_hz[index - 1])
        if index < len(crossovers_hz):
            weight = weight * _lr4_lowpass(freqs, crossovers_hz[index])
        weights[name] = weight
    return weights


@dataclass(frozen=True)
class ResolvedChannel:
    """One member's resolved crossover settings.

    ``gain_db`` / ``delay_ms`` carry the manual value and are ignored in
    ``auto`` and ``max`` modes; ``invert`` is ``None`` when the polarity
    follows the ideal filter pair.
    """

    hp: Filter | None = None
    lp: Filter | None = None
    gain_mode: str = "auto"
    gain_db: float | None = None
    delay_mode: str = "auto"
    delay_ms: float | None = None
    invert: bool | None = None


def _mode(value: Any, field: str) -> tuple[str, float | None]:
    if value is None:
        return "auto", None
    if isinstance(value, Mapping):
        mode = str(value.get("mode") or "auto").strip().lower()
        raw = value.get(field)
    else:
        raise ValueError(f"combine channel {field} must be a mapping")
    allowed = ("auto", "manual", "max") if field == "db" else ("auto", "manual")
    if mode not in allowed:
        raise ValueError(
            f"combine channel {field} mode must be "
            f"{' or '.join(allowed[:-1])} or {allowed[-1]}"
        )
    if mode in ("auto", "max"):
        return mode, None
    number = 0.0 if raw is None else float(raw)
    if not math.isfinite(number):
        raise ValueError(f"combine channel manual {field} must be finite")
    return "manual", number


def normalize_channel(value: Any) -> ResolvedChannel:
    """Build a :class:`ResolvedChannel` from a wire mapping."""

    if isinstance(value, ResolvedChannel):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("a combine channel entry must be a mapping")
    gain_mode, gain_db = _mode(value.get("gain"), "db")
    delay_mode, delay_ms = _mode(value.get("delay"), "ms")
    invert = value.get("invert")
    hp = Filter.from_mapping(value.get("hp"))
    lp = Filter.from_mapping(value.get("lp"))
    if hp is not None and lp is not None and hp.fc_hz >= lp.fc_hz:
        raise ValueError(
            "a combine channel high-pass must sit below its low-pass: "
            f"{hp.fc_hz:g} Hz is not below {lp.fc_hz:g} Hz"
        )
    return ResolvedChannel(
        hp=hp,
        lp=lp,
        gain_mode=gain_mode,
        gain_db=gain_db,
        delay_mode=delay_mode,
        delay_ms=delay_ms,
        invert=None if invert is None else bool(invert),
    )


def expand_legacy_channels(
    members: list[str],
    crossovers_hz: list[float],
    *,
    level_match: bool = True,
    align: bool = True,
) -> dict[str, dict[str, Any]]:
    """Expand the legacy LR4 chain into the per-channel wire form.

    The one definition of what a legacy spec means: LR4 pairs, auto gain when
    level matching is on and a manual 0 dB when it is off, auto delay when
    alignment is on and a manual 0 ms when it is off, polarity from the pair.
    """

    crossovers = [float(value) for value in crossovers_hz]
    if len(crossovers) != len(members) - 1:
        raise ValueError(
            "combine needs exactly one crossover between each adjacent member "
            f"pair: {len(members)} members require {len(members) - 1} crossovers_hz"
        )
    gain: dict[str, Any] = {"mode": "auto"} if level_match else {"mode": "manual", "db": 0.0}
    delay: dict[str, Any] = {"mode": "auto"} if align else {"mode": "manual", "ms": 0.0}
    expanded: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(members):
        expanded[name] = {
            "hp": (
                {"family": "lr", "order": 4, "fc_hz": crossovers[index - 1]}
                if index > 0
                else None
            ),
            "lp": (
                {"family": "lr", "order": 4, "fc_hz": crossovers[index]}
                if index < len(crossovers)
                else None
            ),
            "gain": dict(gain),
            "delay": dict(delay),
            "invert": None,
        }
    return expanded


def chain_weights(
    freqs: np.ndarray,
    members: list[str],
    channels: Mapping[str, ResolvedChannel],
) -> dict[str, np.ndarray]:
    """Band-limiting weight per member (engineering convention)."""

    frequencies = np.asarray(freqs, dtype=np.float64)
    return {
        name: channel_weight(frequencies, hp=channels[name].hp, lp=channels[name].lp)
        for name in members
    }


def raw_channel_weights(
    freqs: np.ndarray,
    members: list[str],
    crossovers_hz: list[float] | None,
    gains_db: Mapping[str, float],
    delays_s: Mapping[str, float],
    *,
    channels: Mapping[str, Any] | None = None,
    inverted: Mapping[str, bool] | None = None,
) -> dict[str, np.ndarray]:
    """Return factors applied to raw ``exp(+ikr)`` solver fields.

    Crossover filters, gains, delays and polarity are defined in the
    engineering ``e^{+jωt}`` convention. Raw solver fields use ``e^{-iωt}``,
    so this is the single convention boundary where every engineering weight
    is complex conjugated before it is applied to pressure or Neumann traces.

    Pass ``channels`` (the resolved per-member sections) for anything other
    than a plain LR4 chain; ``crossovers_hz`` is the legacy shorthand for one.
    """

    frequencies = np.asarray(freqs, dtype=np.float64).reshape(-1)
    if channels is None:
        if crossovers_hz is None:
            raise ValueError("raw_channel_weights needs crossovers_hz or channels")
        channels = {
            name: normalize_channel(value)
            for name, value in expand_legacy_channels(
                list(members), list(crossovers_hz)
            ).items()
        }
    else:
        channels = {name: normalize_channel(channels[name]) for name in members}
    bands = chain_weights(frequencies, list(members), channels)
    polarity = inverted or {}
    weights_eng = {
        name: bands[name]
        * (10.0 ** (float(gains_db[name]) / 20.0))
        * np.exp(-1j * _TWO_PI * frequencies * float(delays_s[name]))
        * (-1.0 if polarity.get(name) else 1.0)
        for name in members
    }
    return {name: np.conjugate(weights_eng[name]) for name in members}


def _interp_complex(freqs: np.ndarray, values: np.ndarray, target_hz: float) -> complex:
    """Interpolate magnitude and unwrapped phase separately at ``target_hz``."""

    mag = np.interp(float(target_hz), freqs, np.abs(values))
    phase = np.interp(float(target_hz), freqs, np.unwrap(np.angle(values)))
    return complex(mag * np.exp(1j * phase))


@dataclass(frozen=True)
class _PairFit:
    """One adjacent pair's alignment result and its confidence evidence."""

    delay_s: float
    residual_deg: float | None
    intercept_deg: float | None
    points: int
    fitted_delay_s: float | None = None
    phase_at_fc_rad: float | None = None


def _weighted_line_fit(
    x: np.ndarray, y: np.ndarray, weights: np.ndarray
) -> tuple[float, float, float] | None:
    """Weighted least-squares ``y = slope*x + intercept``; also its RMS residual."""

    total = float(np.sum(weights))
    if total <= 0.0 or x.size < 2:
        return None
    sx = float(np.sum(weights * x))
    sy = float(np.sum(weights * y))
    sxx = float(np.sum(weights * x * x))
    sxy = float(np.sum(weights * x * y))
    determinant = total * sxx - sx * sx
    if not np.isfinite(determinant) or determinant == 0.0:
        return None
    slope = (total * sxy - sx * sy) / determinant
    intercept = (sxx * sy - sx * sxy) / determinant
    if not (np.isfinite(slope) and np.isfinite(intercept)):
        return None
    residual = y - (slope * x + intercept)
    rms = float(np.sqrt(np.sum(weights * residual * residual) / total))
    return float(slope), float(intercept), rms


def _wrap_pi(value: float) -> float:
    return float((value + np.pi) % (2.0 * np.pi) - np.pi)


def _pair_alignment(
    freqs: np.ndarray,
    ratio: np.ndarray,
    weights: np.ndarray,
    overlap: np.ndarray,
    eval_hz: float,
) -> tuple[_PairFit, list[str]]:
    """Fit the raw arrival offset of one adjacent pair.

    ``ratio`` is ``P_lower / P_upper`` from the raw on-axis spectra, so the
    delay it returns is the physical arrival gap the filters must be handed a
    coincident pair for. The coarse pass fits a +/-1/3-octave window where the
    unwrap cannot slip a cycle; the refine pass removes that delay and refits
    over the whole overlap region, where the residual phase is nearly flat.
    """

    notes: list[str] = []
    x = _TWO_PI * freqs
    coarse = (
        overlap
        & (freqs >= eval_hz * 2.0 ** -_COARSE_HALF_OCTAVE)
        & (freqs <= eval_hz * 2.0**_COARSE_HALF_OCTAVE)
    )
    coarse_count = int(np.count_nonzero(coarse))
    if coarse_count < _MIN_FIT_POINTS:
        notes.append(
            f"only {coarse_count} solved frequencies lie in the +/-1/3-octave "
            f"window around {eval_hz:g} Hz; the alignment delay is fitted over "
            "the whole overlap region instead and may pick the wrong cycle"
        )
        coarse = overlap
    phase = np.unwrap(np.angle(ratio[coarse]))
    fit = _weighted_line_fit(x[coarse], phase, weights[coarse])
    if fit is None:
        notes.append(
            f"the pair overlap around {eval_hz:g} Hz carries no usable phase; "
            "its alignment delay defaults to 0"
        )
        return _PairFit(0.0, None, None, int(np.count_nonzero(overlap))), notes
    coarse_delay = fit[0]

    residual_ratio = ratio * np.exp(-1j * x * coarse_delay)
    refine_mask = overlap if int(np.count_nonzero(overlap)) >= 2 else coarse
    refined = _weighted_line_fit(
        x[refine_mask],
        np.unwrap(np.angle(residual_ratio[refine_mask])),
        weights[refine_mask],
    )
    if refined is None:
        return (
            _PairFit(coarse_delay, None, None, int(np.count_nonzero(refine_mask))),
            notes,
        )
    slope, intercept, rms = refined
    fitted_delay = coarse_delay + slope
    at_fc = _interp_complex(freqs, ratio, eval_hz)
    return (
        _PairFit(
            delay_s=fitted_delay,
            fitted_delay_s=fitted_delay,
            residual_deg=float(np.degrees(rms)),
            intercept_deg=float(np.degrees(_wrap_pi(intercept))),
            points=int(np.count_nonzero(refine_mask)),
            phase_at_fc_rad=float(np.angle(at_fc)) if abs(at_fc) > 0.0 else None,
        ),
        notes,
    )


def _pin_delay_s(fit: _PairFit, eval_hz: float, target_rad: float) -> float:
    """Pin a pair's delay where the crossover actually sums.

    The fit settles the period branch, which a phase value at one frequency
    cannot do on its own; the delay itself then brings the raw ratio's phase
    at ``eval_hz`` to ``target_rad`` (0 for a like-polarity pair, pi when the
    applied polarity flips the raw pair) on the branch nearest the fitted
    slope. On a real horn the raw ratio is rarely a pure delay, and the
    least-squares line trades exactness at fc for the wings -- measured on a
    three-way return as a reverse null of -8 dB instead of -15 dB.
    """

    if fit.phase_at_fc_rad is None:
        return fit.delay_s
    period = 1.0 / float(eval_hz)
    principal = (fit.phase_at_fc_rad - target_rad) / (_TWO_PI * float(eval_hz))
    cycles = round((fit.delay_s - principal) / period)
    return principal + cycles * period


def _raw_pair_flipped(fit: _PairFit) -> bool:
    """True when the raw drivers of a pair sit closer to 180 than to 0 degrees."""

    return fit.intercept_deg is not None and abs(fit.intercept_deg) > 90.0


def _spl_db(pressure: np.ndarray, reference_pa: float) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(np.abs(pressure), 1.0e-30) / reference_pa)


def _level_match_gains_db(
    freqs: np.ndarray,
    filtered_on_axis: Mapping[str, np.ndarray],
    members: list[str],
    channels: Mapping[str, ResolvedChannel],
) -> tuple[dict[str, float], dict[str, float], float]:
    """Today's rule: median filtered on-axis SPL per member in its passband.

    A member's passband runs from its high-pass corner (or the bottom of the
    solved band) to its low-pass corner (or the top). For a linked LR4 chain
    that is exactly the legacy crossover-edge partition.
    """

    medians: dict[str, float] = {}
    for name in members:
        channel = channels[name]
        low = float(channel.hp.fc_hz) if channel.hp is not None else float(freqs[0])
        high = float(channel.lp.fc_hz) if channel.lp is not None else float(freqs[-1])
        band = (freqs >= low) & (freqs <= high)
        spl = _spl_db(np.asarray(filtered_on_axis[name]), 20.0e-6)
        values = spl[band]
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            finite = spl[np.isfinite(spl)]
        medians[name] = float(np.median(finite)) if finite.size else 0.0
    target_db = float(np.median([medians[name] for name in members]))
    gains_db = {name: target_db - medians[name] for name in members}
    return gains_db, medians, target_db


def _on_axis_angle_index(angles: np.ndarray, point_count: int) -> int | None:
    usable = np.flatnonzero(np.isfinite(angles))
    usable = usable[usable < point_count]
    if usable.size == 0:
        return None
    return int(usable[np.argmin(np.abs(angles[usable]))])




def _resolve_channels(
    members: list[str],
    channels: Mapping[str, Any] | None,
    crossovers_hz: list[float] | None,
    *,
    level_match: bool,
    align: bool,
) -> dict[str, ResolvedChannel]:
    """Accept either spec form and return the per-member resolved settings."""

    if channels is None:
        if crossovers_hz is None:
            raise ValueError("combine needs either crossovers_hz or channels")
        channels = expand_legacy_channels(
            list(members),
            list(crossovers_hz),
            level_match=level_match,
            align=align,
        )
    missing = [name for name in members if name not in channels]
    if missing:
        raise ValueError(f"combine channels miss the members {missing}")
    return {name: normalize_channel(channels[name]) for name in members}


def _pair_frequencies(
    lower: ResolvedChannel, upper: ResolvedChannel
) -> tuple[float | None, float | None]:
    """``(linked crossover, evaluation frequency)`` of one adjacent pair.

    A pair is *linked* when the lower member's low-pass and the upper
    member's high-pass share a corner; that shared corner is the crossover
    the payload reports. An unlinked pair has no single crossover, so it is
    evaluated at the geometric mean of the two corners it does have.
    """

    corners = [
        float(section.fc_hz)
        for section in (lower.lp, upper.hp)
        if section is not None
    ]
    if (
        lower.lp is not None
        and upper.hp is not None
        and math.isclose(lower.lp.fc_hz, upper.hp.fc_hz, rel_tol=1.0e-9, abs_tol=0.0)
    ):
        return float(lower.lp.fc_hz), float(lower.lp.fc_hz)
    if not corners:
        return None, None
    return None, float(np.exp(np.mean(np.log(corners))))


def _phase_error_deg(
    freqs: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    lower_channel: ResolvedChannel,
    upper_channel: ResolvedChannel,
    eval_hz: float,
) -> float | None:
    """How far the aligned pair sits from what its filter chains ask for.

    The ideal pair already has a phase relationship of its own at the
    crossover (0 degrees for LR4, 90 for BW3, plus whatever a middle band's
    other section contributes there); what matters is the departure from it.
    ``lower``/``upper`` carry the applied polarity, so an inverted channel
    that sums correctly reads as 0, not 180.
    """

    point = np.asarray([float(eval_hz)], dtype=np.float64)
    measured_low = _interp_complex(freqs, lower, eval_hz)
    measured_high = _interp_complex(freqs, upper, eval_hz)
    if abs(measured_low) <= 0.0 or abs(measured_high) <= 0.0:
        return None
    ideal_low = complex(
        channel_weight(point, hp=lower_channel.hp, lp=lower_channel.lp)[0]
    )
    ideal_high = complex(
        channel_weight(point, hp=upper_channel.hp, lp=upper_channel.lp)[0]
    )
    if abs(ideal_low) <= 0.0 or abs(ideal_high) <= 0.0:
        return None
    error = np.angle(measured_low / measured_high) - np.angle(ideal_low / ideal_high)
    return float(np.degrees(_wrap_pi(float(error))))


def _reverse_null_db(
    lower: np.ndarray, upper: np.ndarray, overlap: np.ndarray
) -> float | None:
    """Depth of the null the pair makes when the upper member is flipped.

    A deep reverse null is the classical evidence that the pair is aligned:
    the two members are summing coherently, so inverting one cancels them.
    Reported as the minimum of ``|lower − upper| / |lower + upper|`` over the
    overlap region, in dB relative to the sum.
    """

    if not np.any(overlap):
        return None
    summed = np.abs(lower[overlap] + upper[overlap])
    reversed_sum = np.abs(lower[overlap] - upper[overlap])
    usable = summed > 0.0
    if not np.any(usable):
        return None
    ratio = 20.0 * np.log10(
        np.maximum(reversed_sum[usable], 1.0e-30) / summed[usable]
    )
    finite = ratio[np.isfinite(ratio)]
    if finite.size == 0:
        return None
    return float(max(float(np.min(finite)), _REVERSE_NULL_FLOOR_DB))


def _json_floats(values: np.ndarray) -> list[float | None]:
    """A float list JSON can hold: every non-finite entry becomes ``null``.

    An unlimited member has an infinite headroom, which is the right answer and
    an illegal number. ``null`` says "nothing stops it here" without inventing
    a ceiling, and every reader already draws a gap for a missing point.
    """

    array = np.asarray(values, dtype=np.float64).reshape(-1)
    return [float(value) if math.isfinite(value) else None for value in array]


#: How far below the loudest member a member may sit before it stops setting
#: the level. Twenty decibels is a tenth of the voltage, which cannot move the
#: sum by a decibel -- and cannot, therefore, be what a system maximum is
#: waiting on.
_CONTRIBUTION_FLOOR_DB = -20.0


def _carrying(member_pressure_eng: Mapping[str, np.ndarray], members: list[str]) -> dict[str, np.ndarray]:
    """Which members are actually setting the level, per frequency.

    A member outside its own passband has enormous headroom for the simple
    reason that it is not playing, and neither its room nor its lack of a
    rating says anything about how loud the system can go there.
    """

    magnitudes = {name: np.abs(member_pressure_eng[name]) for name in members}
    loudest = np.maximum.reduce([magnitudes[name] for name in members])
    floor = loudest * (10.0 ** (_CONTRIBUTION_FLOOR_DB / 20.0))
    return {name: magnitudes[name] >= floor for name in members}


def _max_output_payload(
    freqs: np.ndarray,
    members: list[str],
    limits: Mapping[str, MemberLimits],
    bands: Mapping[str, np.ndarray],
    gains_db: Mapping[str, float],
    member_pressure_eng: Mapping[str, np.ndarray],
    combined_on_axis_spl_db: np.ndarray,
) -> dict[str, Any] | None:
    """Maximum SPL per frequency, per member and for the sum.

    A member's own trace is its filtered on-axis level lifted by its own
    headroom -- how loud that driver alone could play with the crossover it
    has. The combined trace is the system answer and a different question: the
    whole chain scales together, so it is lifted by the *smallest* headroom any
    member still has at that frequency, and the member holding it back is named
    so the limiting driver is visible rather than inferred.

    Both traces stop where the member stops setting the level. Far outside its
    passband a cone barely moves, so Xmax permits an enormous drive and the
    arithmetic reports a mid driver playing 180 dB at 20 kHz -- true of the
    filter, false of the driver, and precisely the kind of number a maximum-
    output chart must not print.

    A member with no ceiling at all is not free headroom either. Wherever such
    a member is one of the ones setting the level, the system maximum is simply
    not known, and it is reported as unknown -- a three-way whose compression
    driver has no rating has no system maximum above its crossover, and saying
    one anyway is how an unrated tweeter came to promise 324 dB.
    """

    known = [name for name in members if name in limits]
    if not known:
        return None
    reference_pa = 20.0e-6
    carrying = _carrying(member_pressure_eng, list(members))
    # Where the answer cannot be known, because something that is playing has
    # nothing to be measured against.
    unknown = np.zeros(freqs.shape, dtype=bool)
    for name in members:
        if name not in limits:
            unknown |= carrying[name]
    system_scale = np.full(freqs.shape, float("inf"), dtype=np.float64)
    system_member = np.full(freqs.shape, "", dtype=object)
    system_reason = np.full(freqs.shape, "", dtype=object)
    member_payload: dict[str, Any] = {}
    for name in known:
        applied = np.abs(bands[name]) * (10.0 ** (float(gains_db[name]) / 20.0))
        room = headroom(limits[name], applied)
        with np.errstate(divide="ignore", invalid="ignore"):
            level_db = 20.0 * np.log10(
                np.maximum(np.abs(member_pressure_eng[name]), 1.0e-30) / reference_pa
            )
            lift_db = 20.0 * np.log10(np.where(np.isfinite(room.scale), room.scale, np.nan))
        in_band = carrying[name]
        member_payload[name] = {
            "spl_max_db": _json_floats(np.where(in_band, level_db + lift_db, np.nan)),
            "headroom_db": _json_floats(np.where(in_band, lift_db, np.nan)),
            "limit": [
                str(value) or None if playing else None
                for value, playing in zip(room.reason, in_band, strict=True)
            ],
            "excursion_fraction": room.excursion_fraction,
            "power_fraction": room.power_fraction,
            "voltage_fraction": room.voltage_fraction,
            # The ratings themselves, so a fraction can be read as the two
            # numbers it came from. "31% of rated power" is a percentage;
            # "124 W of 400 W" is an answer -- and it is *real* power, which
            # at an impedance peak is nothing like the nominal watts a gain
            # in W is stated in.
            "xmax_mm": limits[name].xmax_mm,
            "rated_power_w": limits[name].rated_power_w,
            "max_voltage_v": limits[name].max_voltage_v,
        }
        binds = np.isfinite(room.scale) & (room.scale < system_scale)
        system_scale[binds] = room.scale[binds]
        system_member[binds] = name
        system_reason[binds] = room.reason[binds]
    with np.errstate(divide="ignore", invalid="ignore"):
        system_lift_db = 20.0 * np.log10(
            np.where(np.isfinite(system_scale) & ~unknown, system_scale, np.nan)
        )
    unlimited = sorted(name for name in members if name not in limits)
    return {
        "frequencies": freqs.tolist(),
        "reference": "one_way_peak_excursion_and_real_terminal_power",
        "members": member_payload,
        "combined": {
            "spl_max_db": _json_floats(combined_on_axis_spl_db + system_lift_db),
            "headroom_db": _json_floats(system_lift_db),
            "limit": [
                None if blank else str(value) or None
                for value, blank in zip(system_reason, unknown, strict=True)
            ],
            "limiting_member": [
                None if blank else str(value) or None
                for value, blank in zip(system_member, unknown, strict=True)
            ],
        },
        # Named rather than left as a gap in a curve: the fix is to give these
        # channels' drivers a rating, and the user cannot do that unknowingly.
        "unlimited_members": unlimited,
        "caveat": (
            "small-signal swept-sine ceilings: no thermal compression, no "
            "voice-coil heating, no inductance nonlinearity, no programme "
            "material"
        ),
    }


def combine_drive_channels(
    results_by_id: Mapping[str, Any],
    *,
    members: list[str],
    crossovers_hz: list[float] | None = None,
    level_match: bool = True,
    align: bool = True,
    member_validity_hz: Mapping[str, float] | None = None,
    member_roles: Mapping[str, str | None] | None = None,
    channels: Mapping[str, Any] | None = None,
    reference: str | None = None,
    member_limits: Mapping[str, MemberLimits] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Sum member channel results into one synthetic native-shaped result.

    ``results_by_id`` holds the frequency-sorted native results. Pass
    ``channels`` (and optionally ``reference``) for the per-channel spec, or
    the legacy ``crossovers_hz``/``level_match``/``align`` triple, which is
    expanded into an LR4 per-channel spec before anything else runs.
    ``member_limits`` carries each driver-coupled member's excursion, power and
    voltage ceilings; without it a ``max`` gain has nothing to solve for and
    the maximum-output payload is omitted rather than guessed at. Returns
    the synthetic result (same duck type ``build_solver_response`` consumes)
    and the ``combine`` metadata payload. Raises ``ValueError`` when the
    members do not share one observation grid — a solver contract violation,
    not a user error.
    """

    warnings: list[str] = []
    settings = _resolve_channels(
        list(members),
        channels,
        None if crossovers_hz is None else list(crossovers_hz),
        level_match=level_match,
        align=align,
    )
    reference_id = str(reference) if reference else members[-1]
    if reference_id not in members:
        raise ValueError(f"combine reference {reference_id!r} is not a member")

    base = results_by_id[members[0]]
    freqs = np.asarray(base.frequencies_hz, dtype=np.float64).reshape(-1)
    angles = np.asarray(base.observation_angles_deg, dtype=np.float64)
    planes = list(base.observation_planes)
    fields: dict[str, np.ndarray] = {}
    for name in members:
        result = results_by_id[name]
        if not np.array_equal(
            np.asarray(result.frequencies_hz, dtype=np.float64).reshape(-1), freqs
        ):
            raise ValueError("combine members do not share one frequency axis")
        field = np.asarray(result.pressure_complex, dtype=np.complex128)
        if field.ndim != 3:
            raise ValueError("combine members must carry (F, P, N) pressure fields")
        if fields and field.shape != next(iter(fields.values())).shape:
            raise ValueError("combine members do not share one observation grid")
        fields[name] = field

    point_count = next(iter(fields.values())).shape[2]
    on_axis = _on_axis_angle_index(angles, point_count)
    if on_axis is None:
        raise ValueError("combine members have no finite observation angles")
    reference_angle_deg = float(angles[on_axis])
    if not np.isclose(reference_angle_deg, 0.0, rtol=0.0, atol=1.0e-9):
        warnings.append(
            "0 degrees is not present in the sampled polar grid; combine "
            "crossover level matching and phase alignment use the nearest "
            f"available angle, {reference_angle_deg:g} degrees"
        )

    solved_band = (float(freqs[0]), float(freqs[-1]))
    corners = [
        float(section.fc_hz)
        for name in members
        for section in (settings[name].hp, settings[name].lp)
        if section is not None
    ]
    outside_band = sorted(
        {value for value in corners if value < solved_band[0] or value > solved_band[1]}
    )
    if outside_band:
        warnings.append(
            f"combine crossovers_hz {outside_band} lie outside the solved band "
            f"[{solved_band[0]:g}, {solved_band[1]:g}] Hz; level matching may "
            "fall back to a full-band median"
        )

    # Pair geometry: the reported crossover (linked pairs only) and the
    # frequency every pair decision is taken at.
    pair_names = [
        f"{members[index]}-{members[index + 1]}" for index in range(len(members) - 1)
    ]
    linked_hz: list[float | None] = []
    eval_hz: list[float] = []
    for index in range(len(members) - 1):
        linked, evaluated = _pair_frequencies(
            settings[members[index]], settings[members[index + 1]]
        )
        if evaluated is None:
            evaluated = float(np.sqrt(solved_band[0] * solved_band[1]))
            warnings.append(
                f"pair {pair_names[index]} has no crossover section; its "
                f"alignment is evaluated at {evaluated:g} Hz, the geometric "
                "centre of the solved band"
            )
        linked_hz.append(linked)
        eval_hz.append(float(min(max(evaluated, solved_band[0]), solved_band[1])))

    if member_validity_hz:
        for index, evaluated in enumerate(eval_hz):
            for name in (members[index], members[index + 1]):
                limit = member_validity_hz.get(name)
                if limit is not None and float(evaluated) > float(limit):
                    warnings.append(
                        f"crossover {float(evaluated):g} Hz is above channel "
                        f"{name!r} source validity limit {float(limit):g} Hz"
                    )

    # Engineering-domain on-axis pressures drive every weight decision.
    pressures_eng = {
        name: np.conjugate(fields[name][:, 0, on_axis]) for name in members
    }
    bands = chain_weights(freqs, list(members), settings)
    filtered = {name: pressures_eng[name] * bands[name] for name in members}

    auto_gains_db, medians_db, target_db = _level_match_gains_db(
        freqs, filtered, list(members), settings
    )
    # The ceiling each member's own driver puts on it, resolved against the
    # crossover magnitude alone so it is an absolute gain: pressing Max twice
    # must land on the same level, not walk it up by its own headroom.
    limits = dict(member_limits or {})
    max_gains_db: dict[str, float | None] = {}
    max_limit: dict[str, str | None] = {}
    max_limit_hz: dict[str, float | None] = {}
    max_limit_edge: dict[str, bool] = {}
    for name in members:
        member_limit = limits.get(name)
        if member_limit is None:
            max_gains_db[name] = None
            max_limit[name] = None
            max_limit_hz[name] = None
            max_limit_edge[name] = False
            continue
        ceiling_db, reason, index = gain_ceiling_db(member_limit, np.abs(bands[name]))
        max_gains_db[name] = ceiling_db
        max_limit[name] = reason
        max_limit_hz[name] = None if index is None else float(freqs[index])
        max_limit_edge[name] = index in (0, freqs.size - 1)
    for name in members:
        if settings[name].gain_mode == "max" and max_gains_db[name] is None:
            warnings.append(
                f"channel {name!r} is set to maximum gain but carries no driver "
                "limit the solve can read (no driver model, or no Xmax, rated "
                "power or amplifier ceiling); it is left at 0 dB"
            )
    gains_db = {
        name: (
            auto_gains_db[name]
            if settings[name].gain_mode == "auto"
            else (max_gains_db[name] or 0.0)
            if settings[name].gain_mode == "max"
            else float(settings[name].gain_db or 0.0)
        )
        for name in members
    }
    level_matched = any(settings[name].gain_mode == "auto" for name in members)
    if not level_matched:
        medians_db = {}
        target_db = None

    gain_scale = {name: 10.0 ** (gains_db[name] / 20.0) for name in members}
    levelled = {name: filtered[name] * gain_scale[name] for name in members}

    # Alignment: one fit per adjacent pair on the raw arrival difference, so
    # the ideal filter pair sees a coincident pair of drivers.
    pair_fits: list[_PairFit] = []
    overlaps: list[np.ndarray] = []
    for index in range(len(members) - 1):
        lower, upper = members[index], members[index + 1]
        weight = np.abs(levelled[lower]) * np.abs(levelled[upper])
        usable = (
            np.isfinite(weight)
            & (np.abs(pressures_eng[lower]) > 0.0)
            & (np.abs(pressures_eng[upper]) > 0.0)
        )
        peak = float(np.max(weight[usable])) if np.any(usable) else 0.0
        overlap = (
            usable & (weight > peak * 10.0 ** (_OVERLAP_FLOOR_DB / 20.0))
            if peak > 0.0
            else usable
        )
        overlaps.append(overlap)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(
                usable,
                pressures_eng[lower] / pressures_eng[upper],
                1.0,
            )
        fit, notes = _pair_alignment(freqs, ratio, weight, overlap, eval_hz[index])
        pair_fits.append(fit)
        for note in notes:
            warnings.append(f"pair {pair_names[index]}: {note}")
        if fit.residual_deg is not None and fit.residual_deg > _RESIDUAL_WARN_DEG:
            warnings.append(
                f"pair {pair_names[index]}: the alignment phase fit leaves a "
                f"{fit.residual_deg:.1f} degree RMS residual; the delay it "
                "reports is an estimate, not a measurement"
            )

    # Polarity: the ideal filter pair says whether the upper member flips
    # (LR2, BW2); the raw drivers say whether they are already opposed
    # (intercept near 180 degrees). Auto applies both, so an opposed pair is
    # inverted and then aligned on its true delay instead of a half-period
    # one. An explicit ``invert`` overrides the channel; the delay target
    # follows whatever polarity is actually applied, so the pair still sums in
    # phase at the crossover either way.
    ideal_flips = [
        pair_inverts(
            settings[members[index]].lp,
            settings[members[index + 1]].hp,
            eval_hz[index],
        )
        for index in range(len(members) - 1)
    ]
    raw_flips = [_raw_pair_flipped(pair_fits[index]) for index in range(len(members) - 1)]
    auto_inverted: dict[str, bool] = {members[0]: False}
    for index in range(len(members) - 1):
        auto_inverted[members[index + 1]] = auto_inverted[members[index]] != (
            ideal_flips[index] != raw_flips[index]
        )
    inverted = {
        name: (
            auto_inverted[name]
            if settings[name].invert is None
            else bool(settings[name].invert)
        )
        for name in members
    }
    pair_delay_s: list[float] = []
    for index in range(len(members) - 1):
        lower, upper = members[index], members[index + 1]
        applied_flip = inverted[lower] != inverted[upper]
        raw_target = np.pi if applied_flip != ideal_flips[index] else 0.0
        pair_delay_s.append(_pin_delay_s(pair_fits[index], eval_hz[index], raw_target))
        if raw_flips[index]:
            if applied_flip != ideal_flips[index]:
                warnings.append(
                    f"pair {pair_names[index]}: the raw drivers sit "
                    f"{pair_fits[index].intercept_deg:.0f} degrees apart; "
                    f"{upper!r} is inverted relative to {lower!r} so the pair "
                    "sums in phase on its true delay"
                )
            else:
                warnings.append(
                    f"pair {pair_names[index]}: the raw drivers sit "
                    f"{pair_fits[index].intercept_deg:.0f} degrees apart but "
                    "neither channel is inverted, so they are aligned with a "
                    "half-period delay that only holds near the crossover; "
                    "consider inverting one of them"
                )

    reference_index = members.index(reference_id)
    auto_delay_s: dict[str, float] = {reference_id: 0.0}
    for index in range(reference_index - 1, -1, -1):
        auto_delay_s[members[index]] = (
            auto_delay_s[members[index + 1]] + pair_delay_s[index]
        )
    for index in range(reference_index + 1, len(members)):
        auto_delay_s[members[index]] = (
            auto_delay_s[members[index - 1]] - pair_delay_s[index - 1]
        )
    delays_s = {
        name: (
            auto_delay_s[name]
            if settings[name].delay_mode == "auto"
            else float(settings[name].delay_ms or 0.0) / 1000.0
        )
        for name in members
    }
    aligned_any = any(settings[name].delay_mode == "auto" for name in members)

    aligned = {
        name: levelled[name] * np.exp(-1j * _TWO_PI * freqs * delays_s[name])
        for name in members
    }
    pairs: dict[str, dict[str, Any]] = {}
    for index in range(len(members) - 1):
        lower, upper = members[index], members[index + 1]
        pairs[pair_names[index]] = {
            "eval_hz": eval_hz[index],
            "fit_residual_deg": pair_fits[index].residual_deg,
            "fit_delay_ms": (
                None
                if pair_fits[index].fitted_delay_s is None
                else pair_fits[index].fitted_delay_s * 1000.0
            ),
            "phase_error_at_fc_deg": _phase_error_deg(
                freqs,
                aligned[lower] * (-1.0 if inverted[lower] else 1.0),
                aligned[upper] * (-1.0 if inverted[upper] else 1.0),
                settings[lower],
                settings[upper],
                eval_hz[index],
            ),
            "reverse_null_db": _reverse_null_db(
                aligned[lower] * (-1.0 if inverted[lower] else 1.0),
                aligned[upper] * (-1.0 if inverted[upper] else 1.0),
                overlaps[index],
            ),
            "points": pair_fits[index].points,
        }

    # The single convention boundary: engineering weight -> raw-field factor.
    weights_raw = raw_channel_weights(
        freqs,
        list(members),
        None,
        gains_db,
        delays_s,
        channels=settings,
        inverted=inverted,
    )

    combined_field = np.zeros_like(next(iter(fields.values())))
    for name in members:
        combined_field += weights_raw[name][:, None, None] * fields[name]

    spl = _spl_db(combined_field, 20.0e-6)
    directivity_db = spl - spl[:, :, on_axis][:, :, None]

    sphere_pressure = None
    sphere_theta = getattr(base, "sphere_theta_deg", None)
    sphere_phi = getattr(base, "sphere_phi_deg", None)
    sphere_points = getattr(base, "sphere_points", None)
    member_spheres = [
        getattr(results_by_id[name], "sphere_pressure_complex", None)
        for name in members
    ]
    if all(value is not None for value in member_spheres):
        sphere_pressure = np.zeros_like(
            np.asarray(member_spheres[0], dtype=np.complex128)
        )
        for name, member_sphere in zip(members, member_spheres, strict=True):
            sphere_pressure += (
                weights_raw[name][:, None]
                * np.asarray(member_sphere, dtype=np.complex128)
            )
    elif any(value is not None for value in member_spheres):
        warnings.append(
            "balloon sampling is missing from at least one member; the combined "
            "channel has no balloon"
        )
        sphere_theta = sphere_phi = sphere_points = None
    else:
        sphere_theta = sphere_phi = sphere_points = None

    combined = SimpleNamespace(
        frequencies_hz=freqs.copy(),
        observation_angles_deg=np.asarray(base.observation_angles_deg).copy(),
        observation_points=getattr(base, "observation_points", None),
        observation_planes=planes,
        pressure_complex=combined_field,
        directivity_db=directivity_db,
        # Placeholder so the response builder's axis check passes; the
        # combined channel's impedance is popped from the response anyway.
        impedance=np.zeros(freqs.size, dtype=np.complex128),
        surface_pressure_avg=None,
        solver_log=list(getattr(base, "solver_log", []) or []),
        timings={},
        native_diagnostics=[],
        sphere_pressure_complex=sphere_pressure,
        sphere_points=sphere_points,
        sphere_theta_deg=sphere_theta,
        sphere_phi_deg=sphere_phi,
    )
    roles = member_roles or {}
    payload: dict[str, Any] = {
        "type": "filtered_time_aligned_sum",
        "members": list(members),
        "member_roles": [roles.get(name) for name in members],
        "reference": reference_id,
        "crossovers_hz": linked_hz,
        "channels": {
            name: {
                "hp": settings[name].hp.as_payload() if settings[name].hp else None,
                "lp": settings[name].lp.as_payload() if settings[name].lp else None,
                "gain_db": gains_db[name],
                "gain_mode": settings[name].gain_mode,
                "gain_auto_db": auto_gains_db[name],
                "delay_ms": delays_s[name] * 1000.0,
                "delay_mode": settings[name].delay_mode,
                "delay_auto_ms": auto_delay_s[name] * 1000.0,
                "inverted": inverted[name],
                "invert_mode": "auto" if settings[name].invert is None else "manual",
                # What this member's own driver would allow, stated whether or
                # not the gain is set to it: a Manual level is only meaningful
                # next to the ceiling it is spending.
                "gain_max_db": max_gains_db[name],
                "max_limit": max_limit[name],
                "max_limit_hz": max_limit_hz[name],
                # A ceiling reached at the edge of the sweep is a ceiling the
                # sweep did not see the far side of: a woofer's real excursion
                # limit usually sits below the lowest solved frequency, and a
                # number that does not say so reads as more headroom than the
                # driver has.
                "max_limit_at_band_edge": max_limit_edge[name],
            }
            for name in members
        },
        "pairs": pairs,
        # Flattened aliases: the legacy payload shape every current reader
        # (results dock, VituixCAD export, field-plane synthesis) still uses.
        "level_match": {
            "enabled": bool(level_matched),
            "target_db": target_db,
            "medians_db": medians_db,
            "gains_db": gains_db,
        },
        "align": bool(aligned_any),
        "reference_angle_degrees": reference_angle_deg,
        "delays_ms": {name: delays_s[name] * 1000.0 for name in members},
        "gains_db": dict(gains_db),
        "pair_eval_hz": dict(zip(pair_names, eval_hz, strict=True)),
        "weight_convention": (
            "weights defined in engineering exp(+jwt); applied to exp(+ikr) "
            "solver fields as complex conjugates"
        ),
        "warnings": warnings,
    }
    max_output = _max_output_payload(
        freqs,
        list(members),
        limits,
        bands,
        gains_db,
        levelled,
        spl[:, 0, on_axis],
    )
    if max_output is not None:
        payload["max_output"] = max_output
        if max_output["unlimited_members"]:
            named = ", ".join(repr(name) for name in max_output["unlimited_members"])
            warnings.append(
                f"maximum output is unknown wherever {named} sets the level: "
                "no Xmax, rated power or amplifier ceiling is known for it"
            )
    return combined, payload
