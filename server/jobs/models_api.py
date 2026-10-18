"""Imported models: the project an imported file or CAD document is.

A model is the identity setups are saved under and runs are filed by. A file's
model is the exact bytes of its original (``mesh-file:sha256:<artifact>``), so
importing the same file again, or preparing it again with other units, axes or
sources, stays the same model and finds its saved setup. A CAD model is its
lineage. Every prepared mesh (an ``ingests`` row) names its model; the server
derives which saved setup applies from the prepared mesh's source inventory.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import JSONResponse
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, model_validator

from server.cadlink.identity import mint_id, utc_now
from server.cadlink.operations import REASON_UPDATE_RESTART_PENDING
from server.cadlink.roles import inventory_sha256
from server.cadlink.store import FILE_MODEL_PREFIX, UNFILED_MODEL_ID, file_model_id
from server.contracts import JobModel
from server.contracts.canonical import canonical_utf8_strict_json
from server.integration.contracts import ErrorEnvelope, error_envelope
from server.jobs.imported_setup import (
    ImportedSolveSetup,
    setup_content,
    setup_digest,
    solve_request_for,
    validate_setup,
)
from server.jobs.model_solve import ModelSolveRefusal, ModelSolveRequest, solve_model
from server.jobs.models import SolveAccepted
from server.jobs.runtime import ImportedSolveRefusal
from server.mesh.artifact import read_verified_import_mesh
from server.mesh.file_import import MAX_MESH_BYTES, mesh_text, parse_file_mesh, prepare_file_mesh
from server.mesh.integrity import mesh_integrity_report
from server.mesh.prepared_gc import prepared_mesh_use
from server.platform.staging import publish_staging_directory
from server.workspace.archive import archive_folder_slug

MAX_ACCEPTED_SETUP_WRITES = 512

Axis = Literal["+x", "-x", "+y", "-y", "+z", "-z"]


class MeshFile(JobModel):
    filename: str = Field(min_length=1, max_length=240)
    msh_text: str = Field(min_length=1, max_length=MAX_MESH_BYTES)


class FilePreparation(JobModel):
    """How a file is turned into a solver mesh: its units, frame and sources."""

    units: Literal["mm", "cm", "m", "in"]
    forward: Axis
    up: Axis
    source_tags: list[int] = Field(min_length=1, max_length=128)


class MeshRegistration(MeshFile, FilePreparation):
    pass


class SourceInventoryItem(BaseModel):
    """One source of a CAD return, as its manifest states it."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    role: str
    required: StrictBool


class ModelSetupWrite(BaseModel):
    """A setup for one model, for the sources of a prepared mesh.

    ``prepared_id`` names the prepared mesh; the server reads its sources. A CAD
    project's settings can also be recorded before anything is prepared, for
    the sources its return lists (``inventory``). Neither: the model's latest
    prepared mesh.
    """

    model_config = ConfigDict(extra="forbid")

    prepared_id: str | None = Field(default=None, min_length=1)
    inventory: list[SourceInventoryItem] | None = Field(default=None, min_length=1)
    setup: ImportedSolveSetup

    @model_validator(mode="after")
    def one_source_of_inventory(self) -> ModelSetupWrite:
        if self.prepared_id is not None and self.inventory is not None:
            raise ValueError("name a prepared mesh or a source inventory, not both")
        return self


