"""ATH symmetry vocabulary and WG imported-CAD tag conventions.

This leaf imports only the standard library. Mesher-owned physical tags are
read from ``hornlab_mesher.tags`` by their consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


FULL_DOMAIN_QUADRANTS = 1234
_LEADING_INT_RE = re.compile(r"^[+-]?\d+")
_CANONICAL = (1234, 12, 14)

PLANE_BY_QUADRANTS = {1: "yz+xz", 12: "xz", 14: "yz", 1234: None}
CUT_PLANE_BY_NATIVE_PLANE = {"yz": "x0", "xz": "y0"}
CUT_PLANE_AXIS = {"x0": 0, "y0": 1, "z0": 2}
# Native solvers support x0 and y0 cuts only; z0 remains an OCC axis.
SUPPORTED_CUT_PLANES = tuple(CUT_PLANE_BY_NATIVE_PLANE.values())
SYMMETRY_PLANE_AXIS = {plane: CUT_PLANE_AXIS[plane] for plane in SUPPORTED_CUT_PLANES}
CUT_PLANES_BY_QUADRANTS = {
    quadrants: tuple(CUT_PLANE_BY_NATIVE_PLANE[part] for part in plane.split("+"))
    if plane is not None
    else ()
    for quadrants, plane in PLANE_BY_QUADRANTS.items()
}
QUADRANTS_BY_CUT_PLANES = {
    frozenset(planes): quadrants for quadrants, planes in CUT_PLANES_BY_QUADRANTS.items()
}
NATIVE_PLANE_BY_CUT_PLANES = {
    planes: PLANE_BY_QUADRANTS[quadrants] for planes, quadrants in QUADRANTS_BY_CUT_PLANES.items()
}
MODE_BY_QUADRANTS = {
    quadrants: "full" if plane is None else "quarter" if "+" in plane else "half_" + plane
    for quadrants, plane in PLANE_BY_QUADRANTS.items()
}
SYMMETRY_MODE_QUADRANTS = {
    "auto": FULL_DOMAIN_QUADRANTS,
    **{
        mode: next(q for q, name in MODE_BY_QUADRANTS.items() if name == mode)
        for mode in ("full", "half_xz", "half_yz", "quarter")
    },
}
SYMMETRY_DOMAINS_BY_QUADRANTS = {
    quadrants: ("half", mode.replace("_", "-")) if mode.startswith("half_") else (mode,)
    for quadrants, mode in MODE_BY_QUADRANTS.items()
}

# WG allocates a separate source-tag namespace for imported CAD; these are
# deliberately not the parametric source tags owned by hornlab_mesher.tags.
FIRST_SOURCE_TAG = 101


def quadrants_leading_int(value: Any) -> int:
    """Parse like ATH/C ``atoi`` (v1 ``quadrants.py:48-65``)."""

    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    match = _LEADING_INT_RE.match(str(value).strip())
    return int(match.group(0)) if match else 0


def normalise_quadrants(value: Any) -> int:
    """Return 1234, 12, 14, or ATH's quarter-domain fallback 1."""

    leading = quadrants_leading_int(value)
    return leading if leading in _CANONICAL else 1


def native_symmetry_plane_for_quadrants(value: Any) -> str | None:
    """Map reduced mesh coverage to Metal/BEMPP native image planes."""

    return PLANE_BY_QUADRANTS[normalise_quadrants(value)]


def symmetry_plane_axes_for_quadrants(value: Any) -> tuple[int, ...]:
    """Coordinate normals of the reduced domain, in canonical x/y order."""

    cuts = CUT_PLANES_BY_QUADRANTS[normalise_quadrants(value)]
    return tuple(SYMMETRY_PLANE_AXIS[plane] for plane in cuts)


def quadrants_for_symmetry_planes(*, xz: bool, yz: bool) -> int:
    """Resolve a pair of available native mirrors to its ATH domain."""

    cuts = frozenset(
        CUT_PLANE_BY_NATIVE_PLANE[plane] for plane, present in (("xz", xz), ("yz", yz)) if present
    )
    return QUADRANTS_BY_CUT_PLANES[cuts]


class ImportedSymmetryUnsupportedError(ValueError):
    """The ingestion artifact uses cut planes the native solver cannot mirror."""

    def __init__(self, cut_planes: Iterable[str]) -> None:
        self.cut_planes = tuple(str(plane) for plane in cut_planes)
        super().__init__(
            "unsupported imported symmetry cut-plane set: "
            + repr(list(self.cut_planes))
            + "; Phase 2 supports only no cuts, x0, y0, or x0+y0"
        )


@dataclass(frozen=True, slots=True)
class ImportedSymmetry:
    mode: str
    quadrants: int
    native_plane: str | None
    cut_planes: tuple[str, ...]


def imported_symmetry_from_cut_planes(cut_planes: Iterable[Any]) -> ImportedSymmetry:
    """Resolve actual CAD cuts, retaining caller order and rejecting duplicates."""

    ordered = tuple(str(plane) for plane in cut_planes)
    cuts = frozenset(ordered)
    if len(cuts) != len(ordered) or cuts not in QUADRANTS_BY_CUT_PLANES:
        raise ImportedSymmetryUnsupportedError(ordered)
    quadrants = QUADRANTS_BY_CUT_PLANES[cuts]
    return ImportedSymmetry(
        MODE_BY_QUADRANTS[quadrants], quadrants, NATIVE_PLANE_BY_CUT_PLANES[cuts], ordered
    )
