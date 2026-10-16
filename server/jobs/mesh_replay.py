"""Portable file-mesh inputs, reconstructed through ordinary mesh registration.

The package is data, never a serialized ingestion row. No path, frame approval,
CAD identity or producer declaration can cross this boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import threading
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import Field, JsonValue

from server.contracts import JobModel
from server.contracts.canonical import canonical_utf8_strict_json
from server.jobs.mesh_import import MeshRegistration, _public_record, register_mesh
from server.jobs.models import ImportedGeometrySource, SolveRequest
from server.jobs.runtime import _solved_under_legacy_axial
from server.mesh.artifact import read_verified_import_mesh
from server.mesh.file_import import MAX_MESH_BYTES, mesh_text, parse_file_mesh, prepare_file_mesh
from server.mesh.records import get_prepared_mesh
from server.solver.imported import AXIAL_CONTRACT_VERSION

MAX_REPLAY_BYTES = 64 * 1024 * 1024
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class MeshReplayInputs(JobModel):
    schema_version: Literal[1]
    kind: Literal["wg-file-mesh-replay"]
    numerical_contract: Literal["imported-free-space-v1"]
    axial_contract: str
    registration: MeshRegistration
    original_sha256: Digest
    solver_msh_text: str = Field(min_length=1, max_length=32 * 1024 * 1024)
    solver_sha256: Digest
    request: SolveRequest
    # Historical provenance is informational, never a capability or approval.
    result_provenance: dict[str, JsonValue] = Field(default_factory=dict)


class MeshReplayPackage(MeshReplayInputs):
    content_sha256: Digest


def _inputs(package: MeshReplayInputs) -> dict[str, Any]:
    return package.model_dump(mode="json", exclude={"content_sha256"})


def _validate_file_request(request: SolveRequest) -> None:
    geometry = request.geometry
    if not isinstance(geometry, ImportedGeometrySource):
        raise ValueError("Mesh replay requires an imported file mesh.")
    if geometry.required_features or geometry.mesh is not None or geometry.acknowledged_findings:
        raise ValueError("CAD/native mesh replay needs a typed CAD package; export its retained CAD inputs instead.")
    if request.options.engine in {"auto", "beat"}:
        raise ValueError("Mesh replay requires the run's resolved engine, not Auto.")
    if (request.options.solver_mode not in {"auto", "full_3d"}
            or request.options.symmetry not in {"auto", "full"}
            or request.options.mesh_ladder != "off"):
        raise ValueError("Mesh replay v1 supports full-domain surface meshes without a mesh ladder.")


def build_mesh_replay(runtime: Any, store: Any, job_id: str) -> dict[str, Any]:
    snapshot = runtime.store.get_archive_snapshot(job_id)
    if snapshot is None:
        raise LookupError("Run not found.")
    job = snapshot["job"]
    if job["status"] != "complete":
        raise ValueError("Complete the run before exporting its mesh replay package.")
    request = SolveRequest.model_validate(job["config_json"])
    _validate_file_request(request)
    record = get_prepared_mesh(store, request.geometry.ingest_id)
    if record is None or record.get("producer") != "mesh-file":
        raise ValueError("Replay v1 supports standalone mesh files. CAD models need their retained CAD inputs.")
    if (request.geometry.manifest_sha256 != record["manifest_sha256"]
            or request.geometry.artifact_sha256 != record["artifact_sha256"]):
        raise ValueError("The retained import does not match this run's admitted hashes.")
    axial_ids = [channel.id for channel in request.geometry.drive_channels
                 if channel.motion == "axial" and channel.exterior_transducer is None]
    if axial_ids and (not snapshot["results_text"] or _solved_under_legacy_axial(snapshot["results_text"], axial_ids)):
        raise ValueError("This run has no compatible per-source axial result contract. Start a new solve before exporting replay.")
    solver_text = read_verified_import_mesh(record)
    if snapshot["mesh_text"] is not None and snapshot["mesh_text"] != solver_text:
        raise ValueError("The run's exact solver mesh is unavailable. Reimport and solve the original mesh.")
    original_path = Path(record["mesh_store_path"]).parent / "original.msh"
    with original_path.open("rb") as handle:
        original = handle.read(MAX_MESH_BYTES + 1)
    if len(original) > MAX_MESH_BYTES:
        raise ValueError("The retained original mesh exceeds the mesh import limit.")
    original_text = original.decode("utf-8")
    source = record["source_file"]
    if _digest(original_text) != source["original_sha256"]:
        raise ValueError("The retained original mesh changed. Reimport and solve it before exporting replay.")
    mesh, _ = prepare_file_mesh(parse_file_mesh(original_text), source["transform"]["units"],
                                source["transform"]["forward"], source["transform"]["up"])
    if mesh_text(mesh) != solver_text:
        raise ValueError("The original mesh no longer reconstructs the run's solver mesh.")
    # Remove local job ancestry and a spent submission key, keeping the entire
    # resolved geometry/options rather than a lossy display projection.
    request.parent_job_id = None
    request.client_request_id = None
    results = json.loads(snapshot["results_text"]) if snapshot["results_text"] else {}
    inputs = MeshReplayInputs(
        schema_version=1, kind="wg-file-mesh-replay", numerical_contract="imported-free-space-v1",
        axial_contract=AXIAL_CONTRACT_VERSION,
        registration=MeshRegistration(
            filename=source["filename"], msh_text=original_text,
            units=source["transform"]["units"], forward=source["transform"]["forward"],
            up=source["transform"]["up"], source_tags=source["source_tags"],
        ),
        original_sha256=_digest(original_text), solver_msh_text=solver_text,
        solver_sha256=_digest(solver_text), request=request,
        result_provenance=results.get("provenance") or {},
    )
    payload = _inputs(inputs)
    content = canonical_utf8_strict_json(payload)
    if len(content.encode("utf-8")) + 100 > MAX_REPLAY_BYTES:
        raise ValueError("The mesh replay package exceeds the 64 MiB limit.")
    return {**payload, "content_sha256": _digest(content)}


def restore_mesh_replay(
    runtime: Any, store: Any, data_dir: Path, package: MeshReplayPackage, lock: Any,
) -> dict[str, Any]:
    content = canonical_utf8_strict_json(_inputs(package))
    if len(content.encode("utf-8")) + 100 > MAX_REPLAY_BYTES:
        raise ValueError("The mesh replay package exceeds the 64 MiB limit.")
    if _digest(content) != package.content_sha256:
        raise ValueError("Mesh replay checksum mismatch. Open an unchanged exported package.")
    if package.axial_contract != AXIAL_CONTRACT_VERSION:
        raise ValueError("This replay uses a different source-motion contract. Open it with a compatible WG version.")
    _validate_file_request(package.request)
    body = package.registration
    if _digest(body.msh_text) != package.original_sha256:
        raise ValueError("The original mesh checksum does not match the replay package.")
    mesh, _ = prepare_file_mesh(parse_file_mesh(body.msh_text), body.units, body.forward, body.up)
    # Reconstruct, do not trust a packaged solver mesh or a transform matrix.
    reconstructed = mesh_text(mesh)
    if (_digest(package.solver_msh_text) != package.solver_sha256
            or reconstructed != package.solver_msh_text):
        raise ValueError("The solver mesh does not match the original mesh and declared transform.")
    def validate_new_record(candidate: dict[str, Any]) -> None:
        runtime._validate_imported_model_setup(package.request.geometry, candidate)
        # The admitted hashes must describe the packaged original and transform.
        if (package.request.geometry.manifest_sha256 != candidate["manifest_sha256"]
                or package.request.geometry.artifact_sha256 != candidate["artifact_sha256"]):
            raise ValueError("The request hashes do not match the packaged mesh and assignments.")
        candidate["mesh_replay_sha256"] = package.content_sha256

    # One backend owns this data directory. Serialize lookup/register/publish
    # so a lost response or simultaneous open reuses the same retained mesh.
    with lock:
        row = store.get_mesh_replay_ingest(package.content_sha256)
        record = get_prepared_mesh(store, row["ingest_id"]) if row else None
        if record is not None:
            if (record.get("producer") != "mesh-file"
                    or read_verified_import_mesh(record) != reconstructed):
                raise ValueError("The retained replay mesh changed. Import the original mesh again.")
        else:
            record = register_mesh(
                store, data_dir, body,
                validate_record=validate_new_record,
            )
        request = package.request.model_copy(deep=True)
        request.geometry.ingest_id = record["ingest_id"]
        request.geometry.manifest_sha256 = record["manifest_sha256"]
        request.geometry.artifact_sha256 = record["artifact_sha256"]
        request.parent_job_id = None
        request.client_request_id = None
        runtime._validate_imported_model_setup(request.geometry, record)
        return {
            "record": {**_public_record(record), "msh_text": reconstructed},
            "request": request.model_dump(mode="json"),
        }


def mount_mesh_replay(application: FastAPI) -> None:
    router = APIRouter(tags=["Mesh replay"])
    runtime = application.state.jobs_runtime
    store = application.state.cadlink_store
    lock = threading.Lock()

    @router.get("/api/jobs/{job_id}/mesh-replay")
    async def export_package(job_id: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(build_mesh_replay, runtime, store, job_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (OSError, UnicodeError, ValueError) as exc:
            raise HTTPException(409, f"Mesh replay unavailable: {exc}") from exc

    @router.post("/api/mesh-replay")
    async def open_package(body: MeshReplayPackage) -> dict[str, Any]:
        if runtime._restart_pending():
            raise HTTPException(409, "An update restart is pending. Open replay after restart.")
        try:
            return await asyncio.to_thread(
                restore_mesh_replay, runtime, store, Path(application.state.data_dir), body, lock,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise HTTPException(422, f"Mesh replay refused: {exc}") from exc

    application.include_router(router)
