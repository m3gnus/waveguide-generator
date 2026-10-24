"""Effective imported polar coverage, independent of stored user intent."""

from collections.abc import Mapping
import math
from typing import Any

from server.jobs.models import SolveRequest

_POLAR_AXIS_ORDER = ("horizontal", "vertical", "diagonal")
_DEFAULT_DIAGONAL_INCLINATION_DEG = 45


def widen_polar_to_derivation(request: SolveRequest, derivation: Any) -> SolveRequest:
    """Never ask for a narrower polar grid than the ingestion derived.

    Planning and submission share this projection; it never edits a stored setup
    or the caller's request. The range covers every derived extent at the
    resolved input step, with half-up sample rounding. Full-circle axes are
    enabled; an axis without extents is pinned, as the runtime reads it.
    """

    axes = derivation.get("axes") if isinstance(derivation, Mapping) else None
    if not isinstance(axes, Mapping) or not axes:
        return request
    data = request.model_dump(mode="json")
    polar = (data.get("options") or {}).get("polar_config")
    if not isinstance(polar, dict):
        return request
    start, end, count = (float(polar["angle_range"][0]), float(polar["angle_range"][1]),
                         int(polar["angle_range"][2]))
    step = (end - start) / (count - 1) if count > 1 else 5.0
    requested = set(polar.get("enabled_axes") or [])
    enabled = set(requested)
    low, high = start, end
    for axis, spec in axes.items():
        spec = spec if isinstance(spec, Mapping) else {}
        minimum = float(spec.get("minimum_deg", -180.0))
        maximum = float(spec.get("maximum_deg", 180.0))
        if minimum <= -180.0 and maximum >= 180.0:
            enabled.add(str(axis))
        low, high = min(low, minimum), max(high, maximum)
    if (low, high) != (start, end):
        # Round positive sample counts half up, preserving the existing solve contract.
        samples = math.floor((high - low) / step + 0.5) + 1
        polar["angle_range"] = [low, high, max(count, samples)]
    polar["enabled_axes"] = [axis for axis in _POLAR_AXIS_ORDER if axis in enabled]
    if "diagonal" in enabled and "diagonal" not in requested:
        # A diagonal the user never enabled carries no inclination intent; the
        # runtime accepts only the supported default for one forced on.
        polar["inclination"] = _DEFAULT_DIAGONAL_INCLINATION_DEG
    return SolveRequest.model_validate(data)
