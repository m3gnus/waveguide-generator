"""Build an immutable Onshape-authored ``wgreturn`` and ingest it.

The Onshape adapter is server-side, so the CAD evidence is observed from the
exact STEP bytes returned by Onshape.  Identity and source contracts come only
from the registry's exact outbound export manifest; no parameter or role is
reverse-engineered from returned geometry.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

import numpy as np

from server.cadlink.identity import mint_id
from server.cadlink.ingest import ingest_bundle
from server.cadlink.store import CadLinkStore
from server.mesh.imported import geometry_candidate_matches


RETURN_SUBDIRECTORY = Path("cadlink") / "onshape" / "wgreturn"
SOURCE_INTERFACE_FEATURE = "source-interface-v1"


class OnshapeReturnError(ValueError):
    """The linked document cannot honestly satisfy the wgreturn contract."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OnshapeReturnError(f"Stored outbound evidence is missing {label}.")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise OnshapeReturnError(f"Stored outbound evidence is missing {label}.")
    return value


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _source_policy_from_export(manifest: Mapping[str, Any]) -> dict[str, Any]:
    features = manifest.get("required_features")
    interface = manifest.get("interface")
    sources = interface.get("sources") if isinstance(interface, Mapping) else None
    if (
        not isinstance(features, list)
        or SOURCE_INTERFACE_FEATURE not in features
        or not isinstance(sources, list)
        or not sources
    ):
        raise OnshapeReturnError(
            "The linked outbound bundle has no WG-authored return-source interface. "
            "Its throat geometry is known, but source role, source id, and drive-channel "
            "identity are not; WG will not default them to HF."
        )
    if len(sources) != 1 or not isinstance(sources[0], Mapping):
        raise OnshapeReturnError(
            "The Onshape linked-throat return currently requires exactly one "
            "wglink.interface.sources[] record."
        )
    raw = sources[0]
    policy = dict(raw)
    for key in ("id", "role", "default_drive_channel_id", "patch_policy"):
        _string(policy.get(key), f"wglink.interface.sources[0].{key}")
    if not isinstance(policy.get("required"), bool):
        raise OnshapeReturnError(
            "Stored outbound evidence is missing wglink.interface.sources[0].required."
        )
    components = policy.get("expected_connected_components")
    if isinstance(components, bool) or not isinstance(components, int) or components < 1:
        raise OnshapeReturnError(
            "Stored outbound evidence has an invalid return-source component count."
        )
    if policy["patch_policy"] != "single-connected":
        raise OnshapeReturnError(
            "The managed linked-throat return source must be single-connected."
        )
    if components != 1:
        raise OnshapeReturnError(
            "A single-connected return source must declare exactly one component."
        )
    resolution = policy.get("suggested_resolution_mm")
    try:
        resolution = float(resolution)
    except (TypeError, ValueError) as exc:
        raise OnshapeReturnError(
            "Stored outbound return-source resolution is missing or invalid."
        ) from exc
    if not math.isfinite(resolution) or resolution <= 0:
        raise OnshapeReturnError(
            "Stored outbound return-source resolution is missing or invalid."
        )
    policy["suggested_resolution_mm"] = resolution
    return policy


