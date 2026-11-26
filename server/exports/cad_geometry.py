"""The one CAD realisation of a design.

File -> STEP (the solid) and Send to CAD (the WGLink point grid Fusion lofts)
used to sample the same design differently: the STEP through the CAD planner
with export corner arcs, the bundle at the design's own solver resolutions with
neither. Both now take their geometry from :func:`resolve_cad_geometry`, so the
part a user exports and the body Fusion builds are the same sampled surface.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import re
import threading
from typing import TYPE_CHECKING, Any

from server.design.schema import DesignConfig
from server.exports.sizing import (
    EXPORT_CORNER_SEGMENTS,
    STEP_SURFACE_TOLERANCE_MM,
    CadPlan,
    _with_resolution,
    plan_cad_resolution,
)
from server.mesh.builder import _solver_mesher_config
from server.preview.construction import retain_mesher_construction

if TYPE_CHECKING:
    from hornlab_mesher.cad import CadInfo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CadGeometry:
    """The resolved geometry every CAD output writes, and the plan that sized it."""

    #: The mesher's ``ResolvedGeometry``: full model, throat at the origin, CAD-sampled.
    resolved: Any
    plan: CadPlan
    #: What determines the geometry: the planned config and the mesher. Two
    #: designs with one key build one solid (see ``solid_known_to_build``).
    build_key: str = ""
    #: The Onshape send's placed frame (``Mesh.VerticalOffset`` applied, the
    #: throat at its own station), which its existing documents were built in;
    #: every other CAD output has the throat centre at the origin.
    placed: bool = False

    def sampling_record(self) -> dict[str, Any]:
        """The plan as an artifact records it (the WGLink manifest's ``cad_sampling``)."""

        return {
            "resolution_mm": self.plan.resolution_mm,
            "deviation_mm": self.plan.deviation_mm,
            "tolerance_mm": STEP_SURFACE_TOLERANCE_MM,
            "warning": self.plan.warning,
            # Why the planner fell back (``CAD_FALLBACK_REASONS``), else None.
            "fallback_reason": self.plan.fallback_reason,
        }


_STRICT_SCALAR = re.compile(r"^(\S+) must be a finite scalar expression$")


def _cad_base_config(design: DesignConfig, *, placed: bool = False) -> dict[str, Any]:
    """``_solver_mesher_config``, its refusals told as a CAD fix.

    Its plain ``ValueError`` refusals speak of the solver; a CAD export or send
    is refused with the control to change and how.
    """

    from hornlab_mesher.errors import UserRefusal, numeric_refusal

    try:
        return _solver_mesher_config(design, keep_placement=placed)
    except UserRefusal:
        raise
    except ValueError as exc:
        text = str(exc)
        if text.startswith("source contours"):
            raise UserRefusal(
                "Source contours",
                "Designs with Source contours cannot be exported to CAD yet.",
                "Clear Source contours (the CAD part does not use them), then try again.",
                diagnostic=text,
            ) from exc
        match = _STRICT_SCALAR.match(text)
        if match:
            raise numeric_refusal(match[1], text) from exc
        raise


def cad_mesher_config(design: DesignConfig, *, placed: bool = False) -> dict[str, Any]:
    """The mesher config every CAD output resolves, before its sampling is chosen.

    The solver's own config, so the part is the geometry that was solved rather
    than a second derivation of it, with three CAD boundary rules:

    * All four quadrants. A solve may run on a quarter model, but a part cannot
      be a quarter of itself. The planner measures this same full config, so
      the geometry it accepted is the geometry written (the mesher's
      ``write_step_from_config`` reopens the domain the same way).
    * No placement. The throat centre is the CAD origin, as it is the solve's:
      ``mesh.vertical_offset`` stays with the design (and its ATH config) but
      moves no CAD output. A CAD program places the part itself -- Fusion
      records that as the link's occurrence transform, which the return leg
      inverts -- so the Fusion document origin is the throat of an unmoved
      link, and File -> STEP writes the same part. ``placed`` is the one
      exception: the Onshape send keeps the offset (and the bundle's 1.2
      placed frame) so documents already built from it do not move.
    * Export corner arcs, set before sampling is chosen, so the planner
      measures the arcs that will be written rather than the design's own
      count.
    """

    config = retain_mesher_construction(_cad_base_config(design, placed=placed))
    config["mesh"] = {
        **(config.get("mesh") or {}),
        "cornerSegments": EXPORT_CORNER_SEGMENTS,
        "quadrants": "1234",
    }
    config.pop("quadrants", None)
    return config


def resolve_cad_geometry(design: DesignConfig, *, placed: bool = False) -> CadGeometry:
    """Plan the CAD sampling and resolve the geometry every CAD output writes.

    The solver's mm resolutions used to size the CAD point grid as well, so
    refining an acoustic mesh made every STEP roughly 20x slower and 10x larger
    while the export's own controls moved the grid by one row. The part is a
    CAD artifact: it is sampled to a CAD tolerance instead, and the result is
    the same whatever the solve was set to. The planner's accepted probe is
    reused rather than resolved again; only a plan whose sampling was never
    probed (the fixed fallback) resolves here.
    """

    from hornlab_mesher.config_builder import resolve_geometry

    config = cad_mesher_config(design, placed=placed)
    plan = plan_cad_resolution(config, tolerance_mm=STEP_SURFACE_TOLERANCE_MM)
    written = _with_resolution(config, plan.resolution_mm)
    resolved = plan.resolved
    if resolved is None:
        resolved = resolve_geometry(written)
    logger.info(
        "CAD geometry sampled at %.3g mm (measured deviation %s mm, tolerance %g mm)",
        plan.resolution_mm,
        "unmeasured" if plan.deviation_mm is None else f"{plan.deviation_mm:.5f}",
        STEP_SURFACE_TOLERANCE_MM,
    )
    if plan.warning:
        logger.warning(
            "CAD geometry sizing (fallback reason %s): %s", plan.fallback_reason, plan.warning
        )
    return CadGeometry(
        resolved=resolved, plan=plan, build_key=_build_key(written), placed=placed
    )


def _build_key(config: dict[str, Any]) -> str:
    from server.contracts.canonical import canonical_ascii_strict_json, normalize_json_value
    from server.preview.geometry_identity import mesher_version

    text = canonical_ascii_strict_json(
        {"config": normalize_json_value(config), "mesher": mesher_version(), "open_throat": True}
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Solids this process has built: File -> STEP wrote them, or a Send checked
# them. Both run in the mesher child, which outlives single requests, so a
# Send after a File -> STEP (or a repeat Send) of an unchanged design skips the
# OCC check. A restarted child forgets and checks again. Bounded; a miss only
# costs the check.
_BUILT: OrderedDict[str, None] = OrderedDict()
_BUILT_LIMIT = 256
_BUILT_LOCK = threading.Lock()


def solid_known_to_build(cad: CadGeometry) -> bool:
    with _BUILT_LOCK:
        if cad.build_key and cad.build_key in _BUILT:
            _BUILT.move_to_end(cad.build_key)
            return True
    return False


def remember_solid_built(cad: CadGeometry) -> None:
    if not cad.build_key:
        return
    with _BUILT_LOCK:
        _BUILT[cad.build_key] = None
        _BUILT.move_to_end(cad.build_key)
        while len(_BUILT) > _BUILT_LIMIT:
            _BUILT.popitem(last=False)


def forget_built_solids() -> None:
    """Tests: start from a child that has built nothing."""

    with _BUILT_LOCK:
        _BUILT.clear()


def write_cad_step(cad: CadGeometry, path: Path) -> CadInfo:
    """Write ``cad``'s geometry as the solid STEP, with the mesher's own writer.

    File -> STEP and the Onshape import upload both write through here, so
    there is one solid STEP of a design and it is built from the one CAD
    geometry. The caller checks the text (``core._assert_step``).
    """

    try:
        from hornlab_mesher.cad import write_step
    except ImportError as exc:
        raise RuntimeError(
            "the installed hornlab-waveguide-mesher has no CAD export; "
            "install the pinned server requirements"
        ) from exc
    from server.mesh.gmsh_worker import _preserve_native_windows_path

    # Public callers run this in the mesher child. Keep the synchronous helper
    # safe as well: contract tests and maintenance tools call it directly,
    # allowing the pinned mesher to own a native session.
    with _preserve_native_windows_path():
        # The WGLink bundle's frame (throat centre at the origin), so this
        # solid and the body Fusion lofts from a Send are one part; the
        # Onshape send's placed geometry keeps its placement.
        _, info = write_step(cad.resolved.geometry, path, link_frame=not cad.placed)
    return info


__all__ = [
    "CadGeometry",
    "cad_mesher_config",
    "forget_built_solids",
    "remember_solid_built",
    "resolve_cad_geometry",
    "solid_known_to_build",
    "write_cad_step",
]
