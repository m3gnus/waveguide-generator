"""Mark result frequencies that failed the radiated-power consistency check.

Every native solve records two independent estimates of radiated power per
frequency: the flux over the driven faces and the far-field integral over the
observation sphere (``metadata.radiated_power``). When the exterior solution is
sound they agree. A channel whose estimates disagree by more than
``POWER_AGREEMENT_THRESHOLD_DB`` inside its recorded frequency-validity band, or
whose driven-face power is nonpositive or missing there, is *unqualified* at
that frequency. For complex-k this can indicate sensitivity to the default
stabilisation shift; for Burton–Miller it is a neutral failure that may arise
from the mesh or source. Neither should be read as an answer there.

This module only contains the failure. It never changes a stored level and never
applies a retrospective correction: the disagreement is evidence that the
number is unreliable, not an offset to add to it.

Contract (additive; every field is new and older readers ignore it):

``metadata.power_qualification`` on every result payload -- the parametric
envelope itself, or each channel of a multi-channel envelope::

    version            1
    status             "qualified" | "unqualified" | "unknown"
    evaluated          "solve" (computed when the result was stored) or
                       "read_time" (derived from an archived record when it is
                       opened; kept if that record is later recombined)
    threshold_db       0.5
    validity_max_hz    governing validity limit, or null when none recorded
    frequency_status   parallel to ``frequencies``: "qualified",
                       "unqualified", "outside_validity" or "unchecked"
    frequency_reasons  parallel reason code, null where not unqualified
    unqualified_ranges [{start_hz, end_hz, count, reasons, channels}]
                       contiguous runs of unqualified samples
    reasons            sorted reason codes present anywhere in the channel
    unknown_reason     why a status is "unknown", else null
    worst              {frequency_hz, agreement_db} of the largest in-band
                       mismatch, or null
    provenance         {engine, package, formulation, complex_k_shift,
                       package_version, solver_pin, missing, recorded};
                       ``recorded`` is false when the formulation, a
                       complex-k shift, or the solver pin is absent
    members            combined channel only: member id -> status
    unqualified_channels  combined channel only: members that made it so
    message            the user-facing sentence when unqualified, else null

A multi-channel envelope also carries ``metadata.power_qualification_summary``
(``{version, status, channels}``). ``metadata.power_qualification_version``
marks a record whose flags were persisted with it, so the read path can serve
it byte-for-byte. A payload with no frequency axis carries no flags at all.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

POWER_QUALIFICATION_VERSION = 1
POWER_AGREEMENT_THRESHOLD_DB = 0.5

#: The key whose presence in stored JSON text proves the flags were persisted.
PERSISTED_MARKER = f'"power_qualification_version": {POWER_QUALIFICATION_VERSION}'

UNQUALIFIED_MESSAGE = (
    "Unqualified: this channel's result is sensitive to solver stabilisation "
    "here; treat it as unreliable until re-solved with a qualified solver. "
    "Re-solve with Accurate."
)
UNQUALIFIED_BM_MESSAGE = (
    "Unqualified: driven-face and far-field power do not balance here. "
    "Check the mesh, source and frequency range."
)
UNQUALIFIED_NEUTRAL_MESSAGE = (
    "Unqualified: driven-face and far-field power do not balance here. "
    "Treat this result as unreliable at the affected frequencies."
)


def _unqualified_message(formulation: str | None) -> str:
    if formulation and formulation.startswith("complex_k"):
        return UNQUALIFIED_MESSAGE
    if formulation == "burton_miller":
        return UNQUALIFIED_BM_MESSAGE
    return UNQUALIFIED_NEUTRAL_MESSAGE

REASON_POWER_MISMATCH = "power_mismatch"
REASON_NONPOSITIVE_FACE_POWER = "nonpositive_face_power"
REASON_NONFINITE_FACE_POWER = "nonfinite_face_power"
REASON_INVALID_SPHERE_POWER = "invalid_sphere_power"
REASON_MEMBER_UNQUALIFIED = "member_unqualified"

UNKNOWN_POWER_CHECK_UNAVAILABLE = "power_check_unavailable"
UNKNOWN_PROVENANCE_MISSING = "provenance_missing"
UNKNOWN_MEMBER = "member_unknown"

_ENGINE_PACKAGES = {
    "metal": "hornlab-metal-bem",
    "circsym": "hornlab-metal-bem",
    "bempp": "hornlab-bempp-bem",
    "beat": "hornlab-beat-bem",
    "beat-cpu": "hornlab-beat-bem",
}
_FORMULATION_BLOCKS = ("solver_engine", "metal", "axisym", "bempp", "beat")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _frequencies(payload: Mapping[str, Any]) -> list[float | None]:
    raw = payload.get("frequencies")
    if not isinstance(raw, list):
        raw = _mapping(payload.get("spl_on_axis")).get("frequencies")
    return [_number(value) for value in raw] if isinstance(raw, list) else []


def _series(block: Mapping[str, Any], key: str, count: int) -> list[float | None]:
    raw = block.get(key)
    values = [_number(value) for value in raw] if isinstance(raw, list) else []
    return (values + [None] * count)[:count]


def _source_ids(metadata: Mapping[str, Any]) -> list[str]:
    raw = metadata.get("source_ids")
    if not isinstance(raw, list):
        return []
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()]


def channel_validity_max_hz(
    payload: Mapping[str, Any],
    wrapper: Mapping[str, Any] | None = None,
) -> float | None:
    """The governing validity limit for one payload, joined like the frontend.

    Per-source evidence lives on the multi-channel wrapper while membership
    lives on the channel. Missing membership is resolved only when exactly one
    join is possible; guessing another channel's ceiling is worse than none.
    """

    metadata = _mapping(payload.get("metadata"))
    wrapper = wrapper if wrapper is not None else payload
    wrapper_metadata = _mapping(wrapper.get("metadata"))
    per_source = metadata.get("per_source_frequency_validity")
    if not isinstance(per_source, Mapping):
        per_source = wrapper_metadata.get("per_source_frequency_validity")
    if not isinstance(per_source, Mapping) or not per_source:
        return None
    declared = _source_ids(metadata)
    channels = wrapper.get("channels")
    channel_count = len(channels) if isinstance(channels, Mapping) else 0
    if declared:
        relevant = declared
    elif not isinstance(channels, Mapping) or channel_count == 1 or len(per_source) == 1:
        relevant = [str(key) for key in per_source]
    else:
        relevant = []
    limits = [
        limit
        for source_id in relevant
        if (limit := _number(_mapping(per_source.get(source_id)).get(
            "effective_max_valid_frequency_hz"
        ))) is not None
        and limit > 0.0
    ]
    return min(limits) if limits else None


def _provenance(
    payload: Mapping[str, Any], wrapper: Mapping[str, Any] | None
) -> dict[str, Any]:
    metadata = _mapping(payload.get("metadata"))
    wrapper_metadata = _mapping((wrapper or {}).get("metadata"))
    formulation: Any = None
    shift: Any = None
    for source in (metadata, wrapper_metadata):
        for block_name in _FORMULATION_BLOCKS:
            block = _mapping(source.get(block_name))
            if formulation is None and isinstance(block.get("formulation"), str):
                formulation = block["formulation"]
            if shift is None and _number(block.get("complex_k_shift")) is not None:
                shift = _number(block.get("complex_k_shift"))
    engine_block = _mapping(metadata.get("solver_engine")) or _mapping(
        wrapper_metadata.get("solver_engine")
    )
    backend = metadata.get("solver_backend") or wrapper_metadata.get("solver_backend")
    engine = metadata.get("engine") if isinstance(metadata.get("engine"), str) else None
    package = (
        engine_block.get("package")
        if isinstance(engine_block.get("package"), str)
        else engine
        if engine and (engine.startswith("hornlab-") or engine == "beat-engine")
        else _ENGINE_PACKAGES.get(str(backend)) if backend else None
    )
    device = _mapping(metadata.get("device_interface"))
    selected = _mapping(device.get(str(device.get("selected")))) if device else {}
    package_version = engine_block.get("package_version") or selected.get("version")
    provenance_block = _mapping((wrapper or payload).get("provenance"))
    pins = _mapping(provenance_block.get("installed_dependency_shas")) or _mapping(
        provenance_block.get("dependency_shas")
    )
    solver_pin = pins.get(package) if package else None
    solver_pin = solver_pin if isinstance(solver_pin, str) and solver_pin else None
    # A clean check qualifies a result only when it is known what produced it:
    # the formulation, its stabilisation shift when it uses one, and the exact
    # solver commit. Any gap leaves the result "unknown", never "qualified".
    missing = []
    if not isinstance(formulation, str):
        missing.append("formulation")
    elif formulation.startswith("complex_k") and shift is None:
        missing.append("complex_k_shift")
    if solver_pin is None:
        missing.append("solver_pin")
    return {
        "engine": backend if isinstance(backend, str) else None,
        "package": package,
        "formulation": formulation,
        "complex_k_shift": shift,
        "package_version": package_version if isinstance(package_version, str) else None,
        "solver_pin": solver_pin,
        "missing": missing,
        "recorded": not missing,
    }


def _ranges(
    frequencies: Sequence[float | None],
    status: Sequence[str],
    reasons: Sequence[str | None],
    channels: Sequence[Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    """Contiguous unqualified runs in ascending frequency order."""

    order = sorted(
        (index for index, value in enumerate(frequencies) if value is not None),
        key=lambda index: frequencies[index],  # type: ignore[arg-type,return-value]
    )
    ranges: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for index in order:
        if status[index] != "unqualified":
            current = None
            continue
        frequency = float(frequencies[index])  # type: ignore[arg-type]
        if current is None:
            current = {
                "start_hz": frequency,
                "end_hz": frequency,
                "count": 0,
                "reasons": [],
                "channels": [],
            }
            ranges.append(current)
        current["end_hz"] = frequency
        current["count"] += 1
        reason = reasons[index]
        if reason and reason not in current["reasons"]:
            current["reasons"].append(reason)
        for channel in (channels[index] if channels else ()):
            if channel not in current["channels"]:
                current["channels"].append(channel)
    for item in ranges:
        item["reasons"].sort()
        item["channels"].sort()
    return ranges


def qualify_channel(
    payload: Mapping[str, Any],
    wrapper: Mapping[str, Any] | None = None,
    *,
    evaluated: str = "solve",
) -> dict[str, Any]:
    """Per-frequency qualification of one directly solved result payload."""

    frequencies = _frequencies(payload)
    count = len(frequencies)
    metadata = _mapping(payload.get("metadata"))
    power = metadata.get("radiated_power")
    validity = channel_validity_max_hz(payload, wrapper)
    provenance = _provenance(payload, wrapper)
    status = ["unchecked"] * count
    reasons: list[str | None] = [None] * count
    worst: dict[str, float] | None = None
    available = False
    if isinstance(power, Mapping):
        surface = _series(power, "surface_w", count)
        sphere = _series(power, "sphere_w", count)
        agreement = _series(power, "agreement_db", count)
        # The block is written only when the solver returned a power series,
        # so its presence means the check ran. A null inside it is an invalid
        # computed value -- a visible failure -- never a check that did not run;
        # only a block with neither series is "unknown".
        available = isinstance(power.get("surface_w"), list) or isinstance(
            power.get("sphere_w"), list
        )
        for index, frequency in enumerate(frequencies if available else ()):
            if frequency is None:
                continue
            if validity is not None and frequency > validity:
                status[index] = "outside_validity"
                continue
            face = surface[index]
            far = sphere[index]
            if face is None:
                reason = REASON_NONFINITE_FACE_POWER
            elif face <= 0.0:
                reason = REASON_NONPOSITIVE_FACE_POWER
            elif far is None or far <= 0.0:
                reason = REASON_INVALID_SPHERE_POWER
            else:
                ratio_db = agreement[index]
                if ratio_db is None:
                    ratio_db = 10.0 * math.log10(far / face)
                if worst is None or abs(ratio_db) > abs(worst["agreement_db"]):
                    worst = {"frequency_hz": frequency, "agreement_db": ratio_db}
                reason = (
                    REASON_POWER_MISMATCH
                    if abs(ratio_db) > POWER_AGREEMENT_THRESHOLD_DB
                    else None
                )
            status[index] = "unqualified" if reason else "qualified"
            reasons[index] = reason
    unqualified = "unqualified" in status
    unknown_reason: str | None = None
    if unqualified:
        overall = "unqualified"
    elif not available:
        overall, unknown_reason = "unknown", UNKNOWN_POWER_CHECK_UNAVAILABLE
    elif not provenance["recorded"]:
        overall, unknown_reason = "unknown", UNKNOWN_PROVENANCE_MISSING
    else:
        overall = "qualified"
    return {
        "version": POWER_QUALIFICATION_VERSION,
        "status": overall,
        "evaluated": evaluated,
        "threshold_db": POWER_AGREEMENT_THRESHOLD_DB,
        "validity_max_hz": validity,
        "frequency_status": status,
        "frequency_reasons": reasons,
        "unqualified_ranges": _ranges(frequencies, status, reasons),
        "reasons": sorted({reason for reason in reasons if reason}),
        "unknown_reason": unknown_reason,
        "worst": worst,
        "provenance": provenance,
        "message": _unqualified_message(provenance["formulation"]) if unqualified else None,
    }


def _combine_members(metadata: Mapping[str, Any]) -> list[str] | None:
    combine = _mapping(metadata.get("combine"))
    members = combine.get("members")
    if not isinstance(members, list):
        members = metadata.get("derived_from_channels")
    if not isinstance(members, list):
        return None
    return [str(member) for member in members]


def qualify_combined(
    payload: Mapping[str, Any],
    member_flags: Mapping[str, Mapping[str, Any]],
    member_payloads: Mapping[str, Mapping[str, Any]],
    members: Iterable[str],
    wrapper: Mapping[str, Any] | None = None,
    *,
    evaluated: str = "solve",
) -> dict[str, Any]:
    """A crossover sum inherits every contributing member's unqualified samples.

    The sum has no driven face of its own, so its only evidence is its members'.
    Inside the sum's own validity band (the lowest ceiling among the sources it
    sums), a member unqualified at a frequency makes the sum unqualified there,
    naming the member. Above that band nothing propagates: the sum is not a
    claim there. ``members`` records each member's standing inside the band.
    """

    frequencies = _frequencies(payload)
    count = len(frequencies)
    members = list(members)
    validity = channel_validity_max_hz(payload, wrapper)
    status = ["qualified"] * count
    reasons: list[str | None] = [None] * count
    culprits: list[list[str]] = [[] for _ in range(count)]
    for index, frequency in enumerate(frequencies):
        if frequency is None:
            status[index] = "unchecked"
        elif validity is not None and frequency > validity:
            status[index] = "outside_validity"
    member_status: dict[str, str] = {}
    for member in members:
        flags = member_flags.get(member)
        if not flags or flags.get("status") not in {"qualified", "unqualified"}:
            member_status[member] = "unknown"
            continue
        member_status[member] = "qualified"
        member_frequencies = _frequencies(member_payloads.get(member, {}))
        member_state = flags.get("frequency_status") or []
        flagged = [
            frequency
            for frequency, state in zip(member_frequencies, member_state)
            if state == "unqualified" and frequency is not None
        ]
        for index, frequency in enumerate(frequencies):
            if status[index] in {"unchecked", "outside_validity"}:
                continue
            if any(
                math.isclose(frequency, value, rel_tol=1e-9, abs_tol=1e-9)
                for value in flagged
            ):
                status[index] = "unqualified"
                reasons[index] = REASON_MEMBER_UNQUALIFIED
                culprits[index].append(member)
                member_status[member] = "unqualified"
    unqualified_channels = sorted(
        {member for items in culprits for member in items}
    )
    unknown_reason: str | None = None
    if unqualified_channels:
        overall = "unqualified"
    elif any(value != "qualified" for value in member_status.values()) or not members:
        overall, unknown_reason = "unknown", UNKNOWN_MEMBER
    else:
        overall = "qualified"
    return {
        "version": POWER_QUALIFICATION_VERSION,
        "status": overall,
        "evaluated": evaluated,
        "threshold_db": POWER_AGREEMENT_THRESHOLD_DB,
        "validity_max_hz": validity,
        "frequency_status": status,
        "frequency_reasons": reasons,
        "unqualified_ranges": _ranges(frequencies, status, reasons, culprits),
        "reasons": sorted({reason for reason in reasons if reason}),
        "unknown_reason": unknown_reason,
        "worst": None,
        "provenance": _provenance(payload, wrapper),
        "members": member_status,
        "unqualified_channels": unqualified_channels,
        "message": _unqualified_message(_provenance(payload, wrapper)["formulation"]) if unqualified_channels else None,
    }


def _worst_status(statuses: Iterable[str]) -> str:
    values = list(statuses)
    if "unqualified" in values:
        return "unqualified"
    if not values or "unknown" in values:
        return "unknown"
    return "qualified"


def _has_frequency_axis(results: Mapping[str, Any]) -> bool:
    channels = results.get("channels")
    if isinstance(channels, Mapping) and channels:
        return True
    return bool(_frequencies(results))


def _prior_evaluation(results: Mapping[str, Any]) -> str | None:
    """How the flags already on ``results`` were produced, if it has any."""

    payloads: list[Any] = [results]
    channels = results.get("channels")
    if isinstance(channels, Mapping):
        payloads.extend(channels.values())
    for payload in payloads:
        flags = _mapping(_mapping(_mapping(payload).get("metadata")).get(
            "power_qualification"
        ))
        if flags.get("evaluated") in {"solve", "read_time"}:
            return str(flags["evaluated"])
    return None


def annotate_results(
    results: Mapping[str, Any], *, evaluated: str | None = None
) -> dict[str, Any]:
    """Return a copy of ``results`` with qualification flags on every payload.

    The input is never mutated. Existing flags are recomputed, so a recombined
    crossover sum always reflects the members it now sums. ``evaluated``
    defaults to "solve", except that a record whose flags were derived at read
    time (an archived run, then recombined) keeps saying so. A payload with no
    frequency axis at all is returned as an unchanged copy: there is nothing
    to qualify.
    """

    prior = _prior_evaluation(results)
    if evaluated is None:
        evaluated = "read_time" if prior == "read_time" else "solve"
    if not _has_frequency_axis(results):
        return dict(results)
    # Shallow copies along the path that changes: a finished sweep runs to
    # megabytes and every other branch is shared with the input unchanged.
    annotated = dict(results)
    metadata = dict(_mapping(annotated.get("metadata")))
    channels = annotated.get("channels")
    if isinstance(channels, Mapping) and channels:
        channels = {key: value for key, value in channels.items()}
        flags: dict[str, dict[str, Any]] = {}
        combined: dict[str, list[str]] = {}
        for channel_id, payload in channels.items():
            if not isinstance(payload, Mapping):
                continue
            members = _combine_members(_mapping(payload.get("metadata")))
            if members is not None:
                combined[str(channel_id)] = members
                continue
            flags[str(channel_id)] = qualify_channel(
                payload, annotated, evaluated=evaluated
            )
        for channel_id, members in combined.items():
            flags[channel_id] = qualify_combined(
                channels[channel_id],
                flags,
                {member: channels.get(member) or {} for member in members},
                members,
                annotated,
                evaluated=evaluated,
            )
        for channel_id, value in flags.items():
            payload = dict(channels[channel_id])
            payload["metadata"] = {
                **_mapping(payload.get("metadata")),
                "power_qualification": value,
            }
            channels[channel_id] = payload
        annotated["channels"] = channels
        metadata["power_qualification_summary"] = {
            "version": POWER_QUALIFICATION_VERSION,
            "status": _worst_status(value["status"] for value in flags.values()),
            "channels": {key: value["status"] for key, value in flags.items()},
        }
    else:
        metadata["power_qualification"] = qualify_channel(
            annotated, annotated, evaluated=evaluated
        )
    if evaluated == "read_time" and prior is None:
        # Served in memory only; the next read derives the flags again.
        metadata.pop("power_qualification_version", None)
    else:
        # Persisted with the result, so the read path serves it verbatim.
        metadata["power_qualification_version"] = POWER_QUALIFICATION_VERSION
    annotated["metadata"] = metadata
    return annotated


def annotate_stored_text(text: str) -> str | None:
    """Read-time flags for an archived record, or None when it needs none.

    A record persisted with solve-time flags is returned untouched by the
    caller. An older one is annotated in memory only: the stored bytes are
    never rewritten, and nothing about a level changes.
    """

    import json

    if PERSISTED_MARKER in text:
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict) or not _has_frequency_axis(decoded):
        return None
    return json.dumps(
        annotate_results(decoded, evaluated="read_time"),
        allow_nan=False,
    )


__all__ = [
    "PERSISTED_MARKER",
    "POWER_AGREEMENT_THRESHOLD_DB",
    "POWER_QUALIFICATION_VERSION",
    "UNQUALIFIED_MESSAGE",
    "annotate_results",
    "annotate_stored_text",
    "channel_validity_max_hz",
    "qualify_channel",
    "qualify_combined",
]
