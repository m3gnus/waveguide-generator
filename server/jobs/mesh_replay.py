"""Portable solve inputs reconstructed through ordinary registration/ingestion.

Packages carry original evidence, never trusted ingestion rows or approvals.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import threading
import tempfile
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import Field, JsonValue

from server.contracts import JobModel
from server.contracts.canonical import canonical_utf8_strict_json
from server.jobs.models_api import MeshRegistration, begin_file_preparation, prepare_registration, public_record, register_mesh
from server.cadlink.store import StaleAttempt, file_model_id
from server.cadlink.intake.ingest import ingest_bundle, read_snapshot
from server.cadlink.intake.readings import ensure_frame_suggestion, decide_record_frame, store_frame_decision, record_frame_decision
from server.cadlink.wgreturn import _parse_manifest, _portable_member_name, validate_manifest
from server.cadlink.intake.isolation import ChildRefusal
from server.jobs.models import ImportedGeometrySource, SolveRequest
from server.jobs.runtime.core import _solved_under_legacy_axial
from server.jobs.runtime.cad_lane import _imported_symmetry_metadata
from server.mesh.artifact import read_verified_import_mesh
from server.mesh.file_import import FileMesh, MAX_MESH_BYTES, mesh_text, parse_file_mesh, prepare_file_mesh
from server.jobs.request_cancellation import ClientDisconnected, RequestCancellation
from server.mesh.preparation_fence import PreparationSuperseded
from server.mesh.child import MesherChildError, MesherCrashError
from server.mesh.prepared_gc import prepared_mesh_use
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


class CadReplayPreparation(JobModel):
    symmetry_mode: Literal["auto", "full", "half", "quarter"] = "auto"
    surface_deviation_mm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    solver_frame: Literal["+x", "-x", "+y", "-y", "+z", "-z"]


class CadReplayInterpretation(JobModel):
    # Only solve interpretation, never storage, provenance approvals or a row.
    # Every field is re-derived by ingestion and compared, not adopted.
    normalisation: dict[str, JsonValue]
    symmetry: dict[str, JsonValue]
    domain_interpretation: dict[str, JsonValue]
    domain_decision: dict[str, JsonValue]
    sources: list[dict[str, JsonValue]]
    source_tags: dict[str, int]
    tag_map: dict[str, JsonValue]
    identity: dict[str, JsonValue]
    anchor: dict[str, JsonValue]
    polar_grid_derivation: dict[str, JsonValue]
    role_resolution: dict[str, JsonValue]
    post_cut_source_areas: dict[str, JsonValue]

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> CadReplayInterpretation:
        if set(cls.model_fields) - record.keys():
            raise ValueError("This CAD run has no complete retained interpretation. Prepare and solve it again before exporting replay.")
        return cls.model_validate({key: record[key] for key in cls.model_fields})


class CadMeshReplayPackage(JobModel):
    schema_version: Literal[1]
    kind: Literal["wg-cad-mesh-replay"]
    numerical_contract: Literal["imported-free-space-v1"]
    axial_contract: str
    # Original evidence, not an ingestion row or a frame approval.
    manifest_text: str = Field(min_length=1, max_length=1024 * 1024)
    members_base64: dict[str, str]
    preparation: CadReplayPreparation
    interpretation: CadReplayInterpretation
    solver_msh_text: str = Field(min_length=1, max_length=32 * 1024 * 1024)
    solver_sha256: Digest
    solve_model_sha256: Digest
    request: SolveRequest
    content_sha256: Digest


ReplayPackage = Annotated[MeshReplayPackage | CadMeshReplayPackage, Field(discriminator="kind")]


def _package_inputs(package: MeshReplayInputs | CadMeshReplayPackage) -> dict[str, Any]:
    if isinstance(package, CadMeshReplayPackage):
        return package.model_dump(mode="json", exclude={"content_sha256"})
    return _inputs(package)


def _validate_cad_request(request: SolveRequest, record: dict[str, Any] | None = None) -> None:
    geometry = request.geometry
    if record is not None:
        producer = record.get("producer") or "cad"
        if producer != "cad" or record.get("native_source"):
            raise ValueError(f"Replay for {producer} models is not available yet. Keep this run's retained model in WG.")
    if not isinstance(geometry, ImportedGeometrySource):
        raise ValueError("Mesh replay requires an imported file mesh or CAD surface.")
    if geometry.required_features:
        raise ValueError("Native source replay is not available yet. Keep this run's retained model in WG.")
    if geometry.mesh is None:
        raise ValueError("CAD replay needs its recorded mesh sizes. Prepare and solve the CAD model again.")
    if geometry.acknowledged_findings or geometry.skipped_source_ids:
        raise ValueError("Replay cannot carry finding approvals or skipped CAD sources yet. Resolve the findings and solve again.")
    if request.options.engine in {"auto", "beat"}:
        raise ValueError("Mesh replay requires the run's resolved engine, not Auto.")
    if request.options.solver_mode not in {"auto", "full_3d"} or request.options.mesh_ladder != "off":
        raise ValueError("FEM/coupled or mesh-ladder replay is not available yet. Keep the retained model in WG.")
    if record is not None:
        if record.get("fem_regions") or record.get("coupled_exterior") or (record.get("evidence") or {}).get("fem_air_volumes"):
            raise ValueError("FEM/coupled replay is not available yet. Keep this run's retained model in WG.")
        if any(item.get("blocking") for item in record.get("findings", [])):
            raise ValueError("Replay cannot carry CAD finding approvals yet. Resolve the findings and solve again.")
        _imported_symmetry_metadata(record, requested=request.options.symmetry)


def _cad_manifest(manifest_text: str) -> dict[str, Any]:
    manifest = validate_manifest(_parse_manifest(manifest_text.encode("utf-8")))
    if manifest["instances"]:
        raise ValueError("Linked CAD replay needs design history that this package cannot carry yet. Keep the retained model in WG.")
    scope = manifest["scope"]
    if scope.get("fem_regions") or scope.get("fem_air_volumes"):
        raise ValueError("FEM/coupled replay is not available yet. Keep the retained model in WG.")
    return manifest


def _cad_preparation(record: dict[str, Any]) -> CadReplayPreparation:
    frame = (record.get("normalisation") or {}).get("solver_frame") or {}
    preparation = record.get("preparation_options")
    if not preparation or not frame.get("axis"):
        raise ValueError("This CAD run predates retained replay settings. Prepare and solve the model again before exporting replay.")
    if preparation.get("area_drift_overrides") or record.get("mesh_limit_override"):
        raise ValueError("Replay cannot carry preparation overrides yet. Prepare and solve without overrides before exporting replay.")
    interpretation = record.get("domain_interpretation") or {}
    if (interpretation.get("evidence") or {}).get("source") in {"user", "user-lineage", "lineage"}:
        raise ValueError("This model uses a retained domain reading. Send it with its domain declared in CAD, then solve and export again.")
    return CadReplayPreparation(symmetry_mode=preparation["symmetry_mode"],
        surface_deviation_mm=preparation.get("surface_deviation_mm"), solver_frame=frame["axis"])


def _build_cad_package(request: SolveRequest, record: dict[str, Any], solver_text: str, store: Any) -> dict[str, Any]:
    _validate_cad_request(request, record)
    try:
        bundle = read_snapshot(record.get("bundle_store_path") or "", retained=True)
    except (OSError, ValueError) as exc:
        raise ValueError("The retained CAD originals are unavailable or changed. Send the model again and solve before exporting replay.") from exc
    if bundle.manifest_sha256 != request.geometry.manifest_sha256 or bundle.artifact_sha256 != request.geometry.artifact_sha256:
        raise ValueError("The retained CAD originals do not match this run. Send the model again and solve before exporting replay.")
    manifest_text = bundle.manifest_path.read_bytes().decode("utf-8")
    _cad_manifest(manifest_text)
    if record_frame_decision(store, record) is None:
        raise ValueError("Replay cannot carry a manual or unrecorded solver-frame choice yet. Keep this run's retained model in WG.")
    if _digest(manifest_text) != bundle.manifest_sha256:
        raise ValueError("The retained CAD manifest changed during export. Export the replay again.")
    members = {}
    for name, path in bundle.members.items():
        declared = bundle.manifest["files"][name]
        if declared.get("purpose") == "cad-document":
            continue
        raw = path.read_bytes()
        if len(raw) != declared["size_bytes"] or "sha256:" + hashlib.sha256(raw).hexdigest() != declared["sha256"]:
            raise ValueError("The retained CAD originals changed during export. Send the model again and solve before exporting replay.")
        members[name] = base64.b64encode(raw).decode("ascii")
    preparation = _cad_preparation(record)
    if not record.get("solve_model_sha256"):
        raise ValueError("This CAD run predates retained interpretation identity. Prepare and solve it again before exporting replay.")
    request.parent_job_id = None
    request.client_request_id = None
    package = CadMeshReplayPackage(
        schema_version=1, kind="wg-cad-mesh-replay", numerical_contract="imported-free-space-v1",
        axial_contract=AXIAL_CONTRACT_VERSION, manifest_text=manifest_text,
        members_base64=members,
        preparation=preparation, interpretation=CadReplayInterpretation.from_record(record),
        solver_msh_text=solver_text, solver_sha256=_digest(solver_text),
        solve_model_sha256=record["solve_model_sha256"], request=request,
        content_sha256="sha256:" + "0" * 64,
    )
    return _seal_package(package)


def _inputs(package: MeshReplayInputs) -> dict[str, Any]:
    payload = package.model_dump(mode="json", exclude={"content_sha256"})
    # New optional preparation fields must not alter historical package checksums.
    if payload["registration"]["roles"] is None:
        payload["registration"].pop("roles")
    for field, default in (("domain_planes", []), ("reflect_planes", None), ("remove_lid_planes", []), ("auto_cut", False)):
        if payload["registration"][field] == default:
            payload["registration"].pop(field)
    return payload


def _validate_file_request(request: SolveRequest, record: dict[str, Any] | None = None) -> None:
    geometry = request.geometry
    if not isinstance(geometry, ImportedGeometrySource):
        raise ValueError("Mesh replay requires an imported file mesh.")
    if geometry.required_features or geometry.mesh is not None or geometry.acknowledged_findings:
        raise ValueError("CAD/native mesh replay needs a typed CAD package; export its retained CAD inputs instead.")
    if request.options.engine in {"auto", "beat"}:
        raise ValueError("Mesh replay requires the run's resolved engine, not Auto.")
    if (request.options.solver_mode not in {"auto", "full_3d"}
            or request.options.mesh_ladder != "off"):
        raise ValueError("Mesh replay supports surface meshes without a mesh ladder.")
    if record is not None:
        _imported_symmetry_metadata(record, requested=request.options.symmetry)


def build_mesh_replay(runtime: Any, store: Any, job_id: str, *, prepared: tuple[FileMesh, dict[str, Any]] | None = None, registration_only: bool = False) -> Any:
    snapshot = runtime.store.get_archive_snapshot(job_id)
    if snapshot is None:
        raise LookupError("Run not found.")
    job = snapshot["job"]
    if job["status"] != "complete":
        raise ValueError("Complete the run before exporting its mesh replay package.")
    request = SolveRequest.model_validate(job["config_json"])
    if not isinstance(request.geometry, ImportedGeometrySource):
        raise ValueError("Mesh replay requires an imported file mesh or CAD surface.")
    record = get_prepared_mesh(store, request.geometry.ingest_id)
    if record is None:
        raise ValueError("This run's retained inputs were removed. Reimport the model and solve before exporting replay.")
    file_mesh = record.get("producer") == "mesh-file"
    (_validate_file_request if file_mesh else _validate_cad_request)(request, record)
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
    if not file_mesh:
        return _build_cad_package(request, record, solver_text, store)
    original_path = Path(record["mesh_store_path"]).parent / "original.msh"
    with original_path.open("rb") as handle:
        original = handle.read(MAX_MESH_BYTES + 1)
    if len(original) > MAX_MESH_BYTES:
        raise ValueError("The retained original mesh exceeds the mesh import limit.")
    original_text = original.decode("utf-8")
    source = record["source_file"]
    if _digest(original_text) != source["original_sha256"]:
        raise ValueError("The retained original mesh changed. Reimport and solve it before exporting replay.")
    registration = MeshRegistration(
        filename=source["filename"], msh_text=original_text,
        units=source["transform"]["units"], forward=source["transform"]["forward"],
        up=source["transform"]["up"], source_tags=source["source_tags"],
        roles=source.get("roles"), auto_cut=source.get("auto_cut", False),
        domain_planes=source.get("domain_planes", []), reflect_planes=source.get("reflect_planes"),
        remove_lid_planes=source.get("remove_lid_planes", []),
    )
    if registration_only:
        return registration
    mesh, _ = prepared if prepared is not None else prepare_file_mesh(parse_file_mesh(original_text), source["transform"]["units"],
                                source["transform"]["forward"], source["transform"]["up"],
                                domain_planes=source.get("domain_planes", []), reflect_planes=source.get("reflect_planes"),
                                remove_lid_planes=source.get("remove_lid_planes", []), roles=registration.driven_roles(), auto_cut=registration.auto_cut,
                                historical_reconstruction=True)
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
        registration=registration,
        original_sha256=_digest(original_text), solver_msh_text=solver_text,
        solver_sha256=_digest(solver_text), request=request,
        result_provenance=results.get("provenance") or {},
    )
    return _seal_package(inputs)


def _seal_package(inputs: MeshReplayInputs | CadMeshReplayPackage) -> dict[str, Any]:
    payload = _package_inputs(inputs)
    content = canonical_utf8_strict_json(payload)
    if len(content.encode("utf-8")) + 100 > MAX_REPLAY_BYTES:
        raise ValueError("The mesh replay package exceeds the 64 MiB limit.")
    return {**payload, "content_sha256": _digest(content)}


def _checked_registration(package: MeshReplayPackage | CadMeshReplayPackage) -> MeshRegistration | None:
    content = canonical_utf8_strict_json(_package_inputs(package))
    if len(content.encode("utf-8")) + 100 > MAX_REPLAY_BYTES:
        raise ValueError("The mesh replay package exceeds the 64 MiB limit.")
    if _digest(content) != package.content_sha256:
        raise ValueError("Mesh replay checksum mismatch. Open an unchanged exported package.")
    if package.axial_contract != AXIAL_CONTRACT_VERSION:
        raise ValueError("This replay uses a different source-motion contract. Open it with a compatible WG version.")
    if isinstance(package, CadMeshReplayPackage):
        _validate_cad_request(package.request)
        _cad_manifest(package.manifest_text)
        if _digest(package.solver_msh_text) != package.solver_sha256:
            raise ValueError("The solver mesh checksum does not match the replay package.")
        return None
    _validate_file_request(package.request)
    body = package.registration
    if _digest(body.msh_text) != package.original_sha256:
        raise ValueError("The original mesh checksum does not match the replay package.")
    return body


def restore_mesh_replay(
    runtime: Any, store: Any, data_dir: Path, package: MeshReplayPackage | CadMeshReplayPackage, lock: Any, *,
    prepared: tuple[FileMesh, dict[str, Any]] | None = None, commit_guard: Any = None,
) -> dict[str, Any]:
    body = _checked_registration(package)
    if isinstance(package, CadMeshReplayPackage):
        return _restore_cad_mesh(runtime, store, data_dir, package, lock, commit_guard=commit_guard)
    assert body is not None
    mesh, transform = prepared if prepared is not None else prepare_file_mesh(parse_file_mesh(body.msh_text), body.units, body.forward, body.up,
                                domain_planes=body.domain_planes, reflect_planes=body.reflect_planes,
                                remove_lid_planes=body.remove_lid_planes, roles=body.driven_roles(), auto_cut=body.auto_cut,
                                historical_reconstruction=True)
    # Reconstruct, do not trust a packaged solver mesh or a transform matrix.
    reconstructed = mesh_text(mesh)
    if (_digest(package.solver_msh_text) != package.solver_sha256
            or reconstructed != package.solver_msh_text):
        raise ValueError("The solver mesh does not match the original mesh and declared transform.")

    def validate_record(candidate: dict[str, Any]) -> None:
        _validate_file_request(package.request, candidate)
        runtime._validate_imported_model_setup(package.request.geometry, candidate)
        # The admitted hashes must describe the packaged original and transform.
        if (package.request.geometry.manifest_sha256 != candidate["manifest_sha256"]
                or package.request.geometry.artifact_sha256 != candidate["artifact_sha256"]):
            raise ValueError("The request hashes do not match the packaged mesh and assignments.")

    # The same original prepared the same way is one retained mesh, so a lost
    # response or simultaneous open reuses it (models_api.register_mesh). One
    # backend owns this data directory; the lock keeps the two opens in order.
    # A replay in progress holds off prepared-mesh collection until it answers.
    with lock, prepared_mesh_use():
        record = register_mesh(store, data_dir, body, validate_record=validate_record, prepared=(mesh, transform), commit_guard=commit_guard)
        request = package.request.model_copy(deep=True)
        request.geometry.ingest_id = record["ingest_id"]
        request.geometry.manifest_sha256 = record["manifest_sha256"]
        request.geometry.artifact_sha256 = record["artifact_sha256"]
        request.parent_job_id = None
        request.client_request_id = None
        runtime._validate_imported_model_setup(request.geometry, record)
        return {
            "record": {
                **public_record(record, file_model_id(record["artifact_sha256"])),
                "msh_text": reconstructed,
            },
            "request": request.model_dump(mode="json"),
        }


def _restore_cad_mesh(runtime: Any, store: Any, data_dir: Path, package: CadMeshReplayPackage,
                            lock: Any, *, commit_guard: Any = None) -> dict[str, Any]:
    manifest = _cad_manifest(package.manifest_text)

    def validate_record(record: dict[str, Any]) -> None:
        _validate_cad_request(package.request, record)
        runtime._validate_imported_model_setup(package.request.geometry, record)
        from server.mesh.prepared_record import solve_model_sha256
        if (record["manifest_sha256"] != package.request.geometry.manifest_sha256
                or record["artifact_sha256"] != package.request.geometry.artifact_sha256
                or _cad_preparation(record) != package.preparation
                or CadReplayInterpretation.from_record(record) != package.interpretation
                or solve_model_sha256(record) != package.solve_model_sha256
                or read_verified_import_mesh(record) != package.solver_msh_text):
            raise ValueError("This WG version cannot reconstruct the CAD run's exact mesh and interpretation. Open it with the exporting version, or send the model again and start a new solve.")

    # Use the same strict bundle reader and ingest writer as a CAD delivery.
    # No packaged record, cache sidecar, matrix or approval is adopted.
    with lock, prepared_mesh_use(), tempfile.TemporaryDirectory(prefix="wg-replay-") as temporary:
        root = Path(temporary) / "replay.wgreturn"
        root.mkdir()
        (root / "wgreturn.json").write_bytes(package.manifest_text.encode("utf-8"))
        expected = {name for name, member in manifest["files"].items() if member.get("purpose") != "cad-document"}
        if set(package.members_base64) != expected:
            raise ValueError("The replay package has missing or extra CAD originals. Open an unchanged exported package.")
        for name, encoded in package.members_base64.items():
            normalized, _ = _portable_member_name(name, "replay member")
            target = root / normalized
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(encoded, validate=True))
        bundle = read_snapshot(root, retained=True)
        if bundle.manifest_sha256 != package.request.geometry.manifest_sha256:
            raise ValueError("The CAD manifest checksum does not match the recorded request.")
        record = ingest_bundle(root, package.request.geometry.mesh.model_dump(mode="json"), [], store, data_dir,
            prep_options=package.preparation.model_dump(mode="json"), retained_copy=True, defer_viewport=True,
            validate_record=validate_record, reuse_existing=True, commit_guard=commit_guard)
        suggestion = ensure_frame_suggestion(store, record)
        choice = decide_record_frame(store, record, suggestion)
        if choice is not None:
            if choice.axis != package.preparation.solver_frame:
                raise ValueError("This WG version chooses another automatic solver frame for this model. Open with the exporting version, or prepare the model and start a new solve.")
            store_frame_decision(store, record, choice, suggestion)
        request = package.request.model_copy(deep=True)
        request.geometry.ingest_id = record["ingest_id"]
        request.parent_job_id = None
        request.client_request_id = None
        store.touch_ingest(record["ingest_id"])
        # CAD's existing rail consumes its ordinary public ingestion record.
        return {"record": {**record, "producer": "cad", "model_id": store.get_ingest(record["ingest_id"])["model_id"],
                           "msh_text": package.solver_msh_text},
                "request": request.model_dump(mode="json")}


def mount_mesh_replay(application: FastAPI) -> None:
    router = APIRouter(tags=["Mesh replay"])
    runtime = application.state.jobs_runtime
    store = application.state.cadlink_store
    lock = threading.Lock()

    @router.get("/api/jobs/{job_id}/mesh-replay")
    async def export_package(job_id: str) -> dict[str, Any]:
        try:
            registration = await asyncio.to_thread(build_mesh_replay, runtime, store, job_id, registration_only=True)
            if isinstance(registration, dict):
                return registration
            prepared = await prepare_registration(registration, historical_reconstruction=True)
            return await asyncio.to_thread(build_mesh_replay, runtime, store, job_id, prepared=prepared)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (OSError, UnicodeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/mesh-replay")
    async def open_package(body: ReplayPackage, http_request: Request) -> dict[str, Any]:
        if runtime._restart_pending():
            raise HTTPException(409, "WG is about to restart for an update. Open this run after the restart.")
        cancellation = RequestCancellation(http_request.is_disconnected)
        try:
            registration = await asyncio.to_thread(_checked_registration, body)
            guard = begin_file_preparation(application, file_model_id(body.original_sha256 if isinstance(body, MeshReplayPackage) else body.solver_sha256), http_request, body.content_sha256)
            cancellation.commit_guard = guard
            prepared = await prepare_registration(registration, cancellation.check, historical_reconstruction=True) if registration is not None else None
            cancellation.check()
            restored = await asyncio.to_thread(
                restore_mesh_replay, runtime, store, Path(application.state.data_dir), body, lock,
                prepared=prepared, commit_guard=cancellation.current,
            )
            cancellation.check()
            if isinstance(body, CadMeshReplayPackage):
                # Read-only ordinary geometry admission, with no job allocation.
                await runtime._validate_imported_submission(SolveRequest.model_validate(restored["request"]))
            return restored
        except ClientDisconnected as exc:
            raise HTTPException(499, "Opening this mesh was cancelled. Select the run again.") from exc
        except (StaleAttempt, PreparationSuperseded) as exc:
            raise HTTPException(409, "A newer mesh is already on screen.") from exc
        except (MesherChildError, MesherCrashError, ChildRefusal) as exc:
            raise HTTPException(422, str(exc)) from exc
        except (OSError, UnicodeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            cancellation.close()

    application.include_router(router)
