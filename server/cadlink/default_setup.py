"""WG's default CAD setup: what a first-time model is solved with.

docs/architecture/CAD-OPERATIONS.md, "Project setups". A solve Fusion sends for
a model WG has no recorded settings for is prepared and solved with WG's
default settings instead of waiting for the user. Those defaults are stated
once, in ``shared/solve-defaults.json``, which the frontend's initial solve
settings read as well; this module turns them into the same setup revision a
first-time manual Solve in WG records for that model (``buildCadProjectSetup``
in frontend/src/shell/cadSetupPublisher.ts), with the engine and accuracy
selected in WG.

Nothing here invents a value the model does not state: a source whose return
suggests no mesh size cannot be meshed by default, and the model then waits
for its settings, as before, with a message naming the sources.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any

from .roles import canonical_source_role
from .setup import DEFAULTS_ORIGIN, CadSolveSetup, validate_setup


# Beside the code in the same app layer, bundled or checked out, so
# ``__file__`` finds it wherever the server itself was loaded from.
SOLVE_DEFAULTS_PATH = Path(__file__).resolve().parents[2] / "shared" / "solve-defaults.json"
#: What the run details say about a completed solve made with them. The
#: solve card says "Solved with" only once the run has completed, and
#: "Using WG's default settings" until then (CadSolveCard.tsx).
DEFAULT_SETTINGS_NOTE = "Solved with WG's default settings — change them in WG."
#: What a first-time model waits with when the shipped file cannot be read:
#: WG's packaging is at fault, not anything the user did.
DAMAGED_DEFAULTS_MESSAGE = (
    "WG's default settings file is damaged — reinstall WG or choose settings in WG."
)


class SolveDefaultsDamaged(RuntimeError):
    """``shared/solve-defaults.json`` is missing, unreadable or not the expected shape."""


_NUMBER = (int, float)
# Every value default_setup reads, with the type it must have.
_SHAPE: dict[tuple[str, ...], type | tuple[type, ...]] = {
    ("sweep", "start_hz"): _NUMBER,
    ("sweep", "end_hz"): _NUMBER,
    ("sweep", "points"): int,
    ("sweep", "spacing"): str,
    ("directivity", "angle_start_deg"): _NUMBER,
    ("directivity", "angle_end_deg"): _NUMBER,
    ("directivity", "angle_step_deg"): _NUMBER,
    ("directivity", "distance_m"): _NUMBER,
    ("directivity", "norm_angle_deg"): _NUMBER,
    ("directivity", "diagonal_inclination_deg"): _NUMBER,
    ("directivity", "enabled_axes"): list,
    ("directivity", "observation_origin"): str,
    ("directivity", "spherical_sampling"): bool,
    ("directivity", "field_plane"): bool,
    ("solver", "engine"): str,
    ("solver", "accuracy"): str,
    ("solver", "solver_mode"): str,
    ("solver", "symmetry"): str,
    ("solver", "mesh_validation_mode"): str,
    ("solver", "verbose"): bool,
    ("ground_plane", "enabled"): bool,
    ("ground_plane", "axis"): str,
    ("ground_plane", "height_m"): _NUMBER,
    ("cad", "preparation_symmetry_mode"): str,
    ("cad", "exterior_only"): bool,
    ("cad", "crossover", "family"): str,
    ("cad", "crossover", "order"): int,
    ("cad", "crossover", "band_roles"): list,
    ("cad", "crossover", "role_crossovers_hz"): list,
}


def _check_shape(data: Any) -> None:
    for path, kind in _SHAPE.items():
        value = data
        for key in path:
            if not isinstance(value, dict) or key not in value:
                raise SolveDefaultsDamaged(f"{'.'.join(path)} is missing")
            value = value[key]
        if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
            raise SolveDefaultsDamaged(f"{'.'.join(path)} has the wrong type")
    for item in data["cad"]["crossover"]["role_crossovers_hz"]:
        if not isinstance(item, dict) or not {"lower", "upper", "hz"} <= set(item):
            raise SolveDefaultsDamaged("cad.crossover.role_crossovers_hz has a malformed entry")


@lru_cache(maxsize=1)
def solve_defaults() -> dict[str, Any]:
    """The shared default solve settings (``shared/solve-defaults.json``), shape-checked.

    Raises ``SolveDefaultsDamaged`` when the shipped file cannot be used.
    """

    try:
        data = json.loads(SOLVE_DEFAULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SolveDefaultsDamaged(f"{SOLVE_DEFAULTS_PATH.name}: {exc}") from exc
    _check_shape(data)
    return data


def _polar_config(directivity: Mapping[str, Any]) -> dict[str, Any]:
    """The directivity grid, exactly as the frontend's ``polarConfigFromUi`` builds it."""

    start = directivity["angle_start_deg"]
    end = directivity["angle_end_deg"]
    step = directivity["angle_step_deg"]
    intervals = (end - start) / step
    nearest = math.floor(intervals + 0.5)
    resolved = (
        nearest
        if abs(intervals - nearest) <= max(1e-9, abs(intervals) * 1e-12)
        else math.floor(intervals)
    )
    return {
        "angle_range": [start, end, max(2, resolved + 1)],
        "angle_step": step,
        "distance": directivity["distance_m"],
        "norm_angle": directivity["norm_angle_deg"],
        "inclination": directivity["diagonal_inclination_deg"],
        "enabled_axes": list(directivity["enabled_axes"]),
        "observation_origin": directivity["observation_origin"],
        "spherical_sampling": directivity["spherical_sampling"],
        "field_plane": directivity["field_plane"],
    }


