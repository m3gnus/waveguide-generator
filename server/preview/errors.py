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
    span = re.search(r"mouth z spans ([0-9.eE+-]+) mm", detail)
    protrusion = re.search(r"to z=([0-9.eE+-]+) mm", detail)
    context = (f"The rim varies by {span[1]} mm in depth. " if span else
               f"The horn extends {protrusion[1]} mm in front of the baffle. " if protrusion else "")
    return (
        "This horn cannot be mounted in an infinite baffle: its mouth is not a "
        "flat rim lying in the baffle plane, with the whole horn behind it. "
        + context + "Use a planar, front-most mouth, or choose Bare shell "
        "(free-standing) in Mounting."
    )



def mesher_error_message(
    config: Mapping[str, Any] | Callable[[], Mapping[str, Any]], detail: str
) -> str:
    """Translate once, and keep the original technical diagnostic in the log."""
    corner = re.search(r"FREEFORM corner z=([^:]+) mm: unsafe offset mitre", detail)
    if corner is not None:
        logger.warning("Mesher corner diagnostic: %s", detail)
        return PublicMessage(
            f"The outer wall cannot fit around the sharp profile corner at {corner[1]} mm. "
            "Soften this profile corner, or choose Bare shell in Mounting.")
    # Compatibility for public diagnostics emitted by older mesher workers.
    if detail.startswith(("Mouth coverage angle (a) reaches ", "Throat angle (a0) reaches ",
                          "Mouth coverage angle (a):", "Throat angle (a0):",
                          "The enclosure leaves ")):
        return PublicMessage(detail)
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
    return PublicMessage(message)


class PublicMessage(str):
    """Words produced by this owner, retained by legacy exception wrappers."""


PROGRAM_FAILURE = "WG could not finish this request and has recorded the problem."


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


# Python/NumPy diagnostics have no user-authored refusal or remedy. Keep this
# one list at the common owner, including in-process and worker solve callers.
_LIBRARY_DIAGNOSTICS = (
    "could not convert string to float", "math domain error",
    "not enough values to unpack", "too many values to unpack",
    "zero-size array to reduction", "iterable argument is empty", "arg is an empty sequence",
    "operands could not be broadcast", "invalid literal for int",
    "cannot reshape array", "setting an array element with a sequence",
)


def public_error_message(exc: BaseException, *, domain_values: bool = True) -> str | None:
    """Preserve the existing domain owners; arbitrary exceptions stay private."""
    if isinstance(exc, (TypeError, KeyError, AttributeError, AssertionError, IndexError, OSError)):
        return None
    marker = getattr(sys.modules.get("hornlab_mesher.errors"), "InternalInvariantError", None)
    cause, seen = exc, set()
    while cause is not None and id(cause) not in seen:
        if isinstance(marker, type) and isinstance(cause, marker):
            return None
        seen.add(id(cause))
        cause = cause.__cause__
    # The outer domain owner may have authored more specific import guidance.
    # Invariant causes were rejected above before any public marker is trusted.
    forwarded = getattr(exc, "public_message", None)
    if isinstance(forwarded, str) and forwarded:
        return forwarded
    if exc.args and isinstance(exc.args[0], PublicMessage):
        return str(exc.args[0])
    refusal = _user_refusal(exc)
    if refusal is not None:
        return f"{refusal.user_message} {refusal.next_step}".strip()
    public_types = {
        "server.mesh.imported": ("ImportedMeshError",),
        "server.mesh.builder": ("AssemblyRefusal",),
        "server.solver.ground_plane": ("GroundPlaneClearance",),
        "server.solver.driver_lem": ("DriverMassLimitError",),
        "server.solver.imported": ("SourceAxisError",),
        "server.solver.beat_imported": ("ImportedBeatRefusal",),
        "server.solver.bempp": ("BemppUnavailable",),
        "server.solver.metal": ("MetalUnavailable",),
        "server.solver.beat": ("BeatUnavailable",),
        "server.jobs.runtime.engine_policy": ("EngineUnavailableError",),
        "server.mesh.child": ("MesherCrashError", "MesherShuttingDownError"),
    }
    for module_name, names in public_types.items():
        module = sys.modules.get(module_name)
        kinds = tuple(kind for name in names if isinstance(kind := getattr(module, name, None), type))
        if isinstance(exc, kinds):
            message, step = getattr(exc, "user_message", None), getattr(exc, "next_step", None)
            return f"{message} {step}".strip() if isinstance(message, str) and isinstance(step, str) else str(exc)
    module = sys.modules.get("server.cadlink.intake.ingest")
    kind = getattr(module, "IngestRefusal", None)
    if isinstance(kind, type) and isinstance(exc, kind) and exc.user_worded:
        return str(exc)
    # ValidationError retains the original ValueError in each row's context.
    validation_types = tuple(kind for module_name, name in (
        ("pydantic", "ValidationError"), ("fastapi.exceptions", "RequestValidationError"))
        if isinstance(kind := getattr(sys.modules.get(module_name), name, None), type))
    if isinstance(exc, validation_types):
        words = [public_error_message(cause, domain_values=domain_values) for row in exc.errors()
                 if isinstance(cause := row.get("ctx", {}).get("error"), BaseException)]
        if words and all(words):
            return " ".join(words)
    # Historical geometry translations are explicit, diagnosed contracts.
    translated = mesher_error_message({"mode": "infinite-baffle"}, str(exc))
    if isinstance(translated, PublicMessage):
        return str(translated)
    if any(shape in str(exc).lower() for shape in _LIBRARY_DIAGNOSTICS):
        return None
    if str(exc).startswith("the acoustic control grid needed for the requested mm resolution"):
        return "The requested mesh resolution needs too many surface samples. Increase Throat mesh resolution and Mouth mesh resolution to use a coarser mesh."
    # ConfigError is mesher design validation, including during a job mesh stage.
    # Plain ValueError needs a WG domain boundary; solver workers opt out.
    config_error = getattr(sys.modules.get("hornlab_mesher.errors"), "ConfigError", None)
    if (domain_values and type(exc) is ValueError) or (isinstance(config_error, type) and type(exc) is config_error):
        cause = exc.__cause__
        if cause is None or isinstance(cause, ValueError):
            return str(exc)
    return None


def user_error_message(exc: BaseException, *, fallback: str = PROGRAM_FAILURE, domain_values: bool = True) -> str:
    """Show the domain owner's remedy, with original diagnostics in the log."""
    words = public_error_message(exc, domain_values=domain_values)
    if words is not None:
        logger.warning("Public refusal (%s): %s", type(exc).__name__, getattr(exc, "diagnostic", str(exc)))
        return words
    logger.error("Program failure: %s", exc, exc_info=(type(exc), exc, exc.__traceback__))
    return fallback


def is_user_refusal(exc: BaseException) -> bool:
    """Whether a domain owner refused the request, without importing the mesher."""
    return _user_refusal(exc) is not None


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
    if (words := public_error_message(exc)) is not None:
        wrapped.public_message = words
    return wrapped