def source_contract_from_export(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the linked throat geometry only from stored WG-authored evidence."""

    source_policy = _source_policy_from_export(manifest)

    datums = _mapping(manifest.get("datums"), "wglink.datums")
    throat = _mapping(datums.get("WG_THROAT_PLANE"), "wglink.datums.WG_THROAT_PLANE")
    axis = _mapping(datums.get("WG_AXIS"), "wglink.datums.WG_AXIS")
    parameters = manifest.get("parameters")
    if not isinstance(parameters, list):
        raise OnshapeReturnError("Stored outbound evidence is missing wglink.parameters.")
    throat_parameter = next(
        (
            item
            for item in parameters
            if isinstance(item, Mapping)
            and isinstance(item.get("name"), str)
            and str(item["name"]).endswith("_throat_dia")
        ),
        None,
    )
    if throat_parameter is None:
        raise OnshapeReturnError(
            "The linked outbound bundle has no managed throat-diameter parameter, "
            "so WG cannot author a source contract for this return."
        )
    try:
        diameter = float(throat_parameter["value"])
        origin = [float(value) for value in throat["origin_mm"]]
        normal = [float(value) for value in throat["normal"]]
        axis_origin = [float(value) for value in axis["origin_mm"]]
        axis_direction = [float(value) for value in axis["direction"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise OnshapeReturnError(
            "The linked outbound bundle's throat datums are incomplete."
        ) from exc
    if diameter <= 0 or len(origin) != 3 or len(normal) != 3 or len(axis_origin) != 3 or len(axis_direction) != 3:
        raise OnshapeReturnError("The linked outbound bundle's throat contract is invalid.")
    return {
        "role": source_policy["role"],
        "throat_z_mm": origin[2],
        "throat_plane_link": {"origin_mm": origin, "normal": normal},
        "axis_link": {"origin_mm": axis_origin, "direction": axis_direction},
        "throat_diameter_mm": diameter,
        "expected_disc_area_mm2": math.pi * diameter * diameter / 4.0,
    }


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
    volume_tolerance = max(1.0e-3, 1.0e-6 * max(abs(left_volume), abs(right_volume)))
    if abs(left_volume - right_volume) > volume_tolerance:
        return False
    return all(
        abs(left - right) <= max(1.0e-4, 1.0e-7 * max(abs(left), abs(right)))
        for left, right in zip(left_bbox, right_bbox, strict=True)
    )


def inspect_step(
    step_path: Path,
    contract: Mapping[str, Any],
    baseline_fingerprint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Observe body inventory, bounds, and the linked throat in returned STEP."""

    try:
        import gmsh
    except Exception as exc:  # pragma: no cover - packaged runtime owns gmsh
        raise OnshapeReturnError("The Onshape return inspector requires Gmsh.") from exc

    gmsh.clear()
    gmsh.model.add("onshape-return-evidence")
    roots = gmsh.model.occ.importShapes(str(step_path), highestDimOnly=True)
    gmsh.model.occ.synchronize()
    if not roots:
        raise OnshapeReturnError("Onshape's STEP export contains no importable CAD bodies.")
    root_bodies = [(int(dim), int(tag)) for dim, tag in roots if int(dim) in {2, 3}]
    if not root_bodies:
        raise OnshapeReturnError("Onshape's STEP export contains no solid or surface bodies.")

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
        raise OnshapeReturnError(
            "The returned Onshape STEP resolved the required linked throat to "
            f"{len(matches)} faces; exactly one is required. No source evidence was invented."
        )

    if len(root_bodies) == 1:
        linked_root = root_bodies[0]
    elif baseline_fingerprint is not None:
        linked_candidates = [
            root
            for root in root_bodies
            if _fingerprints_match(root_fingerprints[root], baseline_fingerprint)
        ]
        if len(linked_candidates) != 1:
            raise OnshapeReturnError(
                "The returned Onshape STEP contains multiple bodies, but WG could not "
                "identify exactly one as the stored outbound linked body. No instance "
                "identity was assigned by proximity or name."
            )
        linked_root = linked_candidates[0]
    else:
        raise OnshapeReturnError(
            "The returned Onshape STEP contains multiple bodies and the stored outbound "
            "bundle has no body fingerprint. WG cannot identify the linked instance."
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


def _baseline_fingerprint(outbound: Mapping[str, Any]) -> dict[str, Any] | None:
    body = outbound.get("body")
    if not isinstance(body, Mapping):
        return None
    bbox = body.get("bbox_mm")
    if not (
        isinstance(bbox, list)
        and len(bbox) == 2
        and all(isinstance(row, list) and len(row) == 3 for row in bbox)
    ):
        return None
    try:
        return {
            "is_solid": str(body.get("kind")) == "solid",
            "volume_mm3": float(body["volume_mm3"]),
            "bbox_mm": [float(value) for row in bbox for value in row],
        }
    except (KeyError, TypeError, ValueError):
        return None


def write_return_bundle(
    step_bytes: bytes,
    *,
    link: Mapping[str, Any],
    export_row: Mapping[str, Any],
    data_dir: str | Path,
) -> Path:
    """Publish one checksummed immutable bundle under WG's Onshape data area."""

    if not step_bytes.startswith(b"ISO-10303") and b"ISO-10303" not in step_bytes[:4096]:
        raise OnshapeReturnError(
            "Onshape's completed translation did not return a STEP Part 21 file."
        )
    try:
        outbound = json.loads(str(export_row["manifest_json"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise OnshapeReturnError("The linked export's stored manifest is unreadable.") from exc
    if not isinstance(outbound, Mapping):
        raise OnshapeReturnError("The linked export's stored manifest is not an object.")
    source_policy = _source_policy_from_export(outbound)
    contract = source_contract_from_export(outbound)
    return_id = mint_id("wgr_")
    created_at = _utc_now()
    root = Path(data_dir).resolve() / RETURN_SUBDIRECTORY
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".onshape-return-", dir=root))
    target = root / f"{return_id}.wgreturn"
    instance_id = f"onshape-{_string(link.get('part_studio_element_id'), 'Part Studio identity')}"
    try:
        assembly = temporary / "assembly.step"
        assembly.write_bytes(step_bytes)
        baseline = _baseline_fingerprint(outbound)
        observed = inspect_step(assembly, contract, baseline)
        for item in observed["included"]:
            if item.get("wglink_instance_id") == "__LINKED_INSTANCE__":
                item["wglink_instance_id"] = instance_id
        design = _mapping(outbound.get("design"), "wglink.design")
        exported = _mapping(outbound.get("export"), "wglink.export")
        bundle = _mapping(outbound.get("bundle"), "wglink.bundle")
        parameter = next(
            item for item in outbound["parameters"]
            if isinstance(item, Mapping) and str(item.get("name", "")).endswith("_throat_dia")
        )
        parameter_prefix = str(parameter["name"])[: -len("throat_dia")]
        manifest = {
            "wgreturn_version": "1.1",
            "required_features": [
                "checksummed-files-v1",
                "assembly-frame-v1",
                "instance-records-v1",
            ],
            "return": {"id": return_id, "created_at": created_at},
            "generator": {
                "adapter": "waveguide-generator/Onshape",
                "adapter_version": "1.0",
                "cad_app": "onshape",
                "cad_version": "cloud-api",
            },
            "document": {
                "name": _string(link.get("document_name"), "Onshape document name"),
                "native_id": _string(link.get("document_id"), "Onshape document identity"),
            },
            "coordinate_system": {
                "length_unit": "mm",
                "handedness": "right",
                "matrix_convention": "row-major-local-to-parent",
                "solver_anchor_instance_id": instance_id,
            },
            "assembly": {
                "file": "assembly.step",
                "n_bodies_expected": observed["n_bodies"],
                "bbox_mm": observed["bbox_mm"],
                "signature_hash": observed["signature_hash"],
            },
            "files": {
                "assembly.step": {
                    "sha256": _sha256(step_bytes),
                    "size_bytes": len(step_bytes),
                    "media_type": "model/step",
                    "purpose": "exterior-assembly",
                }
            },
            "scope": {
                "selection": "linked-onshape-part-studio",
                "included": observed["included"],
                "skipped": [],
                "status": "clean",
                "fem_air_volumes": [],
            },
            "instances": [
                {
                    "instance_id": instance_id,
                    "design_id": _string(design.get("id"), "wglink.design.id"),
                    "lineage_id": design.get("lineage_id"),
                    "edit_version": design.get("edit_version"),
                    "design_hash": design.get("design_hash"),
                    "formula": design.get("formula"),
                    "config": design.get("config"),
                    "export_id": _string(exported.get("id"), "wglink.export.id"),
                    "export_sequence": int(exported["sequence"]),
                    "geometry_hash": exported.get("geometry_hash"),
                    "origin_bundle_id": _string(bundle.get("id"), "wglink.bundle.id"),
                    "occurrence_path": f"Part Studio/{link['part_studio_element_id']}",
                    "build_mode": _string(design.get("build_mode"), "wglink.design.build_mode"),
                    "parameter_prefix": parameter_prefix,
                    "assembly_from_link": [
                        [1, 0, 0, 0],
                        [0, 1, 0, 0],
                        [0, 0, 1, 0],
                        [0, 0, 0, 1],
                    ],
                    "chirality": "original",
                    "body_evidence": {
                        # O3 did not capture a post-translation Onshape baseline.
                        # The exact returned observation is retained, but it is
                        # not promoted to an unmodified verdict.
                        "local_body_state": "unknown",
                        "baseline_fingerprint": baseline,
                        "observed_fingerprint": observed["observed_fingerprint"],
                        "observed_at": created_at,
                    },
                    "source_contract": contract,
                }
            ],
            "sources": [
                {
                    "id": source_policy["id"],
                    "role": source_policy["role"],
                    "instance_id": instance_id,
                    "required": source_policy["required"],
                    "default_drive_channel_id": source_policy[
                        "default_drive_channel_id"
                    ],
                    "patch_policy": source_policy["patch_policy"],
                    "expected_connected_components": source_policy[
                        "expected_connected_components"
                    ],
                    "selectors": {"linked_throat": {"instance_id": instance_id}},
                    "observed": observed["source_observed"],
                    "suggested_resolution_mm": source_policy[
                        "suggested_resolution_mm"
                    ],
                }
            ],
            "acoustics": None,
        }
        (temporary / "wgreturn.json").write_bytes(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
        )
        if target.exists():
            raise OnshapeReturnError(f"Return id collision at {target.name}.")
        os.replace(temporary, target)
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def write_and_ingest_return(
    step_bytes: bytes,
    *,
    link: Mapping[str, Any],
    export_row: Mapping[str, Any],
    store: CadLinkStore,
    data_dir: str | Path,
) -> tuple[Path, dict[str, Any]]:
    try:
        outbound = json.loads(str(export_row["manifest_json"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise OnshapeReturnError("The linked export's stored manifest is unreadable.") from exc
    if not isinstance(outbound, Mapping):
        raise OnshapeReturnError("The linked export's stored manifest is not an object.")
    source_policy = _source_policy_from_export(outbound)
    bundle_path = write_return_bundle(
        step_bytes, link=link, export_row=export_row, data_dir=data_dir
    )
    mesh = {
        "rigid_size_mm": float(source_policy["suggested_resolution_mm"]),
        "transition_mm": float(source_policy["suggested_resolution_mm"]),
        "source_size_mm": {
            str(source_policy["id"]): float(source_policy["suggested_resolution_mm"])
        },
    }
    record = ingest_bundle(bundle_path, mesh, [], store, data_dir)
    return bundle_path, record


__all__ = [
    "OnshapeReturnError",
    "RETURN_SUBDIRECTORY",
    "inspect_step",
    "source_contract_from_export",
    "write_and_ingest_return",
    "write_return_bundle",
]