class ModelRefusal(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


def _refusal(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=error_envelope(code=code, stage="imported_model", message=message),
    )


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _original_path(record: dict[str, Any]) -> Path:
    return Path(record["mesh_store_path"]).parent / "original.msh"


def _intact(record: dict[str, Any], source_bytes: bytes, solver_text: str) -> bool:
    """Both retained files of a file preparation are exactly what it recorded."""

    try:
        return (
            read_verified_import_mesh(record) == solver_text
            and _original_path(record).read_bytes() == source_bytes
        )
    except (OSError, ValueError):
        return False


def register_mesh(
    store: Any,
    data_dir: Path,
    body: MeshRegistration,
    *,
    validate_record: Any = None,
) -> dict[str, Any]:
    """Prepare a file once per (original, preparation): the same inputs are one ingest.

    The files are published before the short insert transaction, never inside
    it, so no store lock or write transaction waits on a 32 MB fsync.
    ``validate_record`` checks the record before anything is published (a
    replay holds the setup it carries to the reconstructed sources). No
    prepared-mesh collection runs meanwhile, and a preparation this answers
    with is marked used, so collection gives it its grace period again.
    """

    with prepared_mesh_use():
        return _register_mesh(store, data_dir, body, validate_record=validate_record)


def _register_mesh(
    store: Any,
    data_dir: Path,
    body: MeshRegistration,
    *,
    validate_record: Any = None,
) -> dict[str, Any]:
    original = parse_file_mesh(body.msh_text)
    mesh, transform = prepare_file_mesh(original, body.units, body.forward, body.up)
    if len(set(body.source_tags)) != len(body.source_tags) or set(body.source_tags) - set(
        mesh.tags.tolist()
    ):
        raise ValueError("Select unique driven groups that exist in this mesh.")
    filename = body.filename.replace("\\", "/").rsplit("/", 1)[-1]
    source_bytes = body.msh_text.encode("utf-8")
    solver_text = mesh_text(mesh)
    digest = _sha256(solver_text.encode())
    original_digest = _sha256(source_bytes)
    spec = {
        "original_sha256": original_digest,
        "transform": transform,
        "source_tags": sorted(body.source_tags),
    }
    spec_digest = _sha256(canonical_utf8_strict_json(spec).encode())
    model_id = file_model_id(original_digest)

    # The same file prepared the same way is the preparation already retained,
    # as long as both of its files are intact.
    candidates = store.find_file_ingests(model_id, spec_digest)
    for row in candidates:
        existing = json.loads(row["record_json"])
        if _intact(existing, source_bytes, solver_text):
            if validate_record is not None:
                validate_record(dict(existing))
            store.touch_ingest(str(existing["ingest_id"]))
            return existing

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
    ingest_id = mint_id("wgi_")
    created_at = utc_now()
    destination = parent / ingest_id
    model = store.get_model(model_id) or {}
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
        # One folder for every run of this file, however it was prepared.
        "archive_stem": model.get("archive_stem")
        or f"{archive_folder_slug(Path(filename).stem)}_{ingest_id[-10:]}",
        "document": {"name": filename},
        "sources": sources,
        "source_tags": {source["id"]: tag for source, tag in zip(sources, sorted(body.source_tags))},
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
            "integrity": mesh_integrity_report(mesh.points, mesh.triangles),
            "metadata": {},
        },
        "summary": {
            **mesh.summary(),
            "dimensions_mm": (np.ptp(mesh.points, axis=0) * 1000).tolist(),
        },
    }
    if validate_record is not None:
        validate_record(record)
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
    try:
        row, created = store.insert_file_ingest(
            ingest_id=ingest_id,
            created_at=created_at,
            manifest_sha256=spec_digest,
            artifact_sha256=original_digest,
            record_json=json.dumps(record, allow_nan=False),
            known_ingest_ids=[candidate["ingest_id"] for candidate in candidates],
        )
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    if not created:
        # A concurrent preparation of the same file and spec committed first.
        shutil.rmtree(destination, ignore_errors=True)
    return json.loads(row["record_json"])


def read_original(store: Any, model_id: str) -> tuple[str, str]:
    """``(filename, text)`` of a file model's retained original, verified by its digest."""

    if not model_id.startswith(FILE_MODEL_PREFIX):
        raise ModelRefusal(409, "not_a_file_model", "Only an imported file is prepared from its original.")
    expected = model_id[len(FILE_MODEL_PREFIX):]
    for row in store.list_model_ingests(model_id):
        record = json.loads(row["record_json"])
        try:
            with _original_path(record).open("rb") as handle:
                raw = handle.read(MAX_MESH_BYTES + 1)
        except (KeyError, OSError):
            continue
        if len(raw) <= MAX_MESH_BYTES and _sha256(raw) == expected:
            return str(record["source_file"]["filename"]), raw.decode("utf-8")
    raise ModelRefusal(
        409, "original_unavailable",
        "The original of this mesh is no longer retained. Import the file again.",
    )


def prepare_model(store: Any, data_dir: Path, model_id: str, body: FilePreparation) -> dict[str, Any]:
    if store.get_model(model_id) is None:
        raise ModelRefusal(404, "model_not_found", "Imported model not found. Import the file again.")
    filename, text = read_original(store, model_id)
    return register_mesh(
        store, data_dir, MeshRegistration(filename=filename, msh_text=text, **body.model_dump())
    )


def _model_summary(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_id": model["model_id"],
        "producer": model["producer"],
        "display_name": model["display_name"],
        "archive_stem": model["archive_stem"],
        "latest_prepared_id": model["latest_ingest_id"],
        "created_at": model["created_at"],
        "updated_at": model["updated_at"],
    }


def public_record(record: dict[str, Any], model_id: str | None) -> dict[str, Any]:
    """What a client sees of a prepared mesh. A CAD snapshot has no source file."""

    keys = (
        "ingest_id", "producer", "created_at", "manifest_sha256", "artifact_sha256",
        "source_file", "source_tags", "sources", "summary",
    )
    return {
        **{key: record[key] for key in keys if key in record},
        "producer": record.get("producer") or "cad",
        "model_id": model_id,
    }


