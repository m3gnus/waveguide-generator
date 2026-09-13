"""Real CAD returns for the imported-solve qualification.

These build a small round horn as a STEP body, wrap it in a `.wgreturn` the way
the WGLink add-in writes a linked return, and put it through WG's own ingest,
so the records the engines are qualified on are the records a user's return
produces: tagged by role resolution, cut by WG's cutter, normalised into the
anchor frame.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

DESIGN_ID = "wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M"
_RETURN_IDS = {
    "round": "wgr_01J5A8QK3M9T2XVBH0RD7NWEF0",
    "placed": "wgr_01J5A8QK3M9T2XVBH0RD7NWEG0",
    "skewed": "wgr_01J5A8QK3M9T2XVBH0RD7NWEH0",
    "edited": "wgr_01J5A8QK3M9T2XVBH0RD7NWEJ0",
    "coarse": "wgr_01J5A8QK3M9T2XVBH0RD7NWEK0",
    "fine": "wgr_01J5A8QK3M9T2XVBH0RD7NWEM0",
    "rearcap": "wgr_01J5A8QK3M9T2XVBH0RD7NWEN0",
}


def horn_points(*, skew_mm: float = 0.0, n_phi: int = 16, n_length: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """A round horn along +z, throat at z = 0: symmetric about x0 and y0.

    ``skew_mm`` pushes the mouth towards +x, which keeps the y0 symmetry and
    breaks the x0 one -- the y-only half WG's cutter then produces.
    """

    inner = np.empty((n_phi, n_length + 1, 3), dtype=float)
    for phi_index in range(n_phi):
        phi = math.tau * phi_index / n_phi
        for length_index in range(n_length + 1):
            fraction = length_index / n_length
            radius = 10.0 + 20.0 * fraction
            inner[phi_index, length_index] = (
                radius * math.cos(phi) + skew_mm * fraction**2,
                radius * math.sin(phi),
                60.0 * fraction,
            )
    outer = inner.copy()
    centre = np.zeros_like(outer)
    centre[:, :, 0] = skew_mm * (np.arange(n_length + 1) / n_length) ** 2
    radial = np.linalg.norm((outer - centre)[:, :, :2], axis=2)
    scale = (radial + 4.0) / radial
    outer[:, :, 0] = centre[:, :, 0] + (outer[:, :, 0] - centre[:, :, 0]) * scale
    outer[:, :, 1] *= scale
    return inner, outer


def write_horn_step(path: Path, *, skew_mm: float = 0.0, source_shape: int = 1) -> Any:
    """Write the horn as a closed STEP body in its link frame, in mm."""

    from hornlab_mesher.cad import write_step
    from hornlab_mesher.geometry import PointGridHornGeometry

    from server.mesh.gmsh_worker import _run_in_gmsh_session

    inner, outer = horn_points(skew_mm=skew_mm)
    geometry = PointGridHornGeometry(
        inner_points=inner, outer_points=outer, wall_thickness_mm=4.0, source_shape=source_shape
    )
    _step, info = _run_in_gmsh_session(write_step, geometry, path, open_throat=False)
    return info


def _transform_step(path: Path, placement: np.ndarray) -> None:
    """Place the body rigidly, as CAD moves an occurrence.

    OCC's rotate and translate keep analytic surfaces; a general affine
    transform rewrites them as B-splines, and the throat would no longer be
    the plane the contract names.
    """

    import gmsh

    rotation = np.asarray(placement, dtype=float)[:3, :3]
    offset = np.asarray(placement, dtype=float)[:3, 3]
    angle = math.acos(max(-1.0, min(1.0, (float(np.trace(rotation)) - 1.0) / 2.0)))
    axis = np.asarray(
        [
            rotation[2, 1] - rotation[1, 2],
            rotation[0, 2] - rotation[2, 0],
            rotation[1, 0] - rotation[0, 1],
        ]
    )
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.clear()
    shapes = gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
    if angle > 1.0e-12:
        axis = axis / np.linalg.norm(axis)
        gmsh.model.occ.rotate(shapes, 0.0, 0.0, 0.0, *axis.tolist(), angle)
    gmsh.model.occ.translate(shapes, *offset.tolist())
    gmsh.model.occ.synchronize()
    gmsh.write(str(path))
    gmsh.clear()


def measure_throat(path: Path) -> dict[str, Any]:
    """The planar throat face of the written body, as CAD would report it."""

    from hornlab_mesher.step_import import gmsh_surface_tags

    from server.mesh.gmsh_worker import _run_in_gmsh_session

    def measure() -> dict[str, Any]:
        import gmsh

        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        planar = [
            surface
            for surface in gmsh_surface_tags()
            if str(gmsh.model.getType(2, surface)).casefold() == "plane"
        ]
        # The flat throat cap is the only planar face of a round horn; a
        # skewed horn keeps it at z = 0 as well.
        planar.sort(key=lambda surface: abs(gmsh.model.occ.getCenterOfMass(2, surface)[2]))
        centre = [float(value) for value in gmsh.model.occ.getCenterOfMass(2, planar[0])]
        area = float(gmsh.model.occ.getMass(2, planar[0]))
        bbox = [float(value) for value in gmsh.model.getBoundingBox(-1, -1)]
        gmsh.clear()
        return {"centre": centre, "area_mm2": area, "bbox_mm": [bbox[:3], bbox[3:]]}

    return _run_in_gmsh_session(measure)


def _bounding_box(path: Path) -> list[list[float]]:
    from server.mesh.gmsh_worker import _run_in_gmsh_session

    def measure() -> list[list[float]]:
        import gmsh

        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        bbox = [float(value) for value in gmsh.model.getBoundingBox(-1, -1)]
        gmsh.clear()
        return [bbox[:3], bbox[3:]]

    return _run_in_gmsh_session(measure)


def _fingerprint(volume: float) -> dict[str, Any]:
    return {"is_solid": True, "volume_mm3": volume, "bbox_mm": [0, 0, 0, 1, 1, 1]}


def linked_return(
    root: Path,
    name: str,
    *,
    placement: np.ndarray | None = None,
    skew_mm: float = 0.0,
    source_shape: int = 0,
    body_state: str = "unmodified",
) -> Path:
    """A `.wgreturn` as WGLink writes one for a linked instance of one design.

    ``placement`` is the instance's ``assembly_from_link``: the STEP body is
    written in the assembly frame, while the throat contract stays in the
    link's own frame, as the add-in records it. ``body_state="modified"`` is a
    return whose body was edited in CAD after WG exported it.

    ``source_shape=0`` makes the driver membrane a flat disc in the throat
    plane, facing the bore: the planar throat disc the add-in tags and WG's
    linked-throat gate accepts. With ``1`` the membrane is a rounded cap, so
    the only planar face left is the plug's rear, facing away from the bore --
    the "derived rear cap" the add-in strips -- and the contract binds to it.
    """

    placement = np.eye(4) if placement is None else np.asarray(placement, dtype=float)
    bundle = root / f"{name}.wgreturn"
    bundle.mkdir(parents=True)
    step_path = bundle / "assembly.step"
    # The throat is measured on the body in its link frame, where the add-in
    # records the contract; the placement moves the body afterwards.
    write_horn_step(step_path, skew_mm=skew_mm, source_shape=source_shape)
    measured = measure_throat(step_path)
    if not np.allclose(placement, np.eye(4)):
        from server.mesh.gmsh_worker import _run_in_gmsh_session

        _run_in_gmsh_session(_transform_step, step_path, placement)
        measured["bbox_mm"] = _bounding_box(step_path)
    step = step_path.read_bytes()
    area = float(measured["area_mm2"])
    throat_link = [float(value) for value in measured["centre"]]
    observed = 1.0 if body_state == "unmodified" else 2.0
    manifest = {
        "wgreturn_version": "1.0",
        "required_features": ["checksummed-files-v1", "assembly-frame-v1", "instance-records-v1"],
        "return": {"id": _RETURN_IDS[name], "created_at": "2026-09-13T09:14:03Z"},
        "generator": {"adapter": "qualification", "adapter_version": "1", "cad_app": "test", "cad_version": "1"},
        "document": {"name": name, "native_id": None},
        "coordinate_system": {
            "length_unit": "mm",
            "handedness": "right",
            "matrix_convention": "row-major-local-to-parent",
            "solver_anchor_instance_id": "anchor",
        },
        "assembly": {"file": "assembly.step", "n_bodies_expected": 1, "bbox_mm": measured["bbox_mm"]},
        "files": {
            "assembly.step": {
                "sha256": "sha256:" + hashlib.sha256(step).hexdigest(),
                "size_bytes": len(step),
                "media_type": "model/step",
                "purpose": "exterior-assembly",
            }
        },
        "scope": {
            "selection": "root",
            "included": [
                {
                    "object_id": name,
                    "name": name,
                    "body_kind": "solid",
                    "visible": True,
                    "external_reference": "local",
                    "wglink_instance_id": "anchor",
                }
            ],
            "skipped": [],
            "fem_air_volumes": [],
            "status": "clean",
        },
        "instances": [
            {
                "instance_id": "anchor",
                "design_id": DESIGN_ID,
                "lineage_id": None,
                "edit_version": 1,
                "design_hash": "sha256:" + "1" * 64,
                "config": {"root": {"mesh": {"vertical_offset": {"value": 0.0, "raw": None}}}},
                "export_id": "wge_01J4Y2ZD000000000000000000",
                "export_sequence": 1,
                "geometry_hash": "sha256:" + "2" * 64,
                "origin_bundle_id": "wgb_01J4Y2ZF000000000000000000",
                "build_mode": "freestanding",
                "parameter_prefix": "wg_horn_",
                "occurrence_path": name,
                "assembly_from_link": placement.tolist(),
                "chirality": "original",
                "body_evidence": {
                    "local_body_state": body_state,
                    "baseline_fingerprint": _fingerprint(1.0),
                    "observed_fingerprint": _fingerprint(observed),
                    "observed_at": "2026-09-13T09:14:03Z",
                },
                "source_contract": {
                    "role": "HF",
                    "throat_z_mm": throat_link[2],
                    "throat_plane_link": {"origin_mm": throat_link, "normal": [0, 0, 1]},
                    "axis_link": {"origin_mm": throat_link, "direction": [0, 0, 1]},
                    "throat_diameter_mm": math.sqrt(4.0 * area / math.pi),
                    "expected_disc_area_mm2": area,
                },
            }
        ],
        "sources": [
            {
                "id": "source-hf",
                "role": "HF",
                "instance_id": "anchor",
                "required": True,
                "default_drive_channel_id": "drive-hf",
                "patch_policy": "single-connected",
                "expected_connected_components": 1,
                "selectors": {"linked_throat": {"instance_id": "anchor"}},
                "observed": {"face_count": 1, "total_area_mm2": area, "per_face_area_mm2": [area], "bodies": [name]},
                "suggested_resolution_mm": 8,
            }
        ],
        "acoustics": None,
    }
    (bundle / "wgreturn.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return bundle


def mesh_sizes(scale: float = 1.0) -> dict[str, Any]:
    return {
        "rigid_size_mm": 20.0 * scale,
        "transition_mm": 30.0 * scale,
        "source_size_mm": {"source-hf": 8.0 * scale},
    }


@dataclass
class Ingested:
    record: dict[str, Any]
    store: Any
    data_dir: Path
    sizes: dict[str, Any]


def ingest(
    bundle: Path,
    data_dir: Path,
    *,
    sizes: Mapping[str, Any] | None = None,
    symmetry_mode: str = "auto",
    surface_deviation_mm: float | None = None,
) -> Ingested:
    """Prepare ``bundle`` into ``data_dir`` exactly as the app's ingest does.

    ``surface_deviation_mm`` is the chord-deviation dial the Prepare panel
    exposes; omitted, the server default applies.
    """

    from server.cadlink.ingest import ingest_bundle
    from server.cadlink.store import CadLinkStore
    from server.mesh.gmsh_worker import _run_in_gmsh_session

    sizes = dict(sizes or mesh_sizes())
    store = CadLinkStore(data_dir / "cadlink.db")
    prep_options: dict[str, Any] = {"symmetry_mode": symmetry_mode}
    if surface_deviation_mm is not None:
        prep_options["surface_deviation_mm"] = float(surface_deviation_mm)
    record = _run_in_gmsh_session(
        ingest_bundle,
        bundle,
        sizes,
        [],
        store,
        data_dir,
        prep_options=prep_options,
        # The user opened the design this linked return belongs to; on a fresh
        # install that design is not in the registry, which freshness reports.
        expected_design_id=DESIGN_ID,
    )
    return Ingested(record=record, store=store, data_dir=data_dir, sizes=sizes)


def acknowledgements(record: Mapping[str, Any]) -> list[str]:
    """The report acknowledgements a submission needs for blocking findings."""

    report = str(record.get("report_sha256") or "")
    return [
        f"{report}:{finding['id']}"
        for finding in record.get("findings") or []
        if isinstance(finding, Mapping) and finding.get("blocking")
    ]


def request_for_record(
    ingested: Ingested,
    *,
    engine: str,
    motion: str = "normal",
    frequencies: Sequence[float],
) -> Any:
    from server.jobs.models import SolveRequest

    record = ingested.record
    polar = record.get("polar_grid_derivation") or {}
    angle_range = polar.get("angle_range") or [-180.0, 180.0, 73]
    return SolveRequest.model_validate(
        {
            "geometry": {
                "type": "imported",
                "ingest_id": record["ingest_id"],
                "manifest_sha256": record["manifest_sha256"],
                "artifact_sha256": record["artifact_sha256"],
                "drive_channels": [{"id": "drive-hf", "source_ids": ["source-hf"], "motion": motion}],
                "mesh": ingested.sizes,
                "acknowledged_findings": acknowledgements(record),
            },
            "options": {
                "engine": engine,
                "frequencies_hz": list(frequencies),
                "polar_config": {
                    "angle_range": list(angle_range),
                    "distance": 2.0,
                    "enabled_axes": ["horizontal", "vertical", "diagonal"],
                },
            },
        }
    )


def placement_matrix(axis: Sequence[float], degrees: float, offset_mm: Sequence[float]) -> np.ndarray:
    k = np.asarray(axis, dtype=float)
    k /= np.linalg.norm(k)
    angle = math.radians(degrees)
    cross = np.asarray([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    rotation = np.eye(3) + math.sin(angle) * cross + (1 - math.cos(angle)) * cross @ cross
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = offset_mm
    return matrix
