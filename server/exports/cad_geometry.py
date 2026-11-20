"""The one CAD realisation of a design.

File -> STEP (the solid) and Send to CAD (the WGLink point grid Fusion lofts)
used to sample the same design differently: the STEP through the CAD planner
with export corner arcs, the bundle at the design's own solver resolutions with
neither. Both now take their geometry from :func:`resolve_cad_geometry`, so the
part a user exports and the body Fusion builds are the same sampled surface.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
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

    #: The mesher's ``ResolvedGeometry``: full model, placed, CAD-sampled.
    resolved: Any
    plan: CadPlan

    def sampling_record(self) -> dict[str, Any]:
        """The plan as an artifact records it (the WGLink manifest's ``cad_sampling``)."""

        return {
            "resolution_mm": self.plan.resolution_mm,
            "deviation_mm": self.plan.deviation_mm,
            "tolerance_mm": STEP_SURFACE_TOLERANCE_MM,
            "warning": self.plan.warning,
        }


def cad_mesher_config(design: DesignConfig) -> dict[str, Any]:
    """The mesher config every CAD output resolves, before its sampling is chosen.

    The solver's own config, so the part is the geometry that was solved rather
    than a second derivation of it, with three CAD boundary rules:

    * All four quadrants. A solve may run on a quarter model, but a part cannot
      be a quarter of itself. The planner measures this same full config, so
      the geometry it accepted is the geometry written (the mesher's
      ``write_step_from_config`` reopens the domain the same way).
    * The placement. ``mesh.vertical_offset`` is applied only at the CAD
      boundary (``_solver_mesher_config``), and every CAD output carries it,
      so an ATH import declaring Mesh.Quadrants = 1 or 12 still exports a
      placed solid.
    * Export corner arcs, set before sampling is chosen, so the planner
      measures the arcs that will be written rather than the design's own
      count.
    """

    config = retain_mesher_construction(_solver_mesher_config(design, keep_placement=True))
    config["mesh"] = {
        **(config.get("mesh") or {}),
        "cornerSegments": EXPORT_CORNER_SEGMENTS,
        "quadrants": "1234",
    }
    config.pop("quadrants", None)
    return config


def resolve_cad_geometry(design: DesignConfig) -> CadGeometry:
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

    config = cad_mesher_config(design)
    plan = plan_cad_resolution(config, tolerance_mm=STEP_SURFACE_TOLERANCE_MM)
    resolved = plan.resolved
    if resolved is None:
        resolved = resolve_geometry(_with_resolution(config, plan.resolution_mm))
    logger.info(
        "CAD geometry sampled at %.3g mm (measured deviation %s mm, tolerance %g mm)",
        plan.resolution_mm,
        "unmeasured" if plan.deviation_mm is None else f"{plan.deviation_mm:.5f}",
        STEP_SURFACE_TOLERANCE_MM,
    )
    if plan.warning:
        logger.warning("CAD geometry sizing: %s", plan.warning)
    return CadGeometry(resolved=resolved, plan=plan)


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
        _, info = write_step(cad.resolved.geometry, path)
    return info


__all__ = ["CadGeometry", "cad_mesher_config", "resolve_cad_geometry", "write_cad_step"]