def _prepared_with_mesh(record: dict[str, Any], model_id: str | None) -> dict[str, Any]:
    return {**public_record(record, model_id), "msh_text": read_verified_import_mesh(record)}


def _prepared_row(store: Any, model_id: str, prepared_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = store.get_ingest(prepared_id)
    if row is None:
        raise ModelRefusal(
            404, "prepared_unavailable", "This prepared mesh is no longer retained. Prepare it again."
        )
    if row.get("model_id") != model_id:
        raise ModelRefusal(409, "model_mismatch", "This prepared mesh belongs to another model.")
    return row, json.loads(row["record_json"])


def read_model(store: Any, model_id: str, prepared_id: str | None) -> dict[str, Any]:
    model = store.get_model(model_id)
    if model is None:
        raise ModelRefusal(404, "model_not_found", "Imported model not found.")
    latest = store.get_ingest(model["latest_ingest_id"]) if model["latest_ingest_id"] else None
    if model["producer"] == "mesh-file":
        try:
            read_original(store, model_id)
            original_available = True
        except ModelRefusal:
            original_available = False
    else:
        bundle = json.loads(latest["record_json"]).get("bundle_store_path") if latest else None
        original_available = bool(bundle) and Path(str(bundle)).exists()
    response: dict[str, Any] = {
        "model": _model_summary(model),
        "latest_prepared": (
            public_record(json.loads(latest["record_json"]), model_id) if latest else None
        ),
        "original_available": original_available,
    }
    if prepared_id is not None:
        _row, record = _prepared_row(store, model_id, prepared_id)
        try:
            response["prepared"] = _prepared_with_mesh(record, model_id)
        except (OSError, ValueError) as exc:
            raise ModelRefusal(
                409, "prepared_unavailable", f"Retained mesh unavailable: {exc}. Import it again."
            ) from exc
    return response


def model_of_prepared(store: Any, prepared_id: str) -> dict[str, Any]:
    """Which model one prepared mesh is filed under (for a pointer that named only the mesh)."""

    row = store.get_ingest(prepared_id)
    if row is None or not row.get("model_id"):
        raise ModelRefusal(
            404, "prepared_unavailable", "This prepared mesh is not retained under any model."
        )
    return {"model_id": row["model_id"], "prepared_id": prepared_id}


def _setup_inventory(store: Any, model_id: str, prepared_id: str | None) -> tuple[str, dict[str, Any]]:
    """The inventory a model's setup is keyed by, from one of its prepared meshes."""

    if prepared_id is None:
        model = store.get_model(model_id)
        prepared_id = (model or {}).get("latest_ingest_id")
        if not prepared_id:
            raise ModelRefusal(404, "model_not_found", "Imported model not found.")
    _row, record = _prepared_row(store, model_id, prepared_id)
    return inventory_sha256(record.get("sources") or []), record


def read_setup(
    store: Any, model_id: str, prepared_id: str | None, sources: list[SourceInventoryItem] | None = None,
) -> dict[str, Any]:
    inventory = inventory_sha256([item.model_dump() for item in sources]) if sources is not None else (
        _setup_inventory(store, model_id, prepared_id)[0]
    )
    row = store.get_model_setup(model_id, inventory)
    revision = store.get_setup_revision(row["revision_id"]) if row else None
    return {
        "setup": json.loads(revision["setup_json"]) if revision else None,
        "revision_id": revision["revision_id"] if revision else None,
        **({"cad_claimed": store.get_lineage_cad_names(model_id) is not None} if sources is not None else {}),
    }


def mount_models_api(application: FastAPI) -> None:
    router = APIRouter(prefix="/api/models", tags=["Imported models"])
    store = application.state.cadlink_store
    data_dir = Path(application.state.data_dir)
    # Pagehide sends immediately. Refuse an older request from that same page
    # if its successor already committed; clients without headers keep working.
    setup_writes = asyncio.Lock()
    accepted_setup_writes: OrderedDict[tuple[str, str, str], int] = OrderedDict()
    refused = {
        404: {"model": ErrorEnvelope, "description": "Unknown model or prepared mesh"},
        409: {"model": ErrorEnvelope, "description": "Restart pending, mismatched or unavailable mesh"},
        422: {"model": ErrorEnvelope, "description": "Mesh or setup refused"},
    }
    readable = {
        **{status: refused[status] for status in (404, 409)},
        500: {"model": ErrorEnvelope, "description": "A retained record is unreadable"},
    }

    # Preparing a file is in-flight work the restart would cut, exactly as a
    # CAD ingest is (UPDATE-TRANSACTION-CONTRACT.md §4.2, which refuses
    # POST /api/cadlink/ingest): it is refused while the restart is approved.
    # Saving a setup starts nothing and writes one short transaction, so it is
    # never refused for a restart: the editor's last edit must still land.
    def restart_pending() -> JSONResponse | None:
        if application.state.jobs_runtime._restart_pending():
            return _refusal(
                409, REASON_UPDATE_RESTART_PENDING,
                "An update restart is pending. Try again after restart.",
            )
        return None

    async def answer(call: Any, *args: Any) -> Any:
        """Run a preparation: a ValueError is the mesh or its choices being refused."""

        try:
            return await asyncio.to_thread(call, *args)
        except ModelRefusal as exc:
            return _refusal(exc.status, exc.code, str(exc))
        except ValueError as exc:
            return _refusal(422, "mesh_refused", str(exc))

    async def read(call: Any, *args: Any) -> Any:
        """Run a read: it takes no input to refuse, so a failure is a damaged record."""

        try:
            return await asyncio.to_thread(call, *args)
        except ModelRefusal as exc:
            return _refusal(exc.status, exc.code, str(exc))
        except (KeyError, OSError, TypeError, ValueError) as exc:
            return _refusal(500, "record_unreadable", f"A retained model record is unreadable: {exc}")

    @router.post("/files/inspect", responses={422: refused[422]})
    async def inspect(body: MeshFile) -> Any:
        """Read a file's groups and size, without retaining anything."""

        def summary() -> dict[str, Any]:
            return parse_file_mesh(body.msh_text, require_groups=False).summary()

        return await answer(summary)

    @router.post("/files", responses=refused)
    async def register(body: MeshRegistration) -> Any:
        """Import a file as a model and prepare it; the same inputs find the same mesh."""

        if (blocked := restart_pending()) is not None:
            return blocked

        def run() -> dict[str, Any]:
            record = register_mesh(store, data_dir, body)
            model_id = file_model_id(record["artifact_sha256"])
            return {
                "model": _model_summary(store.get_model(model_id)),
                "prepared": _prepared_with_mesh(record, model_id),
            }

        return await answer(run)

    @router.post("/{model_id}/prepare", responses=refused)
    async def prepare(model_id: str, body: FilePreparation) -> Any:
        """Prepare a file model again from its retained original, however it is now set."""

        if (blocked := restart_pending()) is not None:
            return blocked

        def run() -> dict[str, Any]:
            record = prepare_model(store, data_dir, model_id, body)
            return {"prepared": _prepared_with_mesh(record, model_id)}

        return await answer(run)

    @router.get("", responses=readable)
    async def find_model(prepared_id: str = Query(min_length=1)) -> Any:
        """The model a prepared mesh is filed under: ``{model_id, prepared_id}``."""

        return await read(model_of_prepared, store, prepared_id)

    @router.get("/{model_id}", responses=readable)
    async def get_model(model_id: str, prepared_id: str | None = Query(default=None)) -> Any:
        """A model, its latest preparation, and one named preparation with its mesh."""

        return await read(read_model, store, model_id, prepared_id)

    @router.get("/{model_id}/setup", responses=readable)
    async def get_setup(
        model_id: str, prepared_id: str | None = Query(default=None),
        inventory: str | None = Query(default=None, max_length=100_000),
    ) -> Any:
        """The setup saved for this model and the sources of a prepared mesh."""

        if inventory is None:
            return await read(read_setup, store, model_id, prepared_id)
        if prepared_id is not None or model_id.startswith(FILE_MODEL_PREFIX):
            return _refusal(422, "setup_refused", "A CAD inventory read names no prepared mesh.")
        try:
            sources = TypeAdapter(list[SourceInventoryItem]).validate_json(inventory)
            if not sources or len({item.id for item in sources}) != len(sources):
                raise ValueError("A source inventory must be nonempty with unique ids.")
        except ValueError as exc:
            return _refusal(422, "setup_refused", str(exc))
        return await read(read_setup, store, model_id, None, sources)

    @router.put("/{model_id}/setup", responses=refused)
    async def put_setup(model_id: str, body: ModelSetupWrite, http_request: Request) -> Any:
        """Save a model's setup for the sources of a prepared mesh (or a CAD return)."""

        runtime = application.state.jobs_runtime
        if model_id == UNFILED_MODEL_ID:
            return _refusal(404, "model_not_found", "No model has this id: it names a mesh filed under none.")
        try:
            setup = validate_setup(body.setup.model_dump(mode="json"))
        except ValueError as exc:
            return _refusal(422, "setup_refused", str(exc))
        if body.inventory is not None:
            if model_id.startswith(FILE_MODEL_PREFIX):
                return _refusal(422, "setup_refused", "A file setup names its prepared mesh.")
            # Before anything is prepared, the project is a lineage this
            # workspace knows (a saved design's, or a CAD document's): a setup
            # is never filed under an id nothing else names.
            known = await asyncio.to_thread(store.get_model, model_id) is not None or (
                await asyncio.to_thread(store.lineage_known, model_id)
            )
            if not known:
                return _refusal(404, "model_not_found", "No model or project has this id here.")
            inventory = inventory_sha256([item.model_dump() for item in body.inventory])
        else:
            try:
                inventory, record = await asyncio.to_thread(
                    _setup_inventory, store, model_id, body.prepared_id
                )
                request = solve_request_for(
                    setup,
                    ingest_id=record["ingest_id"],
                    manifest_sha256=record["manifest_sha256"],
                    artifact_sha256=record["artifact_sha256"],
                    acknowledged_findings=[],
                )
                if record.get("native_source"):
                    request.geometry.required_features = list(
                        record["native_source"]["required_features"]
                    )
                # Share setup checks with admission, without approving findings
                # or requiring a confirmed CAD frame merely to save settings.
                await runtime._prepared_model_record(request)
                await asyncio.to_thread(
                    runtime._validate_imported_model_setup, request.geometry, record
                )
            except ModelRefusal as exc:
                return _refusal(exc.status, exc.code, str(exc))
            except ImportedSolveRefusal as exc:
                return _refusal(422, exc.reason_code, str(exc))
            except ValueError as exc:
                return _refusal(422, "setup_refused", str(exc))

        def record_setup() -> dict[str, Any]:
            revision = store.create_setup_revision(setup_content(setup), setup_digest(setup))
            store.record_model_setup(model_id, inventory, revision["revision_id"])
            return revision

        writer = http_request.headers.get("X-WG-Setup-Writer")
        sequence_header = http_request.headers.get("X-WG-Setup-Seq")
        try:
            sequence = int(sequence_header) if sequence_header is not None else 0
        except ValueError:
            return _refusal(422, "setup_refused", "Invalid setup write sequence.")
        async with setup_writes:
            key = (model_id, inventory, writer or "")
            if writer and sequence <= accepted_setup_writes.get(key, -1):
                return _refusal(409, "setup_write_superseded", "A newer setup from this window is already saved.")
            revision = await asyncio.to_thread(record_setup)
            if writer:
                accepted_setup_writes[key] = sequence
                accepted_setup_writes.move_to_end(key)
                while len(accepted_setup_writes) > MAX_ACCEPTED_SETUP_WRITES:
                    accepted_setup_writes.popitem(last=False)
        return {
            "revision_id": revision["revision_id"],
            "content_sha256": revision["content_sha256"],
            "inventory_sha256": inventory,
        }

    @router.post(
        "/{model_id}/solve",
        response_model=SolveAccepted,
        response_model_exclude_none=True,
        responses={
            404: {"model": ErrorEnvelope, "description": "Unknown model or prepared mesh"},
            409: {
                "model": ErrorEnvelope,
                "description": (
                    "Restart pending, mismatched or unavailable mesh, settings needed, "
                    "solver frame to confirm, findings to review, or a conflicting Solve id"
                ),
            },
            422: {"model": ErrorEnvelope, "description": "Setup or request refused"},
            503: {"model": ErrorEnvelope, "description": "Engine unavailable"},
        },
    )
    async def solve(model_id: str, body: ModelSolveRequest) -> Any:
        """Solve this model as the press showed it: the one way an imported solve starts.

        A job already preparing, queued or running for the same model,
        prepared mesh and setup is returned instead of a second one.
        """

        try:
            job_id = await solve_model(application.state, model_id, body)
        except ModelSolveRefusal as exc:
            return JSONResponse(
                status_code=exc.status,
                content=error_envelope(
                    code=exc.code, stage="submission", message=str(exc),
                    retryable=exc.retryable, details=exc.details,
                    client_request_id=body.client_request_id,
                ),
            )
        return SolveAccepted(job_id=job_id, client_request_id=body.client_request_id)

    application.include_router(router)


__all__ = [
    "FilePreparation",
    "MeshFile",
    "MeshRegistration",
    "ModelSetupWrite",
    "model_of_prepared",
    "mount_models_api",
    "prepare_model",
    "public_record",
    "register_mesh",
]
