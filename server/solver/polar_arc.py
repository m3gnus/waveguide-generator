"""The observation arc a solve actually uses.

Kept apart from ``server.solver.context`` and free of module-level imports from
``server.jobs``: the job runtime imports these helpers while ``server.jobs`` is
still initialising, and ``context`` imports ``server.jobs.models``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.jobs.models import PolarConfig, SolveRequest

# The arc every client sends when the user has not touched the directivity
# settings: the model default and the browser's ATH default agree on it.
_DEFAULT_ARC = (0.0, 180.0, 37)
_DEFAULT_ARC_STEP = 5.0
# A coupled infinite baffle radiates nothing into the rear half space, so an
# observation arc past 90 degrees only samples the zero-pressure floor.
_INFINITE_BAFFLE_ARC = (0.0, 90.0, 19)


def _uses_default_arc(polar: "PolarConfig") -> bool:
    if "angle_range" not in polar.model_fields_set:
        return True
    start, end, count = polar.angle_range
    step = polar.angle_step
    return (
        (float(start), float(end), int(count)) == _DEFAULT_ARC
        and (step is None or float(step) == _DEFAULT_ARC_STEP)
    )


def polar_config_for(polar: "PolarConfig", *, sim_type: int) -> dict[str, Any]:
    """Serialise the polar config, defaulting the arc to 0-90 for infinite baffle.

    Only an arc the user has not set is replaced: it must be the untouched
    0-180 default (a client cannot mark "untouched" on the wire, so the default
    value itself is the signal). Any other arc is kept as given.
    """

    config = polar.model_dump(mode="json")
    if (
        sim_type == 1
        and not polar.spherical_sampling
        and _uses_default_arc(polar)
        # A normalisation angle behind the front half space cannot live in a
        # 0-90 arc (PolarConfig refuses it), and clamping it would change the
        # user's normalisation silently: keep the full arc instead.
        and polar.norm_angle <= _INFINITE_BAFFLE_ARC[1]
    ):
        config["angle_range"] = list(_INFINITE_BAFFLE_ARC)
        config["angle_step"] = 5.0
    return config


def effective_polar_config(request: "SolveRequest") -> dict[str, Any]:
    """The polar config a request will actually observe (see ``polar_config_for``)."""

    from server.jobs.models import ImportedGeometrySource

    polar = request.options.polar_config
    if isinstance(request.geometry, ImportedGeometrySource):
        return polar.model_dump(mode="json")
    sim_type = 1 if request.design.root.simulation.sim_type == "infinite-baffle" else 2
    return polar_config_for(polar, sim_type=sim_type)


def effective_polar_grid(request: "SolveRequest") -> dict[str, Any]:
    """The polar grid a request will actually observe, as ``resolved_grid`` states it."""

    from server.jobs.models import PolarConfig

    return PolarConfig.model_validate(effective_polar_config(request)).resolved_grid()
