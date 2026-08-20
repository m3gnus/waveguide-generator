"""Observe returned STEP topology behind the untrusted-CAD process boundary.

Split out of ``server/cadlink/onshape/return_leg.py`` so the inspect child can
import it without dragging the registry, the store, or the ingest pipeline in
with it.  Nothing here touches a database, a credential, or the application
data directory: it opens one checksum-verified STEP in OCC and reports bounded
evidence.  Everything that decides what to *do* with that evidence stays in the
parent (``docs/plans/STEP-PARSER-ISOLATION.md``).
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from server.mesh.imported import geometry_candidate_matches


_VOLUME_REL_TOLERANCE = 3.0e-5
_VOLUME_ABS_TOLERANCE_MM3 = 1.0e-3
_BBOX_REL_TOLERANCE = 1.0e-7
_BBOX_ABS_TOLERANCE_MM = 1.0e-2


class ReturnedStepError(ValueError):
    """The returned STEP cannot honestly satisfy the wgreturn contract."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _surface_normal(gmsh: Any, surface: int) -> np.ndarray:
    lower, upper = gmsh.model.getParametrizationBounds(2, surface)
    params = [
        (float(lower[0]) + float(upper[0])) / 2.0,
        (float(lower[1]) + float(upper[1])) / 2.0,
    ]
    normal = np.asarray(gmsh.model.getNormal(surface, params), dtype=float).reshape(-1, 3)[0]
    length = float(np.linalg.norm(normal))
    return normal / length if length > 0 else normal


