"""Rigid half-space ground plane: naming, placement, and symmetry consequences.

A ground plane is an infinite, perfectly rigid boundary that reflects with
coefficient +1 and no sign flip on the pressure.  It is *not* an infinite
baffle.  The baffle is a wall the mouth is let into, coplanar with the mouth
and removing every cabinet edge; the ground plane is a floor (or wall) the
whole body stands above, and the body keeps its own edges.  The two give
different answers, and picking the wrong one gives a plausible wrong answer
rather than an error -- which is why they are separate mountings rather than
two spellings of one.

Naming
------

Every plane here is named by **the single axis it bounds**, following Boundary
Lab's ``src/blab/deploy_solve.py``, which writes the boundary as
``{"type": "rigid_half_space", "axis": "y", "offset_m": 0.0,
"reflection_coefficient": 1.0}``.  The fluid occupies ``axis >= 0``.

This is deliberate, and it is the whole reason this module exists.  The token
``xy`` currently means three different things across this stack:

* WG's own symmetry vocabulary: legacy bi-symmetry (``Mesh.Quadrants``),
  rejected by ``reject_unsupported_native_symmetry`` as unimplemented.
* hornlab-beat-bem: mirrors across x *and* y -- two planes.
* hornlab-bempp-bem's ``SolveConfig.ground_plane``: the single z=0 plane -- the
  plane *spanned* by x and y.

An axis-pair token therefore cannot be shared safely, so WG never uses one for
a ground plane.  ``NATIVE_GROUND_PLANE`` below is the only place in wg2 that
spells one at all, and it exists solely to translate at the package boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: Axis names WG accepts, in coordinate order.  ``y`` is WG's vertical, so a
#: horn standing above a floor is ``axis="y"``; ``z`` is the horn axis, so a
#: ``z`` plane is a rigid wall behind the throat and ``x`` is a side wall.
GROUND_PLANE_AXES: tuple[str, ...] = ("x", "y", "z")

#: Index into a vertex row for each axis.
_AXIS_COMPONENT = {"x": 0, "y": 1, "z": 2}

#: WG axis -> the axis-pair token ``hornlab_bempp_bem.SolveConfig.ground_plane``
#: wants.  The pair names the plane the axis is normal to: bounding x is the
#: plane spanned by y and z.  Read the module docstring before reusing any of
#: these strings anywhere else -- they collide with two other vocabularies.
NATIVE_GROUND_PLANE = {"x": "yz", "y": "xz", "z": "xy"}


@dataclass(frozen=True, slots=True)
class GroundPlane:
    """A resolved ground plane: which axis it bounds and how high the model sits.

    ``height_m`` is the height of the model's *own origin* above the plane,
    matching Boundary Lab's ``DeploySourcePlacement.position_height_m`` rather
    than a clearance under the lowest point.  It is the quantity a user knows
    ("the waveguide is 1.1 m off the floor"), and it keeps the plane itself at
    the world origin, which is what the image assembler requires.
    """

    axis: str
    height_m: float

    def __post_init__(self) -> None:
        if self.axis not in GROUND_PLANE_AXES:
            raise ValueError(
                "ground plane axis must be one of "
                + ", ".join(repr(name) for name in GROUND_PLANE_AXES)
            )

    @property
    def component(self) -> int:
        return _AXIS_COMPONENT[self.axis]

    @property
    def native_plane(self) -> str:
        """The package's axis-pair spelling.  See ``NATIVE_GROUND_PLANE``."""

        return NATIVE_GROUND_PLANE[self.axis]

    @property
    def blocked_symmetry_plane(self) -> str:
        """The symmetry plane this ground plane makes unavailable.

        Same plane, different role, so the same spelling: a reduced mesh is cut
        *on* its mirror plane and must touch it, while a model standing above a
        ground plane must not reach it.  A model cannot do both at once, and
        the package refuses the pair outright.  Lifting the model off the plane
        is what destroys the cut, so the restriction is unconditional rather
        than a function of the height.
        """

        return NATIVE_GROUND_PLANE[self.axis]


class GroundPlaneClearance(ValueError):
    """The model would cross its ground plane at the requested height."""


