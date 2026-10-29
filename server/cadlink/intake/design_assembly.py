"""A design's Sources and baffle assembly, compiled into an immutable prepared mesh.

The third prepared-mesh producer, beside an imported file and a CAD return.
The horn is always the design's live horn and ``design.enclosure`` the only
box: the design mesher builds both, with the enabled woofers cut into the
front baffle, in the same killable child and under the same triangle and
memory guards as every design mesh (``server/mesh/builder.py``). The artifact
then passes the ready-mesh validation and normalisation imported files use
(``server/mesh/file_import.py``), and the record is sealed by the shared
prepared-record owner. Nothing here re-implements geometry, roles or symmetry.

A build captures one design revision. It is published only while that
revision is still the newest one asked for its design (``AssemblyBuildFence``,
checked inside the publishing transaction), so a superseded build never
replaces a newer one, and identical content is the record already published.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import threading
from typing import Any

import numpy as np

from server.cadlink.identity import mint_id, utc_now
from server.cadlink.store import StaleAttempt, design_model_id
from server.contracts.assembly import (
    ASSEMBLY_RECORD_FEATURE,
    HF_RADIATOR_ID,
    MESHER_TAG_NAMESPACE,
    source_role,
    split_source_id,
)
from server.contracts.canonical import canonical_ascii_strict_bytes
from server.design.schema import DesignConfig
from server.integration.mesher_identity import mesher_identity
from server.mesh.artifact import read_verified_import_mesh
from server.mesh.builder import (
    AssemblyRefusal,
    _solver_mesher_config,
    assembly_woofer_rows,
    build_assembly_mesh,
)
from server.mesh.file_import import file_mesh_integrity, mesh_text, parse_file_mesh, prepare_file_mesh, solve_group_tags
from server.mesh.imported import polar_grid_from_symmetry
from server.mesh.prepared_gc import prepared_mesh_use
from server.mesh.prepared_record import seal_prepared_record
from server.platform.staging import publish_staging_directory
from server.workspace.archive_identity import claim_project_folder

PRODUCER = "design-assembly"
RECORD_VERSION = 1
#: Identity of the solver frame: the design mesher's horn-throat frame, metres.
SOLVER_FRAME = "design-mesher-horn-throat-v1"
_IDENTITY_MATRIX = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_ascii_strict_bytes(value)).hexdigest()


class AssemblySuperseded(RuntimeError):
    """A newer revision of the same design was asked for; this build is not published."""


@dataclass
class AssemblyBuildFence:
    """The newest design revision asked for, per design: only it may publish."""

    _latest: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def begin(self, design_uid: str, revision: int) -> tuple[str, int]:
        with self._lock:
            if revision < self._latest.get(design_uid, revision):
                raise AssemblySuperseded("A newer revision of this design is already being prepared.")
            self._latest[design_uid] = revision
        return design_uid, revision

    def current(self, token: tuple[str, int]) -> bool:
        with self._lock:
            return self._latest.get(token[0]) == token[1]


FENCE = AssemblyBuildFence()


def geometry_spec(design: DesignConfig) -> dict[str, Any]:
    """What the compiled artifact is built from: the design mesher's own inputs.

    The horn and box are the design's solver-mesh translation, as the design
    mesh cache keys them; woofers are their placement rows; groups are their
    membership. Drivers, member processing, the crossover, the sweep and
    labels belong to the setup and the document, so changing them reuses the
    published record.
    """

    assembly = design.root.assembly
    assert assembly is not None
    return {
        "mesher_config": _solver_mesher_config(design),
        "woofers": assembly_woofer_rows(design),
        "signal_groups": [
            {"id": group.id, "members": [member.radiator_id for member in group.members]}
            for group in assembly.signal_groups
        ],
    }


def compile_spec(design: DesignConfig) -> dict[str, Any]:
    """Everything the compiled artifact and its interpretation depend on.

    A change here is a new record; any other change reuses the published one,
    and drivers, member processing and the crossover reach a solve through
    its setup.
    """

    return {
        "producer": PRODUCER,
        "version": RECORD_VERSION,
        "design_geometry_sha256": _digest(geometry_spec(design)),
        "mesher_identity": mesher_identity(),
        "solved_domain": "full",
    }


def _intact(record: Mapping[str, Any]) -> bool:
    try:
        read_verified_import_mesh(record)
        return True
    except (OSError, ValueError):
        return False


def _areas_mm2(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    corners = points[triangles]
    return np.linalg.norm(
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1
    ) * 0.5e6


def _radiators(design: DesignConfig, built: Mapping[str, Any], source_ids: list[str]) -> list[dict[str, Any]]:
    """One record per physical radiator, enabled or not, by stable identity."""

    assembly = design.root.assembly
    assert assembly is not None
    placed = {row["radiator_id"]: row for row in built["placement"]["woofers"]}
    patches = {patch["patch_id"]: patch for patch in (built["metadata"].get("assembly") or {}).get("patches", [])}
    radiators: list[dict[str, Any]] = [{
        "radiator_id": HF_RADIATOR_ID, "kind": assembly.hf_source.kind, "label": "HF",
        "state": "compiled", "role": "HF", "motion": "normal",
        "source_ids": [HF_RADIATOR_ID],
    }]
    for woofer in assembly.woofers:
        ids = [source_id for source_id in source_ids if split_source_id(source_id)[0] == woofer.id]
        state = "disabled" if not woofer.enabled else "rigid" if woofer.drive == "rigid" else "compiled"
        entry: dict[str, Any] = {
            "radiator_id": woofer.id, "kind": woofer.shape.kind, "label": woofer.label,
            "state": state, "role": "LF", "motion": "axial", "motion_axis": [0.0, 0.0, 1.0],
            "radius_mm": woofer.radius_mm,
            # Full-driver Sd is the projected aperture, not the cone's skin area.
            "projected_area_m2": math.pi * (woofer.radius_mm * 1e-3) ** 2,
            "source_ids": ids,
        }
        if woofer.id in placed:
            entry["center_mm"] = [placed[woofer.id]["x_mm"], placed[woofer.id]["y_mm"]]
        if ids:
            entry["patches"] = [
                {key: patches[source_id][key] for key in ("segment_id", "physical_tag", "recess_mm", "area_mm2")
                 if key in patches.get(source_id, {})}
                for source_id in ids
            ]
        radiators.append(entry)
    return radiators


def _excitation_symmetry(radiators: list[dict[str, Any]]) -> dict[str, Any]:
    """Which mirror each excitation is invariant under, from its own placement.

    A horn-only design keeps no claim here: the full domain is solved and
    nothing is reduced. A woofer is its own mirror image only on that plane
    through its centre; mirrored geometry alone never makes two excitations
    one, and independent processing would break it anyway.
    """

    result: dict[str, Any] = {}
    for radiator in radiators:
        if radiator["state"] != "compiled" or radiator["radiator_id"] == HF_RADIATOR_ID:
            continue
        x, y = radiator["center_mm"]
        result[radiator["radiator_id"]] = {"x0": x == 0, "y0": y == 0}
    return result


def compose_record(
    design: DesignConfig, built: Mapping[str, Any], *, ingest_id: str, created_at: str,
    design_uid: str, design_name: str, design_revision: int, spec_digest: str,
    archive_stem: str, destination: Path,
) -> tuple[dict[str, Any], str]:
    """The record of one compiled build and the normalised solver mesh it names."""

    metadata = built["metadata"]
    assembly_meta = metadata.get("assembly") or {}
    tag_by_source = {str(key): int(value) for key, value in (metadata.get("source_tags") or {}).items()}
    if not tag_by_source:
        # A horn-only build is the ordinary design mesh: its throat is tag 2.
        tag_by_source = {HF_RADIATOR_ID: 2}
        mesher_roles = {HF_RADIATOR_ID: "primary"}
    else:
        mesher_roles = {
            str(entry["source_id"]): str(entry["role"])
            for entry in (metadata.get("tag_map") or {}).values() if entry.get("source_id")
        }
    roles = {source_id: source_role(mesher_roles[source_id]) for source_id in tag_by_source}
    original = parse_file_mesh(str(built["msh_text"]))
    by_tag = {tag: roles[source_id] for source_id, tag in tag_by_source.items()}
    # The design mesher writes metres in its horn-throat frame: identity frame.
    mesh, transform = prepare_file_mesh(original, "m", "+z", "+y", roles=by_tag)
    written = solve_group_tags(mesh)
    solver_text = mesh_text(mesh)
    areas = _areas_mm2(mesh.points, mesh.triangles)
    source_ids = list(tag_by_source)
    sources = [
        {
            "id": source_id,
            "role": roles[source_id],
            "required": True,
            "name": "HF horn throat" if source_id == HF_RADIATOR_ID else source_id,
            "radiator_id": split_source_id(source_id)[0],
            "segment_id": split_source_id(source_id)[1],
            "observed": {"total_area_mm2": float(areas[mesh.tags == tag].sum())},
        }
        for source_id, tag in tag_by_source.items()
    ]
    radiators = _radiators(design, built, source_ids)
    assembly = design.root.assembly
    assert assembly is not None
    compiled = {radiator["radiator_id"] for radiator in radiators if radiator["state"] == "compiled"}
    excitations = [
        {
            "id": radiator["radiator_id"],
            "source_ids": radiator["source_ids"],
            "motion": radiator["motion"],
            **({"motion_axis": radiator["motion_axis"]} if "motion_axis" in radiator else {}),
            # One excitation per radiator: every patch moves with unit weight.
            "patch_weights": {source_id: 1.0 for source_id in radiator["source_ids"]},
        }
        for radiator in radiators if radiator["radiator_id"] in compiled
    ]
    # Group inventory only: member processing is the setup's.
    groups = [
        {"id": group.id, "label": group.label,
         "members": [member.radiator_id for member in group.members if member.radiator_id in compiled]}
        for group in assembly.signal_groups
    ]
    symmetry = {
        "domain_planes": [], "cut_planes": [], "reflected_planes": [],
        "solved_domain": "full",
        # The first cut solves the complete assembly; no reduction is claimed.
        "reduction": None,
        "excitation_mirror_invariance": _excitation_symmetry(radiators),
    }
    record: dict[str, Any] = {
        "ingest_id": ingest_id,
        "created_at": created_at,
        "producer": PRODUCER,
        "record_version": RECORD_VERSION,
        "required_features": [ASSEMBLY_RECORD_FEATURE],
        "manifest_sha256": spec_digest,
        "artifact_sha256": _digest(geometry_spec(design)),
        "mesh_content_sha256": "sha256:" + hashlib.sha256(solver_text.encode("utf-8")).hexdigest(),
        "mesh_size_bytes": len(solver_text.encode("utf-8")),
        "mesh_store_path": str(destination / "solve.msh"),
        "design": {
            "design_uid": design_uid, "name": design_name, "revision": design_revision,
            # The captured revision: the whole design and its assembly as built.
            "content_sha256": _digest(design.model_dump(mode="json")),
            "assembly_sha256": _digest(assembly.model_dump(mode="json")),
            "geometry_sha256": _digest(geometry_spec(design)),
        },
        "archive_stem": archive_stem,
        "document": {"name": design_name},
        "sources": sources,
        "source_tags": {source_id: written[tag] for source_id, tag in tag_by_source.items()},
        "tag_namespace": MESHER_TAG_NAMESPACE,
        "tag_map": {
            str(written[tag]): {"source_id": source_id, "role": roles[source_id]}
            for source_id, tag in tag_by_source.items()
        },
        "radiators": radiators,
        "excitations": excitations,
        "signal_groups": groups,
        "findings": [],
        "skipped_source_ids": [],
        "acoustic_domain": "free-space",
        "normalisation": {
            "assembly_frame_is_solver_frame": True,
            "solver_frame": SOLVER_FRAME,
            "matrix": _IDENTITY_MATRIX,
            "observation_origin_m": [0.0, 0.0, 0.0],
            "transform": transform,
        },
        "symmetry": symmetry,
        "domain_interpretation": {"type": "design-assembly-full-domain"},
        # No CAD instances: the design itself is the identity (``design``).
        "identity": {"schema_version": 1, "instances": [], "selected_instance_id": None,
                     "solver_anchor_instance_id": None},
        "polar_grid_derivation": polar_grid_from_symmetry({
            "domain_planes": [], "planes": {plane: {"accepted": False} for plane in ("x0", "y0")},
        }),
        # An exterior assembly has no FEM air; say so rather than leave it unknown.
        "evidence": {"fem_regions": [], "fem_air_volumes": []},
        "mesh": {
            "stats": {"triangle_count": len(mesh.triangles), "node_count": len(mesh.points)},
            "integrity": file_mesh_integrity(mesh, []),
            "metadata": {
                "mesher_identity": mesher_identity(),
                "mesher_features": ["design-enclosure-woofers"] if assembly_meta else [],
                "build_stats": built["stats"],
                "normals": "outward, certified by the design mesher and the ready-mesh check",
                "placement": built["placement"],
                **({"assembly": {key: assembly_meta[key] for key in ("patches", "contours", "half_x", "coordinate_frame")
                                 if key in assembly_meta}} if assembly_meta else {}),
            },
        },
        "summary": {
            **mesh.summary(),
            "dimensions_mm": (np.ptp(mesh.points, axis=0) * 1000).tolist(),
        },
    }
    return record, solver_text


def _publish(parent: Path, destination: Path, solver_text: str) -> None:
    stage = publish_staging_directory(parent, ".mesh-")
    try:
        with (stage / "solve.msh").open("wb") as handle:
            handle.write(solver_text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage, destination)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _held(call: Any, *args: Any, **kwargs: Any) -> Any:
    """Run off the event loop, holding collection off as every ingest does."""

    with prepared_mesh_use():
        return call(*args, **kwargs)


def _reuse(store: Any, model_id: str, spec_digest: str) -> tuple[dict[str, Any] | None, list[str]]:
    candidates = store.find_file_ingests(model_id, spec_digest)
    for row in candidates:
        existing = json.loads(row["record_json"])
        if _intact(existing):
            store.touch_ingest(str(existing["ingest_id"]))
            return existing, []
    return None, [str(row["ingest_id"]) for row in candidates]


def _publish_record(
    store: Any, data_dir: Path, design: DesignConfig, built: Mapping[str, Any], *,
    design_uid: str, design_name: str, design_revision: int, spec_digest: str,
    model_id: str, known: list[str], fence: AssemblyBuildFence, token: tuple[str, int],
) -> dict[str, Any] | None:
    parent = data_dir / "imports" / "files"
    parent.mkdir(parents=True, exist_ok=True)
    ingest_id = mint_id("wgi_")
    created_at = utc_now()
    destination = parent / ingest_id
    model = store.get_model(model_id) or {}
    archive_stem = model.get("archive_stem") or claim_project_folder(
        store, f"model:{model_id}", design_name or "Design assembly"
    )
    record, solver_text = compose_record(
        design, built, ingest_id=ingest_id, created_at=created_at, design_uid=design_uid,
        design_name=design_name, design_revision=design_revision, spec_digest=spec_digest,
        archive_stem=archive_stem, destination=destination,
    )
    record_json = seal_prepared_record(record, producer=PRODUCER)
    _publish(parent, destination, solver_text)
    try:
        row, created = store.insert_file_ingest(
            ingest_id=ingest_id, created_at=created_at, manifest_sha256=spec_digest,
            artifact_sha256=record["artifact_sha256"], record_json=record_json,
            known_ingest_ids=known, model_id=model_id,
            commit_guard=lambda _conn: fence.current(token),
        )
    except StaleAttempt:
        shutil.rmtree(destination, ignore_errors=True)
        return None
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    if not created:
        shutil.rmtree(destination, ignore_errors=True)
    return json.loads(row["record_json"])


async def compile_design_assembly(
    store: Any, data_dir: Path, design_dump: Mapping[str, Any], *,
    design_uid: str, design_name: str, design_revision: int,
    fence: AssemblyBuildFence = FENCE, cancel_cb: Any = None,
) -> dict[str, Any]:
    """Compile and publish the design's assembly, or report it superseded.

    Returns ``{"status": "published", "record": ..., "reused": bool}`` or
    ``{"status": "superseded"}``. Raises ``AssemblyRefusal`` for a design the
    design mesher cannot build as an assembly, in words the user acts on.
    """

    import asyncio

    design = DesignConfig.model_validate(dict(design_dump))
    if design.root.assembly is None:
        raise AssemblyRefusal("This design has no Sources and baffle assembly.")
    model_id = design_model_id(design_uid)
    if not model_id:
        raise AssemblyRefusal("Save the design before solving its assembly.")
    token = fence.begin(design_uid, design_revision)
    spec_digest = _digest(compile_spec(design))
    reused, known = await asyncio.to_thread(_held, _reuse, store, model_id, spec_digest)
    if reused is not None:
        return {"status": "published", "record": reused, "reused": True}
    built = await build_assembly_mesh(design, cancel_cb)
    if not fence.current(token):
        return {"status": "superseded"}
    record = await asyncio.to_thread(
            _held, _publish_record, store, data_dir, design, built, design_uid=design_uid,
            design_name=design_name, design_revision=design_revision, spec_digest=spec_digest,
            model_id=model_id, known=known, fence=fence, token=token,
        )
    if record is None:
        return {"status": "superseded"}
    return {"status": "published", "record": record, "reused": False}


__all__ = [
    "AssemblyBuildFence",
    "AssemblySuperseded",
    "FENCE",
    "PRODUCER",
    "compile_design_assembly",
    "compile_spec",
    "compose_record",
    "geometry_spec",
]
