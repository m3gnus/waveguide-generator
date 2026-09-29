"""Coupled infinite-baffle validation for full-3D engines.

A free-field/image shortcut is incorrect for a flush-mounted waveguide, so the
coupled formulation is the only one offered. Metal, and BEMPP where its probe
reports coupled support, use the mesher's aperture physical tag and native
Rayleigh coupling. BEAT cannot solve it and refuses.
"""

from __future__ import annotations

from typing import Any, Mapping

from .context import SolverContext


def aperture_tag_from_metadata(metadata: Any) -> int | None:
    if not isinstance(metadata, Mapping):
        return None
    for key in ("apertureTag", "aperture_tag"):
        raw = metadata.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid infinite-baffle aperture tag {raw!r}") from exc
        if not number.is_integer() or number <= 0:
            raise ValueError(f"Invalid infinite-baffle aperture tag {raw!r}; expected a positive integer")
        return int(number)
    return None


def require_coupled_aperture_tag(
    context: SolverContext,
    metadata: Any,
    *,
    backend: str,
) -> int | None:
    """Return the canonical aperture tag for any full-3D coupled backend."""
    if context.sim_type != 1:
        return None
    tag = aperture_tag_from_metadata(metadata)
    if tag is None:
        raise RuntimeError(
            f"Full-3D infinite-baffle {backend} solve requires the coupled "
            "aperture tag, but hornlab-waveguide-mesher did not report "
            "apertureTag/aperture_tag."
        )
    return tag


def reject_beat_infinite_baffle(context: SolverContext) -> None:
    if context.sim_type == 1:
        raise ValueError(
            "The BEAT backend cannot solve coupled infinite-baffle requests. "
            "Use Metal, or BEMPP with coupled infinite-baffle support."
        )


__all__ = [
    "aperture_tag_from_metadata",
    "reject_beat_infinite_baffle",
    "require_coupled_aperture_tag",
]
