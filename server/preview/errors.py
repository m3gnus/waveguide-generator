"""One translation boundary for mesher diagnostics shown by WG callers."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any, Callable, Mapping

from server.design.schema import Expr

logger = logging.getLogger(__name__)
_WALL_FOLD = re.compile(
    r"^.+ generated outer offset grid (?:has a normal flip |self-intersects )"
)
_OFFSET_DEGENERACY = re.compile(
    r"^.+ generated outer offset grid has (?:a degenerate (?P<side>inner|outer) normal "
    r"|degenerate inner and outer normals )"
)


def offset_degeneracy_message(detail: str) -> str | None:
    """Name collapsed surfaces without inferring a thickness-caused fold."""
    match = _OFFSET_DEGENERACY.match(detail)
    if match is None:
        return None
    side = match.group("side")
    surface = (f"The {side} surface collapses in part." if side is not None else
               "The inner and outer surfaces collapse together in part.")
    return (
        f"{surface} Adjust the local profile so nearby sections remain distinct, "
        "then try again."
    )


def wall_fold_message(config: Mapping[str, Any], detail: str) -> str | None:
    """Recognise invalid offset grids while preserving each diagnosed cause."""
    collapsed = offset_degeneracy_message(detail)
    if collapsed is not None:
        return collapsed
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
    message = offset_degeneracy_message(detail)
    if message is None:
        if not _WALL_FOLD.match(detail) and _BAFFLE_GEOMETRY_MARKER not in detail:
            return detail
        if callable(config):
            config = config()
        message = wall_fold_message(config, detail) or _infinite_baffle_geometry_refusal(config, detail)
    if message is None:
        return detail
    logger.warning("Mesher geometry diagnostic: %s", detail)
    return message


PROGRAM_FAILURE = "WG could not finish this request and has recorded the problem. Try again."


def _user_refusal(exc: BaseException):
    # A typed object can exist only after its defining module was loaded.
    # Reporting an unrelated program fault must not import the native stack.
    module = sys.modules.get("hornlab_mesher.errors")
    refusal_type = getattr(module, "UserRefusal", None)
    if not isinstance(refusal_type, type):
        return None
    if isinstance(exc, refusal_type):
        return exc
    forwarded = getattr(exc, "user_refusal", None)
    if isinstance(forwarded, refusal_type):
        return forwarded
    # The text parser keeps its historical public wrapper. Only the explicit
    # native refusal underneath it is user-actionable.
    text_module = sys.modules.get("server.design.textcfg")
    text_error = getattr(text_module, "TextConfigError", None)
    if isinstance(text_error, type) and isinstance(exc, text_error) and isinstance(exc.__cause__, refusal_type):
        return exc.__cause__
    return None


def user_error_message(exc: BaseException, *, fallback: str = PROGRAM_FAILURE) -> str:
    """One public boundary: only typed user remedies leave the server.

    Keep status and wire codes at the caller. Never infer user responsibility
    from the spelling or apparent readability of an arbitrary exception.
    """
    refusal = _user_refusal(exc)
    if refusal is not None:
        logger.warning("User refusal (%s): %s", refusal.field, getattr(refusal, "diagnostic", str(exc)))
        return f"{refusal.user_message} {refusal.next_step}".strip()
    logger.error("Program failure: %s", exc, exc_info=(type(exc), exc, exc.__traceback__))
    return fallback


def user_error_detail(exc: BaseException) -> str | dict[str, str]:
    """Typed HTTP detail for clients that can mark the named control."""
    refusal = _user_refusal(exc)
    if refusal is None:
        return user_error_message(exc)
    user_error_message(exc)  # Keep the original diagnostic in the log.
    return {"field": refusal.field, "user_message": refusal.user_message, "next_step": refusal.next_step}



def user_error_as(exc: BaseException, error_type: type[ValueError]) -> ValueError:
    """Keep a typed refusal through an established server exception wrapper."""
    wrapped = error_type(user_error_message(exc))
    refusal = _user_refusal(exc)
    if refusal is not None:
        wrapped.user_refusal = refusal
    return wrapped
