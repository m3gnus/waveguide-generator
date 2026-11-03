"""Whole-source area factors shared by CAD interpretation and solver adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np


#: Matches verify_symmetry_cut; the domain interpretation tests pin them equal.
TOLERANCE_MM = 1.0e-4


def source_completion_factors(built: Mapping[str, Any], solver_planes: Iterable[str]) -> dict[str, float]:
    """How much larger each source is than the faces CAD cuts on ``solver_planes`` left.

    A return's ``observed`` areas are the faces as drawn, so a driver cut
    through in CAD reports the part that is left. Each factor scales that area
    back to the whole driver. Planes WG cut itself never come here: a return
    WG cuts reports the uncut faces.
    """

    from server.mesh.tagged_msh import parse_tagged_msh
    from server.contracts.geometry import SYMMETRY_PLANE_AXIS
    from server.mesh.surface_topology import completion_factor

    axes = sorted(SYMMETRY_PLANE_AXIS[plane] for plane in solver_planes if plane in SYMMETRY_PLANE_AXIS)
    if not axes:
        return {}
    try:
        points_m, triangles, tags, _names = parse_tagged_msh(str(built.get("msh_text") or ""))
    except Exception:  # noqa: BLE001 - an unreadable mesh has no observations either
        return {}
    points = np.asarray(points_m, dtype=float) * 1000.0
    faces = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    tags = np.asarray(tags).reshape(-1)
    allocation = built.get("tag_allocation") or {}
    source_tags = allocation.get("source_tags") or built.get("source_tags") or {}
    return {
        str(source_id): completion_factor(points, faces[tags == int(tag)], axes, TOLERANCE_MM)
        for source_id, tag in source_tags.items()
    }
