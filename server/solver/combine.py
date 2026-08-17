"""Combine solved drive-channel bases into one summed response.

Ports the Fusion add-in's LR4 time-aligned on-axis sum
(``solve_fusion_wg_metal.py`` — crossover weights, level match,
phase-equivalent alignment delays) onto WG's solve-time channel results.
Because every channel of one job shares a single observation grid, the
add-in's grid harmonisation stage has no equivalent here.

Convention boundary (the only one): weights are defined in the engineering
``e^{+jωt}`` convention, where an LR4 transfer function and a delay
``e^{-jωτ}`` mean what filter theory says they mean. Raw solver fields are
``e^{-iωt}`` (``exp(+ikr)``), so a weight is applied to a raw field as its
complex conjugate. See CAD-LINK-PHASE3.md §2.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any, Mapping

import numpy as np

_TWO_PI = 2.0 * np.pi

# Serialized per-channel complex bases (persisted with a job) so a new
# crossover can recombine in milliseconds without re-solving. The fields stay
# in the solver's exp(+ikr) convention and the file says so.
CHANNEL_BASES_VERSION = 1
_BASES_PHASE_CONVENTION = "solver_exp_plus_ikr"


def serialize_channel_bases(results_by_id: Mapping[str, Any]) -> bytes:
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


def _interp_complex(freqs: np.ndarray, values: np.ndarray, target_hz: float) -> complex:
    """Interpolate magnitude and unwrapped phase separately at ``target_hz``."""

    mag = np.interp(float(target_hz), freqs, np.abs(values))
    phase = np.interp(float(target_hz), freqs, np.unwrap(np.angle(values)))
    return complex(mag * np.exp(1j * phase))


def _ratio_group_delay_s(
    freqs: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    target_hz: float,
    *,
    rel_window: float = 0.15,
) -> float | None:
    """Local group delay of ``lower/upper`` at ``target_hz``: ``d/dω arg``.

    Used only to pick the correct period branch of the phase-value
    alignment, so a rough slope from a linear fit is enough.
    """

    freqs = np.asarray(freqs, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.asarray(lower, dtype=np.complex128) / np.asarray(
            upper, dtype=np.complex128
        )
    if not np.all(np.isfinite(ratio)):
        return None
    window = (freqs >= target_hz * (1.0 - rel_window)) & (
        freqs <= target_hz * (1.0 + rel_window)
    )
    if np.count_nonzero(window) < 3:
        centre = int(np.argmin(np.abs(freqs - target_hz)))
        lo_i = max(0, centre - 2)
        hi_i = min(freqs.size, centre + 3)
        sel = np.zeros(freqs.size, dtype=bool)
        sel[lo_i:hi_i] = True
        window = sel
    f_sel = freqs[window]
    if f_sel.size < 2:
        return None
    phase = np.unwrap(np.angle(ratio))[window]
    slope = float(np.polyfit(_TWO_PI * f_sel, phase, 1)[0])
    if not np.isfinite(slope):
        return None
    return slope


def _phase_equivalent_delay_s(
    phase_diff_rad: float,
    freq_hz: float,
    *,
    group_delay_hint_s: float | None = None,
) -> float:
    period_s = 1.0 / float(freq_hz)
    principal_s = float(phase_diff_rad) / (_TWO_PI * float(freq_hz))
    if group_delay_hint_s is None or not np.isfinite(group_delay_hint_s):
        # Smallest |delay| branch — correct whenever the true inter-driver
        # arrival gap is under half a period at the crossover.
        return (principal_s + 0.5 * period_s) % period_s - 0.5 * period_s
    # The phase at fc fixes the delay only modulo one period; the group delay
    # of the unfiltered driver ratio is the physical arrival offset that
    # selects the cycle. Global normalisation restores non-negative delays.
    n = round((float(group_delay_hint_s) - principal_s) / period_s)
    return principal_s + n * period_s


def _spl_db(pressure: np.ndarray, reference_pa: float) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(np.abs(pressure), 1.0e-30) / reference_pa)


def _level_match_gains_db(
    freqs: np.ndarray,
    filtered_on_axis: Mapping[str, np.ndarray],
    members: list[str],
    crossovers_hz: list[float],
) -> tuple[dict[str, float], dict[str, float], float]:
    edges = [float(freqs[0]), *[float(xo) for xo in crossovers_hz], float(freqs[-1])]
    medians: dict[str, float] = {}
    for index, name in enumerate(members):
        band = (freqs >= edges[index]) & (freqs <= edges[index + 1])
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


def combine_drive_channels(
    results_by_id: Mapping[str, Any],
    *,
    members: list[str],
    crossovers_hz: list[float],
    level_match: bool = True,
    align: bool = True,
    member_validity_hz: Mapping[str, float] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Sum member channel results into one synthetic native-shaped result.

    ``results_by_id`` holds the frequency-sorted native results. Returns the
    synthetic result (same duck type ``build_solver_response`` consumes) and
    the ``combine`` metadata payload. Raises ``ValueError`` when the members
    do not share one observation grid — a solver contract violation, not a
    user error.
    """

    warnings: list[str] = []
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
    outside_band = [
        float(value)
        for value in crossovers_hz
        if float(value) < solved_band[0] or float(value) > solved_band[1]
    ]
    if outside_band:
        warnings.append(
            f"combine crossovers_hz {outside_band} lie outside the solved band "
            f"[{solved_band[0]:g}, {solved_band[1]:g}] Hz; level matching may "
            "fall back to a full-band median"
        )

    if freqs.size < 3:
        warnings.append(
            "fewer than 3 solved frequencies: alignment period-branch selection "
            "has no group-delay estimate and uses the smallest-|delay| branch"
        )
    if member_validity_hz:
        for index, xo in enumerate(crossovers_hz):
            for name in (members[index], members[index + 1]):
                limit = member_validity_hz.get(name)
                if limit is not None and float(xo) > float(limit):
                    warnings.append(
                        f"crossover {float(xo):g} Hz is above channel {name!r} "
                        f"source validity limit {float(limit):g} Hz"
                    )

    # Engineering-domain on-axis pressures drive every weight decision.
    pressures_eng = {
        name: np.conjugate(fields[name][:, 0, on_axis]) for name in members
    }
    lr4 = lr4_chain_weights(freqs, members, crossovers_hz)
    filtered = {name: pressures_eng[name] * lr4[name] for name in members}

    if level_match:
        gains_db, medians_db, target_db = _level_match_gains_db(
            freqs, filtered, members, crossovers_hz
        )
    else:
        gains_db = {name: 0.0 for name in members}
        medians_db = {}
        target_db = None
    gain_linear = {name: 10.0 ** (gains_db[name] / 20.0) for name in members}

    delays_s = {members[-1]: 0.0}
    pair_eval_hz: dict[str, float] = {}
    if align and len(members) > 1:
        for index in range(len(members) - 2, -1, -1):
            lower, upper = members[index], members[index + 1]
            eval_hz = float(
                min(max(crossovers_hz[index], freqs[0]), freqs[-1])
            )
            pair_eval_hz[f"{lower}-{upper}"] = eval_hz
            lower_at_xo = _interp_complex(freqs, filtered[lower], eval_hz)
            upper_at_xo = _interp_complex(freqs, filtered[upper], eval_hz)
            if abs(lower_at_xo) <= 0.0 or abs(upper_at_xo) <= 0.0:
                warnings.append(
                    f"{lower}/{upper} pair pressure vanishes at {eval_hz:g} Hz; "
                    "alignment delay for this pair defaults to 0"
                )
                delays_s[lower] = delays_s[upper]
                continue
            # The unfiltered ratio gives the cleaner arrival estimate: the
            # LR4 phases cancel at fc but their group delays do not.
            hint = _ratio_group_delay_s(
                freqs, pressures_eng[lower], pressures_eng[upper], eval_hz
            )
            pair_delay = _phase_equivalent_delay_s(
                float(np.angle(lower_at_xo / upper_at_xo)),
                eval_hz,
                group_delay_hint_s=hint,
            )
            delays_s[lower] = delays_s[upper] + pair_delay
        min_delay = min(delays_s.values())
        delays_s = {name: value - min_delay for name, value in delays_s.items()}
    else:
        delays_s = {name: 0.0 for name in members}

    weights_eng = {
        name: lr4[name]
        * gain_linear[name]
        * np.exp(-1j * _TWO_PI * freqs * delays_s[name])
        for name in members
    }
    # The single convention boundary: engineering weight -> raw-field factor.
    weights_raw = {name: np.conjugate(weights_eng[name]) for name in members}

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
    payload: dict[str, Any] = {
        "type": "lr4_time_aligned_sum",
        "members": list(members),
        "crossovers_hz": [float(value) for value in crossovers_hz],
        "level_match": {
            "enabled": bool(level_match),
            "target_db": target_db,
            "medians_db": medians_db,
            "gains_db": gains_db,
        },
        "align": bool(align),
        "reference_angle_degrees": reference_angle_deg,
        "delays_ms": {name: delays_s[name] * 1000.0 for name in members},
        "pair_eval_hz": pair_eval_hz,
        "weight_convention": (
            "weights defined in engineering exp(+jwt); applied to exp(+ikr) "
            "solver fields as complex conjugates"
        ),
        "warnings": warnings,
    }
    return combined, payload
