"""Register ready meshes for the ordinary imported-job path, without a CAD link."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException
import numpy as np
from pydantic import Field

from server.contracts import JobModel
from server.contracts.canonical import canonical_utf8_strict_json
from server.mesh.artifact import read_verified_import_mesh
from server.mesh.file_import import MAX_MESH_BYTES, mesh_text, parse_file_mesh, prepare_file_mesh
from server.mesh.records import get_prepared_mesh
from server.platform.staging import publish_staging_directory
from server.workspace.archive import archive_folder_slug


class MeshFile(JobModel):
    filename: str = Field(min_length=1, max_length=240)
    msh_text: str = Field(min_length=1, max_length=MAX_MESH_BYTES)


class MeshRegistration(MeshFile):
    units: Literal["mm", "cm", "m", "in"]
    forward: Literal["+x", "-x", "+y", "-y", "+z", "-z"]
    up: Literal["+x", "-x", "+y", "-y", "+z", "-z"]
    source_tags: list[int] = Field(min_length=1, max_length=128)


def register_mesh(store: Any, data_dir: Path, body: MeshRegistration) -> dict[str, Any]:
    original = parse_file_mesh(body.msh_text)
    mesh, transform = prepare_file_mesh(original, body.units, body.forward, body.up)
    if len(set(body.source_tags)) != len(body.source_tags) or set(body.source_tags) - set(
        mesh.tags.tolist()
    ):
        raise ValueError("Select unique driven groups that exist in this mesh.")
    filename = body.filename.replace("\\", "/").rsplit("/", 1)[-1]
    source_bytes = body.msh_text.encode("utf-8")
    solver_text = mesh_text(mesh)
    digest = "sha256:" + hashlib.sha256(solver_text.encode()).hexdigest()
    original_digest = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    spec = {
        "original_sha256": original_digest,
        "transform": transform,
        "source_tags": sorted(body.source_tags),
    }
    spec_digest = "sha256:" + hashlib.sha256(canonical_utf8_strict_json(spec).encode()).hexdigest()
    parent = data_dir / "imports" / "files"
    parent.mkdir(parents=True, exist_ok=True)
    corners = mesh.points[mesh.triangles]
    areas_mm2 = (
        np.linalg.norm(
            np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1
        )
        * 0.5e6
    )
    sources = [
        {
            "id": f"group_{tag}",
            "role": "SOURCE",
            "required": True,
            "name": mesh.names.get(tag, f"Group {tag}"),
            "observed": {"total_area_mm2": float(areas_mm2[mesh.tags == tag].sum())},
        }
        for tag in sorted(body.source_tags)
    ]

    def publish(ingest_id: str, created_at: str) -> str:
        destination = parent / ingest_id
        stage = publish_staging_directory(parent, ".mesh-")
        try:
            for name, raw in (("original.msh", source_bytes), ("solve.msh", solver_text.encode())):
                with (stage / name).open("wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
            os.replace(stage, destination)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        record = {
            "ingest_id": ingest_id,
            "created_at": created_at,
            "producer": "mesh-file",
            "manifest_sha256": spec_digest,
            "artifact_sha256": original_digest,
            "mesh_content_sha256": digest,
            "mesh_size_bytes": len(solver_text.encode()),
            "mesh_store_path": str(destination / "solve.msh"),
            "source_file": {"filename": filename, **spec},
            "archive_stem": f"{archive_folder_slug(Path(filename).stem)}_{ingest_id[-10:]}",
            "document": {"name": filename},
            "sources": sources,
            "source_tags": {
                source["id"]: tag for source, tag in zip(sources, sorted(body.source_tags))
            },
            "findings": [],
            "skipped_source_ids": [],
            "acoustic_domain": "free-space",
            "normalisation": {"assembly_frame_is_solver_frame": True},
            "symmetry": {"domain_planes": [], "cut_planes": []},
            "polar_grid_derivation": {
                "axes": {
                    axis: {"minimum_deg": -180, "maximum_deg": 180}
                    for axis in ("horizontal", "vertical", "diagonal")
                }
            },
            "evidence": {"fem_regions": [], "fem_air_volumes": []},
            "mesh": {
                "stats": {"triangle_count": len(mesh.triangles), "node_count": len(mesh.points)},
                "metadata": {},
            },
            "summary": {
                **mesh.summary(),
                "dimensions_mm": (np.ptp(mesh.points, axis=0) * 1000).tolist(),
            },
        }
        return json.dumps(record, allow_nan=False)

    row = store.allocate_ingest(
        manifest_sha256=spec_digest, artifact_sha256=original_digest, record_builder=publish
    )
    return json.loads(row["record_json"])


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record[key]
        for key in (
            "ingest_id",
            "producer",
            "created_at",
            "manifest_sha256",
            "artifact_sha256",
            "source_file",
            "source_tags",
            "sources",
            "summary",
        )
    }


def mount_mesh_import(application: FastAPI) -> None:
    router = APIRouter(prefix="/api/mesh-import", tags=["Mesh import"])
    # The existing ingestion table stores prepared meshes from both producers.
    # No CAD workspace, document, connection or delivery operation is involved.
    store = application.state.cadlink_store
    data_dir = Path(application.state.data_dir)

    @router.post("/inspect")
    async def inspect(body: MeshFile) -> dict[str, Any]:
        try:
            mesh = await asyncio.to_thread(parse_file_mesh, body.msh_text, require_groups=False)
            return mesh.summary()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("")
    async def register(body: MeshRegistration) -> dict[str, Any]:
        if application.state.jobs_runtime._restart_pending():
            raise HTTPException(409, "An update restart is pending. Import again after restart.")
        try:
            record = await asyncio.to_thread(register_mesh, store, data_dir, body)
            return {
                **_public_record(record),
                "msh_text": await asyncio.to_thread(read_verified_import_mesh, record),
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/{ingest_id}")
    async def read(ingest_id: str) -> dict[str, Any]:
        try:
            record = await asyncio.to_thread(get_prepared_mesh, store, ingest_id)
            if record is None or record.get("producer") != "mesh-file":
                raise HTTPException(404, "Imported mesh not found.")
            return {
                **_public_record(record),
                "msh_text": await asyncio.to_thread(read_verified_import_mesh, record),
            }
        except (OSError, ValueError) as exc:
            raise HTTPException(409, f"Retained mesh unavailable: {exc}. Import it again.") from exc

    application.include_router(router)
