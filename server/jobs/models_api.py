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
from copy import deepcopy
import hashlib
import json
import logging
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
from server.cadlink.roles import inventory_sha256, normalize_source_role, prepared_inventory_sha256
from server.cadlink.store import FILE_MODEL_PREFIX, UNFILED_MODEL_ID, file_model_id
from server.cadlink.wglink_protocol import SOURCE_ROLES
from server.contracts.geometry import SYMMETRY_PLANE_AXIS, imported_symmetry_from_cut_planes
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
from server.mesh.builder import _enforce_dense_solver_memory_ceiling
from server.mesh.errors import SolverMemoryTooLarge
from server.mesh.file_import import MAX_MESH_BYTES, file_mesh_integrity, inspect_file_mesh, mesh_text, parse_file_mesh, prepare_file_mesh, solve_group_tags, validate_file_symmetry, validate_remove_lid_planes
from server.mesh.imported import polar_grid_from_symmetry
from server.mesh.prepared_gc import prepared_mesh_use
from server.platform.staging import publish_staging_directory
from server.workspace.archive_identity import claim_project_folder

logger = logging.getLogger(__name__)

MAX_ACCEPTED_SETUP_WRITES = 512

Axis = Literal["+x", "-x", "+y", "-y", "+z", "-z"]


class MeshFile(JobModel):
    filename: str = Field(min_length=1, max_length=240)
    msh_text: str = Field(min_length=1, max_length=MAX_MESH_BYTES)


class MeshInspection(MeshFile):
    units: Literal["mm", "cm", "m", "in"] | None = None
    forward: Axis = "+z"
    up: Axis = "+y"


class RetainedMeshInspection(JobModel):
    model_id: str = Field(min_length=1)
    units: Literal["mm", "cm", "m", "in"] | None = None
    forward: Axis = "+z"
    up: Axis = "+y"