def _drive_channels(sources: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One channel per default drive channel id, in source order (``groupChannels``)."""

    channels: dict[str, dict[str, Any]] = {}
    for source in sources:
        channel_id = str(source.get("default_drive_channel_id") or "").strip()
        if not channel_id:
            continue
        channel = channels.setdefault(
            channel_id, {"id": channel_id, "source_ids": [], "motion": "normal"}
        )
        channel["source_ids"].append(str(source["id"]))
    return list(channels.values())


def _combine(
    channels: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]],
    sweep: Mapping[str, Any],
    crossover: Mapping[str, Any],
) -> dict[str, Any] | None:
    """The combined output an untouched multi-channel model submits.

    The frontend's ``combineWire`` for a model nobody has edited: on for two
    or more channels, chained lowest band first, each adjacent pair crossed
    over at its role default (or a log-spaced frequency inside the sweep),
    LR4, automatic gain, delay and polarity, referenced to the highest band.
    """

    if len(channels) < 2:
        return None
    rank = {role: index for index, role in enumerate(crossover["band_roles"])}
    role_hz = {
        (item["lower"], item["upper"]): item["hz"] for item in crossover["role_crossovers_hz"]
    }
    role_of = {
        str(source["id"]): canonical_source_role(str(source.get("role") or ""))
        for source in sources
    }

    def band(channel: Mapping[str, Any]) -> str | None:
        roles = [role_of.get(source_id, "") for source_id in channel["source_ids"]]
        banded = sorted((role for role in roles if role in rank), key=rank.__getitem__)
        return banded[0] if banded else None

    ordered = sorted(
        enumerate(channels),
        key=lambda item: (
            rank[band(item[1])] if band(item[1]) is not None else math.inf,
            item[0],
        ),
    )
    members = [channel["id"] for _, channel in ordered]
    roles = [band(channel) for _, channel in ordered]
    start, end = float(sweep["start_hz"]), float(sweep["end_hz"])
    log_start = math.log(max(1.0, start))
    log_end = math.log(max(start + 1.0, end))
    crossovers: list[float] = []
    for index in range(len(members) - 1):
        spaced = math.floor(
            math.exp(log_start + ((index + 1) * (log_end - log_start)) / len(members)) + 0.5
        )
        lower, upper = roles[index], roles[index + 1]
        default = role_hz.get((lower, upper)) if lower and upper else None
        inside = default is not None and start <= default <= end
        crossovers.append(default if inside else spaced)

    def section(hz: float) -> dict[str, Any]:
        return {"family": crossover["family"], "order": crossover["order"], "fc_hz": hz}

    return {
        "members": members,
        "reference": members[-1],
        "channels": {
            member: {
                "hp": section(crossovers[index - 1]) if index > 0 else None,
                "lp": section(crossovers[index]) if index < len(crossovers) else None,
                "gain": {"mode": "auto"},
                "delay": {"mode": "auto"},
                "invert": None,
            }
            for index, member in enumerate(members)
        },
    }


def unsized_sources(manifest: Mapping[str, Any]) -> list[str]:
    """Sources the defaults cannot mesh: their return suggests no mesh size."""

    return [
        str(source.get("id"))
        for source in manifest.get("sources") or []
        if isinstance(source, Mapping) and source.get("suggested_resolution_mm") is None
    ]


def default_setup(
    manifest: Mapping[str, Any], selection: Mapping[str, str] | None = None
) -> CadSolveSetup:
    """WG's default setup for a snapshot, with the engine and accuracy selected in WG.

    Raises ``ValueError`` naming what the defaults cannot supply for this
    model, and ``SolveDefaultsDamaged`` when the shipped file cannot be used.
    """

    defaults = solve_defaults()
    sources = [source for source in manifest.get("sources") or [] if isinstance(source, Mapping)]
    if not sources:
        raise ValueError("the model states no sources")
    missing = unsized_sources(manifest)
    if missing:
        raise ValueError(
            "its return suggests no mesh size for "
            + ", ".join(missing)
            + ", and WG does not guess one"
        )
    sizes = {str(source["id"]): source["suggested_resolution_mm"] for source in sources}
    coarsest = max(sizes.values())
    channels = _drive_channels(sources)
    sweep = defaults["sweep"]
    cad = defaults["cad"]
    solver = defaults["solver"]
    selection = selection or {}
    combine = _combine(channels, sources, sweep, cad["crossover"])
    ground = defaults["ground_plane"]
    options: dict[str, Any] = {
        "engine": selection.get("engine") or solver["engine"],
        "solver_mode": solver["solver_mode"],
        "symmetry": solver["symmetry"],
        "mesh_validation_mode": solver["mesh_validation_mode"],
        "verbose": solver["verbose"],
        "frequency_spacing": sweep["spacing"],
        "polar_config": _polar_config(defaults["directivity"]),
        "frequency_range": [sweep["start_hz"], sweep["end_hz"]],
        "num_frequencies": sweep["points"],
        **({"ground_plane": dict(ground)} if ground["enabled"] else {}),
    }
    accuracy = selection.get("accuracy") or solver["accuracy"]
    if accuracy == "accurate":
        options["accuracy"] = "accurate"
    return validate_setup(
        {
            "geometry": {
                # No driver: every channel is solved at the model's own drive,
                # as a first-time manual solve is, so no drive voltage either.
                "drive_channels": channels,
                "mesh": {
                    "rigid_size_mm": coarsest,
                    "transition_mm": coarsest,
                    "source_size_mm": sizes,
                },
                "skipped_source_ids": [],
                "exterior_only": cad["exterior_only"],
                **({"combine": combine} if combine else {}),
            },
            "options": options,
            "preparation": {
                "area_drift_overrides": [],
                "symmetry_mode": cad["preparation_symmetry_mode"],
            },
            "driver_references": {},
            "origin": DEFAULTS_ORIGIN,
        }
    )


__all__ = [
    "DAMAGED_DEFAULTS_MESSAGE",
    "DEFAULT_SETTINGS_NOTE",
    "SolveDefaultsDamaged",
    "SOLVE_DEFAULTS_PATH",
    "default_setup",
    "solve_defaults",
    "unsized_sources",
]