def _node_section(lines: list[str]) -> tuple[int, int]:
    """Locate the Gmsh 2.2 ``$Nodes`` payload as a ``[start, end)`` line range."""

    try:
        header = lines.index("$Nodes")
        count = int(lines[header + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(
            "a ground-plane solve requires an ASCII Gmsh 2.2 solver artifact"
        ) from exc
    first = header + 2
    return first, first + count


def model_extent(msh_text: str, plane: GroundPlane) -> float:
    """The lowest coordinate the model reaches along the ground plane's axis.

    Read from every node rather than only the used ones: an unused node cannot
    make the placement wrong, and refusing to reason about it keeps this
    agreeing with the package's own check, which is the one that matters.
    """

    lines = msh_text.splitlines()
    first, last = _node_section(lines)
    component = plane.component
    lowest: float | None = None
    for row in lines[first:last]:
        fields = row.split()
        if len(fields) < 4:
            continue
        value = float(fields[1 + component])
        if lowest is None or value < lowest:
            lowest = value
    if lowest is None:
        raise ValueError("solver artifact contains no Gmsh nodes")
    return lowest


def place_above_ground(msh_text: str, plane: GroundPlane) -> str:
    """Translate the solve mesh so it stands ``height_m`` above the plane.

    WG builds every solve mesh in the recentred origin frame -- a full-domain
    OSSE measures x, y in [-65.8, +65.8] mm and z in [-2, +60] mm -- so the
    model straddles all three candidate planes and the package rejects it
    outright.  ``mesh.vertical_offset`` cannot help: ``_solver_mesher_config``
    zeroes it for every solve, deliberately, because a reduced mesh's cut plane
    is pinned to the origin.  The translation therefore belongs here, at the
    adapter boundary, where the mesh is already final and no symmetry cut can
    be disturbed by moving it.

    Only the ground axis column is rewritten; the other two are passed through
    verbatim so a translation cannot perturb coordinates it does not own.  The
    observation frame is derived from this same text downstream, so polar data
    stays referenced to the mouth and follows the model up.
    """

    lowest = model_extent(msh_text, plane)
    if lowest + plane.height_m < 0.0:
        required = -lowest
        raise GroundPlaneClearance(
            f"This model reaches {lowest * 1000.0:.1f} mm along {plane.axis} in "
            f"its own frame, so at a height of {plane.height_m * 1000.0:.1f} mm "
            f"it would cross the ground plane. A rigid half space is bounded at "
            f"{plane.axis} = 0 and the whole model must stay above it: raise the "
            f"height to at least {required * 1000.0:.1f} mm, or turn the ground "
            f"plane off."
        )

    lines = msh_text.splitlines(keepends=True)
    bare = [line.rstrip("\r\n") for line in lines]
    first, last = _node_section(bare)
    component = plane.component
    for index in range(first, last):
        fields = bare[index].split()
        if len(fields) < 4:
            continue
        column = 1 + component
        fields[column] = repr(float(fields[column]) + plane.height_m)
        ending = lines[index][len(bare[index]):]
        lines[index] = " ".join(fields) + ending
    return "".join(lines)


#: Matches hornlab-bempp-bem's own ``_GROUND_OBSERVATION_TOLERANCE_M`` and
#: Boundary Lab's ``GROUND_TOLERANCE_M``: a fixed 1 um of real space, not a
#: model-scale relative term. A point landing exactly on the plane is on the
#: boundary of the physical domain, not outside it.
GROUND_OBSERVATION_TOLERANCE_M = 1.0e-6


def observation_below_ground_warning(
    plane: GroundPlane, polar_config: Mapping[str, Any]
) -> str | None:
    """Warn when the requested measurement arcs reach under the ground plane.

    Pressure returned below the plane is not a physical value: the
    representation formula is even about the plane, so it mirrors the point
    *above* it. hornlab-bempp-bem logs this and continues -- deliberately,
    because clipping the points would change array shapes and break every
    caller's angle indexing -- which means without this the only symptom is a
    polar plot whose lower angles are quietly the upper ones.

    It is easy to hit rather than exotic: WG's default 2 m measurement distance
    with a 1 m height puts 12 of a 37-point vertical arc under the floor.

    The bound used is the arc's lowest reachable point, ``height - distance``,
    which is exact for any arc or sphere that passes through the downward
    direction and conservative otherwise. Only the horizontal plane alone
    escapes it, since that arc stays at the model's own height.
    """

    distance = float(polar_config.get("distance") or 0.0)
    if distance <= 0.0:
        return None
    axes = tuple(polar_config.get("enabled_axes") or ())
    # The horizontal arc lies at the model's height and never descends. A
    # sphere is sampled in every direction, so it always descends.
    descends = bool(polar_config.get("spherical_sampling")) or any(
        axis != "horizontal" for axis in axes
    )
    if not descends:
        return None
    lowest = plane.height_m - distance
    if lowest >= -GROUND_OBSERVATION_TOLERANCE_M:
        return None
    return (
        f"Measurement points reach {abs(lowest) * 1000.0:.0f} mm below the "
        f"ground plane: the {distance:.3g} m measurement distance is larger "
        f"than the {plane.height_m:.3g} m height. Pressure there is the mirror "
        "of the point above the plane, not a physical value, so those angles "
        "are not usable. Raise the height above the measurement distance, or "
        "measure closer."
    )