class FilePreparation(JobModel):
    """How a file is turned into a solver mesh: its units, frame and sources."""

    units: Literal["mm", "cm", "m", "in"]
    forward: Axis
    up: Axis
    source_tags: list[int] = Field(default_factory=list, max_length=128)
    roles: dict[int, str] | None = Field(default=None, max_length=256)
    domain_planes: list[str] = Field(default_factory=list, max_length=2)
    reflect_planes: list[str] | None = Field(default=None, max_length=2)
    remove_lid_planes: list[str] = Field(
        default_factory=list, max_length=2,
        description="Remove closed faces on these declared symmetry planes. A plane without a closed face is rejected.",
    )

    @model_validator(mode="after")
    def surface_roles(self) -> FilePreparation:
        if self.roles is None and not self.source_tags:
            raise ValueError("Choose at least one driver surface.")
        if self.roles is not None:
            self.roles = {
                tag: normalize_source_role(role, mode="protocol")
                for tag, role in self.roles.items()
            }
            if any(role not in (*SOURCE_ROLES, "RIGID") for role in self.roles.values()):
                raise ValueError("Choose a driver, Port or passive opening, or Rigid wall for each surface.")
        planes, reflections = validate_file_symmetry(self.domain_planes, self.reflect_planes)
        self.domain_planes = list(planes)
        self.reflect_planes = list(reflections) if reflections is not None else None
        self.remove_lid_planes = list(validate_remove_lid_planes(self.remove_lid_planes, planes))
        return self

    def driven_roles(self) -> dict[int, str]:
        roles = {tag: "HF" for tag in self.source_tags}
        roles.update(self.roles or {})
        return {tag: role for tag, role in roles.items() if role != "RIGID"}


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
    if len(set(body.source_tags)) != len(body.source_tags) or set(body.source_tags) - set(
        original.tags.tolist()
    ):
        raise ValueError("Select unique driven groups that exist in this mesh.")
    if body.roles is not None and set(body.roles) - set(original.tags.tolist()):
        raise ValueError("Assign roles only to groups that exist in this mesh.")
    roles = body.driven_roles()
    mesh, transform = prepare_file_mesh(original, body.units, body.forward, body.up,
                                      domain_planes=body.domain_planes, reflect_planes=body.reflect_planes,
                                      remove_lid_planes=body.remove_lid_planes, roles=roles)
    _enforce_dense_solver_memory_ceiling(
        mesh.triangles, imported_symmetry_from_cut_planes(body.domain_planes).quadrants,
        tags=mesh.tags,
    )
    if not roles:
        raise ValueError("Choose at least one driver surface.")
    written_tags = solve_group_tags(mesh)
    filename = body.filename.replace("\\", "/").rsplit("/", 1)[-1]
    source_bytes = body.msh_text.encode("utf-8")
    solver_text = mesh_text(mesh)
    digest = _sha256(solver_text.encode())
    original_digest = _sha256(source_bytes)
    hf_only = all(role == "HF" for role in roles.values())
    spec = {
        "original_sha256": original_digest,
        "transform": transform,
        "source_tags": sorted(roles) if hf_only else sorted(body.source_tags),
    }
    # HF-only assignments are the old source_tags request, including its
    # preparation digest and the historical SOURCE setup inventory key.
    if body.roles is not None and not hf_only:
        spec["roles"] = {str(tag): role for tag, role in sorted(body.roles.items())}
    if body.domain_planes:
        spec["domain_planes"] = body.domain_planes
        # Retain the choice, including an explicit opt-out, for faithful replay.
        spec["reflect_planes"] = body.reflect_planes
    if body.remove_lid_planes:
        spec["remove_lid_planes"] = body.remove_lid_planes
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
            "role": roles[tag],
            "required": True,
            "name": mesh.names.get(tag, "Untagged surface" if tag == 0 else f"Group {tag}"),
            "observed": {"total_area_mm2": float(areas_mm2[mesh.tags == tag].sum())},
        }
        for tag in sorted(roles)
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
        "source_file": {"filename": filename, **spec, "groups": original.summary()["groups"]},
        # One folder for every run of this file, however it was prepared.
        "archive_stem": model.get("archive_stem")
        or claim_project_folder(store, f"model:{model_id}", Path(filename).stem),
        "document": {"name": filename},
        "sources": sources,
        "source_tags": {source["id"]: written_tags[tag] for source, tag in zip(sources, sorted(roles))},
        "findings": [],
        "skipped_source_ids": [],
        "acoustic_domain": "free-space",
        "normalisation": {"assembly_frame_is_solver_frame": True},
        "symmetry": {"domain_planes": body.domain_planes, "cut_planes": [],
                     "reflected_planes": transform.get("reflected_planes", [])},
        "polar_grid_derivation": polar_grid_from_symmetry({
            "domain_planes": body.domain_planes,
            "planes": {plane: {"accepted": plane in body.domain_planes} for plane in SYMMETRY_PLANE_AXIS},
        }),
        "evidence": {"fem_regions": [], "fem_air_volumes": []},
        "mesh": {
            "stats": {"triangle_count": len(mesh.triangles), "node_count": len(mesh.points)},
            "integrity": file_mesh_integrity(mesh, body.domain_planes),
            "metadata": {},
        },
        "summary": {
            **_file_preparation_summary(mesh.summary(), body.units),
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


def _file_preparation_summary(summary: dict[str, Any], units: str) -> dict[str, Any]:
    """Keep original group IDs beside the IDs written by the shared tag mapper."""
    written = solve_group_tags([group["tag"] for group in summary["groups"]])
    return {
        **summary,
        "suggested_units": units,
        "groups": [{**group, "solve_tag": written[group["tag"]]} for group in summary["groups"]],
    }


def public_record(record: dict[str, Any], model_id: str | None) -> dict[str, Any]:
    """What a client sees of a prepared mesh. A CAD snapshot has no source file."""

    keys = (
        "ingest_id", "producer", "created_at", "manifest_sha256", "artifact_sha256",
        "source_file", "source_tags", "sources", "summary",
        "symmetry", "polar_grid_derivation",
    )
    public = {
        **{key: record[key] for key in keys if key in record},
        "producer": record.get("producer") or "cad",
        "model_id": model_id,
    }

    if public["producer"] == "mesh-file" and "summary" in public:
        public["summary"] = _file_preparation_summary(
            public["summary"], record["source_file"]["transform"]["units"],
        )
    return public


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
    return prepared_inventory_sha256(record), record


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


def _remap_file_setup(setup: dict[str, Any] | None, sources: dict[str, str]) -> dict[str, Any] | None:
    """Carry retained physics by group name even when a re-export changes numeric tags."""

    if setup is None:
        return None
    result = deepcopy(setup)
    geometry = result["geometry"]
    channels = geometry["drive_channels"]
    for channel in channels:
        channel["source_ids"] = [sources[source] for source in channel["source_ids"]]
        if channel.get("patch_weights"):
            channel["patch_weights"] = {sources[source]: weight for source, weight in channel["patch_weights"].items()}
    geometry["skipped_source_ids"] = [sources[source] for source in geometry.get("skipped_source_ids", [])]
    if geometry.get("mesh"):
        geometry["mesh"]["source_size_mm"] = {
            sources[source]: size for source, size in geometry["mesh"]["source_size_mm"].items()
        }
    return result


def _latest_file_preparation(store: Any, model_id: str) -> dict[str, Any]:
    # Reusing an older preparation touches its existing row rather than creating
    # a duplicate. That last use is newer than the model's creation-time pointer.
    return max(store.list_model_ingests(model_id),
               key=lambda row: (row.get("last_used_at") or row["created_at"], row["created_at"], row["ingest_id"]))


def _file_import_memory(store: Any, filename: str, text: str, groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Use the retained model and setup owners; no filename or digest index of our own."""

    model_id = file_model_id(_sha256(text.encode("utf-8")))
    known = store.get_model(model_id)
    answer: dict[str, Any] = {}
    candidates = []
    if known and known["latest_ingest_id"]:
        latest = _latest_file_preparation(store, model_id)
        answer["known_model"] = {"model_id": model_id, "latest_prepared_id": latest["ingest_id"]}
        candidates.append(latest)
    else:
        stem = Path(filename.replace("\\", "/").rsplit("/", 1)[-1]).stem
        seen = set()
        for raw in store.ingest_records():
            record = json.loads(raw)
            if record.get("producer") != "mesh-file" or Path(record["source_file"]["filename"]).stem != stem:
                continue
            previous_id = file_model_id(record["artifact_sha256"])
            if previous_id in seen or store.get_model(previous_id) is None:
                continue
            seen.add(previous_id)
            candidates.append(_latest_file_preparation(store, previous_id))
        candidates.sort(key=lambda row: (row.get("last_used_at") or row["created_at"], row["ingest_id"]), reverse=True)
    current_names = {group["name"]: group["tag"] for group in groups}
    for row in candidates:
        record = json.loads(row["record_json"])
        previous_id = file_model_id(record["artifact_sha256"])
        # Source groups belong to the immutable artifact; a prepared summary
        # can omit a removed symmetry lid. Old records without lid removal
        # already retain the complete original group list in their summary.
        file = record["source_file"]
        previous_groups = file.get("groups")
        if previous_groups is None and not file.get("transform", {}).get("removed_lids"):
            previous_groups = record.get("summary", {}).get("groups")
        if previous_groups is None:
            try:
                original = text if previous_id == model_id else read_original(store, previous_id)[1]
            except ModelRefusal:
                continue
            previous_groups = parse_file_mesh(original).summary()["groups"]
        previous_names = {group["name"]: group["tag"] for group in previous_groups}
        # Ambiguous names cannot identify a source safely. Keep the inspect guesses.
        if len(previous_names) != len(previous_groups) or len(current_names) != len(groups):
            continue
        file = record["source_file"]
        roles = file.get("roles") or {
            str(group["tag"]): next((source["role"] for source in record["sources"]
                                      if source["id"] == f"group_{group['tag']}"), "RIGID")
            for group in previous_groups
        }
        matching_roles = {
            str(tag): (
                "HF" if roles.get(str(previous_names[name])) == "SOURCE"
                else normalize_source_role(roles.get(str(previous_names[name]), "RIGID"), mode="protocol")
            )
            for name, tag in current_names.items() if name in previous_names
        }
        if "previous_roles" not in answer:
            answer["previous_roles"] = matching_roles
        if set(current_names) != set(previous_names):
            continue
        transform = file["transform"]
        preparation = {
            "units": transform["units"], "forward": transform["forward"], "up": transform["up"],
            "roles": matching_roles,
            "domain_planes": file.get("domain_planes", record.get("symmetry", {}).get("domain_planes", [])),
            "reflect_planes": file.get("reflect_planes"),
            "remove_lid_planes": file.get("remove_lid_planes", list(transform.get("removed_lids", {}))),
        }
        saved = read_setup(store, previous_id, record["ingest_id"])
        sources = {f"group_{old_tag}": f"group_{current_names[name]}" for name, old_tag in previous_names.items()}
        setup_row = store.get_model_setup(previous_id, prepared_inventory_sha256(record))
        answer["previous_setup"] = {
            "filename": file["filename"], "updated_at": (setup_row or {}).get("updated_at", record["created_at"]),
            "preparation": preparation, "setup": _remap_file_setup(saved["setup"], sources),
        }
        return answer
    return answer


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
        except SolverMemoryTooLarge as exc:
            logger.warning("Solver memory admission refused a mesh: %s", exc)
            return _refusal(422, "solver_memory_exceeded", SolverMemoryTooLarge.USER_MESSAGE)
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
    async def inspect(body: MeshInspection | RetainedMeshInspection) -> Any:
        """Read a file's groups and size, without retaining anything."""

        def summary() -> dict[str, Any]:
            text = read_original(store, body.model_id)[1] if isinstance(body, RetainedMeshInspection) else body.msh_text
            mesh = parse_file_mesh(text)
            inspected = inspect_file_mesh(mesh, body.units, body.forward, body.up)
            if isinstance(body, MeshInspection):
                inspected.update(_file_import_memory(store, body.filename, text, inspected["groups"]))
            return inspected

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
