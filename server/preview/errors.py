"""One translation boundary for mesher diagnostics shown by WG callers."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Mapping

from server.design.schema import Expr

logger = logging.getLogger(__name__)
_WALL_FOLD = re.compile(
    r"^.+ generated outer offset grid (?:has a normal flip |self-intersects )"
)


def wall_fold_message(config: Mapping[str, Any], detail: str) -> str | None:
    """Recognise offset folds for every family, retaining unrelated diagnostics."""
    if not _WALL_FOLD.match(detail):
        return None
    thickness = config.get("mesh", {}).get("wallThickness")
    resolved = Expr.model_validate(thickness).constant_value() if thickness is not None else None
    wall = f"{resolved:g} mm wall" if resolved is not None else "wall"
    return (
        f"The {wall} is thicker than the tightest bend of the horn allows, "
        "so the outer wall would fold over itself. Reduce Wall thickness, "
        "soften that bend (for example the throat or mouth angle), or use Bare shell / Infinite baffle."
    )


#: Substring shared by the mesher's refusals of a mouth that cannot sit flush
#: in an infinite baffle (non-planar ring; ring not the front-most station).
_BAFFLE_GEOMETRY_MARKER = "infinite-baffle coupled aperture mesh requires"


def _infinite_baffle_geometry_refusal(
    config: Mapping[str, Any], detail: str
) -> str | None:
    """Turn the mesher's flush-mouth refusal into an actionable message.

    Whether a mouth is planar is known only once the mesher has evaluated the
    point grid, so it cannot be judged before a build without duplicating the
    geometry system. What can be done is to make the refusal readable: say what
    was wrong with the design and what to change, instead of surfacing the
    mesher's internal wording alone.
    """

    if str(config.get("mode", "")).strip().lower() != "infinite-baffle":
        return None
    if _BAFFLE_GEOMETRY_MARKER not in detail:
        return None
    return (
        "This horn cannot be mounted in an infinite baffle: its mouth is not a "
        "flat rim lying in the baffle plane, with the whole horn behind it. "
        "Use a planar, front-most mouth, or set the simulation type to "
        f"free-standing. Mesher detail: {detail}"
    )



def mesher_error_message(
    config: Mapping[str, Any] | Callable[[], Mapping[str, Any]], detail: str
) -> str:
    """Translate once, and keep the original technical diagnostic in the log."""
    if not _WALL_FOLD.match(detail) and _BAFFLE_GEOMETRY_MARKER not in detail:
        return detail
    if callable(config):
        config = config()
    message = wall_fold_message(config, detail) or _infinite_baffle_geometry_refusal(config, detail)
    if message is None:
        return detail
    logger.warning("Mesher geometry diagnostic: %s", detail)
    return message