def _fingerprints_match(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    if bool(first.get("is_solid")) != bool(second.get("is_solid")):
        return False
    try:
        left_volume = float(first["volume_mm3"])
        right_volume = float(second["volume_mm3"])
        left_bbox = [float(value) for value in first["bbox_mm"]]
        right_bbox = [float(value) for value in second["bbox_mm"]]
    except (KeyError, TypeError, ValueError):
        return False
    if len(left_bbox) != 6 or len(right_bbox) != 6:
        return False
    if not all(
        math.isfinite(value)
        for value in (left_volume, right_volume, *left_bbox, *right_bbox)
    ):
        return False
    if bool(first.get("is_solid")) and (left_volume <= 0 or right_volume <= 0):
        return False
    # A live Onshape import/export of the regression waveguide changed volume
    # by 2.33e-5 relative while moving its bounds by only 0.001745 mm. Keep a
    # narrow margin over that measured translation noise. This is deliberately
    # below the 3.11e-5 change of the same-bounds near-copy regression fixture.
    volume_tolerance = max(
        _VOLUME_ABS_TOLERANCE_MM3,
        _VOLUME_REL_TOLERANCE * max(abs(left_volume), abs(right_volume)),
    )
    if abs(left_volume - right_volume) > volume_tolerance:
        return False
    return all(
        # The outbound fingerprint is measured before STEP serialization.
        # OCC's write/read round trip expands that box by its modelling
        # tolerance (about 0.006 mm in the regression fixture), so identity
        # matching needs a machining-negligible 0.01 mm floor.
        abs(left - right)
        <= max(
            _BBOX_ABS_TOLERANCE_MM,
            _BBOX_REL_TOLERANCE * max(abs(left), abs(right)),
        )
        for left, right in zip(left_bbox, right_bbox, strict=True)
    )


def _select_linked_root(
    root_bodies: list[tuple[int, int]],
    root_fingerprints: Mapping[tuple[int, int], Mapping[str, Any]],
    baseline_fingerprint: Mapping[str, Any] | None,
) -> tuple[int, int]:
    """Resolve the linked body only when the stored evidence is unambiguous."""

    if len(root_bodies) == 1:
        return root_bodies[0]
    if baseline_fingerprint is None:
        raise ReturnedStepError(
            "The returned Onshape STEP contains multiple bodies and the stored outbound "
            "bundle has no body fingerprint. WG cannot identify the linked instance."
        )
    linked_candidates = [
        root
        for root in root_bodies
        if _fingerprints_match(root_fingerprints[root], baseline_fingerprint)
    ]
    if len(linked_candidates) != 1:
        raise ReturnedStepError(
            "The returned Onshape STEP contains multiple bodies, but WG could not "
            "identify exactly one as the stored outbound linked body. No instance "
            "identity was assigned by proximity or name."
        )
    return linked_candidates[0]


def observe_returned_step(
    step_path: Path,
    contract: Mapping[str, Any],
    baseline_fingerprint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Observe body inventory, bounds, and the linked throat in returned STEP.

    Runs inside the inspect child. The dict it returns is the *only* thing
    that crosses back, so it is deliberately small, JSON-shaped, and free of
    numpy scalars.
    """

    try:
        import gmsh
    except Exception as exc:  # pragma: no cover - packaged runtime owns gmsh
        raise ReturnedStepError("The CAD-return inspector requires Gmsh.") from exc

    gmsh.clear()
    gmsh.model.add("onshape-return-evidence")
    # ``highestDimOnly=True`` drops a standalone sheet whenever the same STEP
    # also contains a solid. Import every dimension, then define root bodies as
    # volumes plus surfaces that have no owning volume. Constituent solid faces
    # remain available for source matching but do not inflate the body count.
    imported = gmsh.model.occ.importShapes(str(step_path), highestDimOnly=False)
    gmsh.model.occ.synchronize()
    if not imported:
        raise ReturnedStepError("Onshape's STEP export contains no importable CAD bodies.")
    root_bodies = [(3, int(tag)) for _dim, tag in gmsh.model.getEntities(3)]
    root_bodies.extend(
        (2, int(surface))
        for _dim, surface in gmsh.model.getEntities(2)
        if len(gmsh.model.getAdjacencies(2, int(surface))[0]) == 0
    )
    if not root_bodies:
        raise ReturnedStepError("Onshape's STEP export contains no solid or surface bodies.")

    included: list[dict[str, Any]] = []
    bounds: list[tuple[float, ...]] = []
    root_names: dict[tuple[int, int], str] = {}
    root_fingerprints: dict[tuple[int, int], dict[str, Any]] = {}
    for index, (dim, tag) in enumerate(root_bodies, start=1):
        name = gmsh.model.getEntityName(dim, tag).strip() or f"Onshape body {index}"
        root_names[(dim, tag)] = name
        bbox = tuple(float(value) for value in gmsh.model.getBoundingBox(dim, tag))
        bounds.append(bbox)
        root_fingerprints[(dim, tag)] = {
            "is_solid": dim == 3,
            "volume_mm3": float(gmsh.model.occ.getMass(dim, tag)) if dim == 3 else 0.0,
            "bbox_mm": list(bbox),
        }
        included.append(
            {
                "object_id": f"onshape:{dim}:{tag}",
                "name": name,
                "body_kind": "solid" if dim == 3 else "surface",
                "visible": True,
                "external_reference": "current linked Onshape Part Studio",
                "wglink_instance_id": None,
            }
        )

    low = [min(item[index] for item in bounds) for index in range(3)]
    high = [max(item[index + 3] for item in bounds) for index in range(3)]
    plane_origin = np.asarray(contract["throat_plane_link"]["origin_mm"], dtype=float)
    plane_normal = np.asarray(contract["throat_plane_link"]["normal"], dtype=float)
    plane_normal /= np.linalg.norm(plane_normal)
    axis_origin = np.asarray(contract["axis_link"]["origin_mm"], dtype=float)
    axis_direction = np.asarray(contract["axis_link"]["direction"], dtype=float)
    axis_direction /= np.linalg.norm(axis_direction)
    matches: list[dict[str, Any]] = []
    for _dim, surface in gmsh.model.getEntities(2):
        center = np.asarray(gmsh.model.occ.getCenterOfMass(2, surface), dtype=float)
        try:
            normal = _surface_normal(gmsh, int(surface))
            angle = math.degrees(
                math.acos(min(1.0, max(-1.0, abs(float(np.dot(normal, plane_normal))))))
            )
        except Exception:
            angle = math.inf
        delta = center - axis_origin
        candidate = {
            "face_id": int(surface),
            "planar": str(gmsh.model.getType(2, surface)).casefold() == "plane",
            "plane_distance_mm": abs(float(np.dot(center - plane_origin, plane_normal))),
            "normal_angle_deg": angle,
            "centroid_axis_distance_mm": float(
                np.linalg.norm(delta - np.dot(delta, axis_direction) * axis_direction)
            ),
            "area_mm2": float(gmsh.model.occ.getMass(2, surface)),
        }
        if geometry_candidate_matches(candidate, contract):
            matches.append(candidate)
    if len(matches) != 1:
        raise ReturnedStepError(
            "The returned Onshape STEP resolved the required linked throat to "
            f"{len(matches)} faces; exactly one is required. No source evidence was invented."
        )

    linked_root = _select_linked_root(
        root_bodies, root_fingerprints, baseline_fingerprint
    )

    linked_object_id = f"onshape:{linked_root[0]}:{linked_root[1]}"
    for item in included:
        if item["object_id"] == linked_object_id:
            item["wglink_instance_id"] = "__LINKED_INSTANCE__"
            break

    source_face = matches[0]
    parents, _children = gmsh.model.getAdjacencies(2, int(source_face["face_id"]))
    parent_volume = int(parents[0]) if len(parents) == 1 else None
    source_body_name = root_names.get((3, parent_volume)) if parent_volume is not None else None
    source_body_name = source_body_name or root_names.get((2, int(source_face["face_id"])))
    source_body_name = source_body_name or "Onshape source body"
    if parent_volume is not None:
        object_id = f"onshape:3:{parent_volume}"
        for item in included:
            if item["object_id"] == object_id:
                source_body_name = str(item["name"])
                break

    signature_evidence = {
        "bodies": [
            {"id": item["object_id"], "kind": item["body_kind"], "bbox_mm": list(bounds[index])}
            for index, item in enumerate(included)
        ],
        "source": source_face,
    }
    observed_fingerprint = root_fingerprints[linked_root]
    return {
        "included": included,
        "bbox_mm": [low, high],
        "n_bodies": len(root_bodies),
        "signature_hash": _sha256(_canonical(signature_evidence)),
        "observed_fingerprint": observed_fingerprint,
        "source_observed": {
            "face_count": 1,
            "total_area_mm2": source_face["area_mm2"],
            "per_face_area_mm2": [source_face["area_mm2"]],
            "bodies": [source_body_name],
        },
    }


__all__ = [
    "ReturnedStepError",
    "observe_returned_step",
]
