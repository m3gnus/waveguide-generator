"""Coupled infinite-baffle validation shared by Metal full-3D and CircSym.

V1 rejects the tempting but incorrect free-field/image shortcut: full 3-D uses
the mesher's aperture physical tag and native Rayleigh coupling, while CircSym
uses its meridian aperture tag.  See v1 ``metal_solver.py:310-359,396-405`` and
``metal_solver.py:477-500,550-558``.
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


def require_full_3d_aperture_tag(context: SolverContext, metadata: Any) -> int | None:
    if context.sim_type != 1:
        return None
    tag = aperture_tag_from_metadata(metadata)
    if tag is None:
        raise RuntimeError(
            "Full-3D infinite-baffle Metal solve requires the coupled aperture tag, "
            "but hornlab-waveguide-mesher did not report apertureTag/aperture_tag."
        )
    return tag


def reject_bempp_infinite_baffle(context: SolverContext) -> None:
    if context.sim_type == 1:
        raise ValueError(
            "The BEMPP fallback cannot solve coupled infinite-baffle requests. "
            "Use Metal full-3D aperture coupling or Metal CircSym."
        )


__all__ = [
    "aperture_tag_from_metadata",
    "reject_bempp_infinite_baffle",
    "require_full_3d_aperture_tag",
]
