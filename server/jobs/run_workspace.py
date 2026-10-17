"""Open a run's workspace again: ``GET /api/runs/{id}/workspace``.

The one way the frontend reopens an imported run (docs/architecture/
CAD-OPERATIONS.md, "Opening a run's workspace"). The server answers with the
run's model, the prepared mesh it solved, whether the model's original is
still retained (a file can then be prepared again), and the setup revision the
run was made from, so an unedited Solve of the reopened run names that same
revision.

- **The setup.** A run records the setup revision it was made from
  (``task_metadata.imported_geometry.setup_revision_id``, or the CAD record's
  ``setup.revision_id``). A run from before revisions existed has none; its
  setup is its effective request without what the prepared mesh contributes,
  stored once as the content-addressed revision it is. The same run always
  derives the same revision, and nothing the request held is dropped.
- **Refusals.** A run that cannot be reopened answers with ``refusal`` (code
  and words) beside whatever could be read, never with a server error: a
  prepared mesh that is no longer retained, a model this workspace has no
  record of, a CAD solve that ended before it had a request.
- **A parametric run** reopens its design from the run itself (its design
  snapshot travels with the run list), so this answers it with the
  ``parametric_run`` refusal rather than a second way to reopen a design.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server.cadlink.store import FILE_MODEL_PREFIX, UNFILED_MODEL_ID
from server.integration.contracts import ErrorEnvelope, error_envelope
from server.jobs.cad_intent import CAD_INTENT, cad_of
from server.jobs.imported_setup import setup_content, setup_digest, validate_setup
from server.jobs.model_solve import recorded_setup_revision
from server.jobs.models import SolveRequest
from server.jobs.models_api import ModelRefusal, public_record, read_original
from server.mesh.artifact import read_verified_import_mesh

# What a prepared mesh contributes to a request, and so what a setup is without.
_SNAPSHOT_FIELDS = ("type", "ingest_id", "manifest_sha256", "artifact_sha256", "acknowledged_findings")


class WorkspaceRefusal(BaseModel):
    """Why a run's workspace cannot be reopened (in part or at all)."""

    code: str
    message: str


class WorkspaceSetup(BaseModel):
    """The setup revision a run was made from, and its content."""

    revision_id: str
    content: dict[str, Any]
    #: True when the run recorded no revision and it was derived from its request.
    derived: bool = False


class RunWorkspace(BaseModel):
    """Everything the frontend opens an imported run with."""

    job_id: str
    model: dict[str, Any] | None = None
    prepared: dict[str, Any] | None = Field(
        default=None, description="The prepared mesh the run solved; a file's carries its solver mesh.",
    )
    original_available: bool = False
    setup: WorkspaceSetup | None = None
    solve_options: dict[str, Any] | None = Field(
        default=None, description="The options the run was solved with (its effective request).",
    )
    refusal: WorkspaceRefusal | None = None


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_imported(row: Mapping[str, Any]) -> bool:
    config = _mapping(row.get("config_json"))
    return config.get("type") == CAD_INTENT or _mapping(config.get("geometry")).get("type") == "imported"


def _effective_request(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The run's exact imported request, or None for a CAD solve never bound to one."""

    config = _mapping(row.get("config_json"))
    if config.get("type") == CAD_INTENT or not isinstance(config.get("geometry"), Mapping):
        return None
    try:
        return SolveRequest.model_validate(config).model_dump(mode="json")
    except ValueError:
        # A request an earlier build stored that this build would not accept:
        # its own fields are still the run's settings.
        return dict(config)


def _prepared_id(row: Mapping[str, Any], request: Mapping[str, Any] | None) -> str | None:
    geometry = _mapping(_mapping(request).get("geometry"))
    if geometry.get("ingest_id"):
        return str(geometry["ingest_id"])
    preparation = _mapping(cad_of(row).get("preparation"))
    return str(preparation["ingest_id"]) if preparation.get("ingest_id") else None


def derived_setup(request: Mapping[str, Any]) -> dict[str, Any]:
    """The setup an exact imported request is: everything but the prepared mesh."""

    geometry = dict(_mapping(request.get("geometry")))
    for field in _SNAPSHOT_FIELDS:
        geometry.pop(field, None)
    return {"geometry": geometry, "options": dict(_mapping(request.get("options")))}


def _setup(store: Any, row: Mapping[str, Any], request: Mapping[str, Any] | None) -> WorkspaceSetup:
    revision_id = recorded_setup_revision(row)
    if revision_id is None and not request:
        intent = _mapping(row.get("config_json"))
        revision_id = intent.get("setup_revision_id") or None
    if revision_id is not None:
        stored = store.get_setup_revision(str(revision_id))
        if stored is not None:
            return WorkspaceSetup(revision_id=str(stored["revision_id"]), content=json.loads(stored["setup_json"]))
        if not request:
            raise _Refused("setup_unavailable", "The settings this run was solved with are no longer retained.")
    if not request:
        raise _Refused(
            "no_request", "This solve ended before it had a solve request. Solve it again from CAD Link.",
        )
    try:
        setup = validate_setup(derived_setup(request))
    except ValueError as exc:
        raise _Refused("setup_unreadable", f"This run's settings cannot be read as a setup: {exc}") from exc
    stored = store.create_setup_revision(setup_content(setup), setup_digest(setup))
    return WorkspaceSetup(revision_id=str(stored["revision_id"]), content=json.loads(stored["setup_json"]),
                          derived=True)


def _model_summary(model: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model_id": model["model_id"],
        "producer": model["producer"],
        "display_name": model["display_name"],
        "archive_stem": model["archive_stem"],
        "latest_prepared_id": model["latest_ingest_id"],
        "created_at": model["created_at"],
        "updated_at": model["updated_at"],
    }


def _original_available(store: Any, model_id: str, record: Mapping[str, Any] | None) -> bool:
    if model_id.startswith(FILE_MODEL_PREFIX):
        try:
            read_original(store, model_id)
            return True
        except ModelRefusal:
            return False
    bundle = _mapping(record).get("bundle_store_path")
    return bool(bundle) and Path(str(bundle)).exists()


def read_run_workspace(row: Mapping[str, Any], store: Any) -> RunWorkspace:
    """A run's workspace: its model, prepared mesh, original and setup."""

    job_id = str(row["id"])
    if not _is_imported(row):
        return RunWorkspace(job_id=job_id, refusal=WorkspaceRefusal(
            code="parametric_run",
            message="A parametric run reopens its design from the run itself, not as an imported model.",
        ))
    request = _effective_request(row)
    imported = _mapping(_mapping(row.get("task_metadata")).get("imported_geometry"))
    answer = RunWorkspace(job_id=job_id, solve_options=dict(_mapping(_mapping(request).get("options"))) or None)
    prepared_id = _prepared_id(row, request)
    ingest = store.get_ingest(prepared_id) if prepared_id else None
    record = json.loads(ingest["record_json"]) if ingest is not None else None
    # The model: the prepared mesh's (a Solve names the model its mesh is
    # filed under, 'unfiled' for none), else what the run recorded of it.
    model_id = (
        (ingest.get("model_id") or UNFILED_MODEL_ID) if ingest is not None
        else imported.get("model_id") or imported.get("anchor_lineage_id") or None
    )
    model = store.get_model(model_id) if model_id and model_id != UNFILED_MODEL_ID else None
    if model is not None:
        answer.model = _model_summary(model)
        answer.original_available = _original_available(store, str(model_id), record)
    try:
        answer.setup = _setup(store, row, request)
    except _Refused as exc:
        answer.refusal = WorkspaceRefusal(code=exc.code, message=str(exc))
        return answer
    if record is None:
        answer.refusal = WorkspaceRefusal(
            code="prepared_unavailable",
            message=(
                "This run's prepared mesh is no longer retained. Prepare the model again to solve it."
                if answer.original_available
                else "This run's prepared mesh is no longer retained. Import the geometry again."
            ),
        )
        return answer
    if model is None and model_id != UNFILED_MODEL_ID:
        answer.refusal = WorkspaceRefusal(
            code="model_not_found", message="This run's imported model is no longer known here. Import it again.",
        )
        return answer
    prepared = public_record(record, str(model_id))
    if (record.get("producer") or "cad") == "mesh-file":
        try:
            prepared["msh_text"] = read_verified_import_mesh(record)
        except (OSError, ValueError) as exc:
            answer.refusal = WorkspaceRefusal(
                code="prepared_unavailable",
                message=f"This run's retained mesh is unavailable: {exc}. Import it again.",
            )
            return answer
    answer.prepared = prepared
    return answer


def mount_run_workspace(application: FastAPI) -> None:
    router = APIRouter(prefix="/api/runs", tags=["Runs"])

    @router.get(
        "/{job_id}/workspace",
        response_model=RunWorkspace,
        response_model_exclude_none=True,
        responses={
            404: {"model": ErrorEnvelope, "description": "Unknown run"},
            500: {"model": ErrorEnvelope, "description": "A retained record is unreadable"},
        },
    )
    async def run_workspace(job_id: str) -> Any:
        """Reopen a run: its model, prepared mesh, original and the setup it was made from."""

        runtime = application.state.jobs_runtime
        store = application.state.cadlink_store
        await runtime.start()
        row = await asyncio.to_thread(runtime.store.get_job_row, job_id)
        if row is None:
            return JSONResponse(status_code=404, content=error_envelope(
                code="run_not_found", stage="run_workspace", message="Run not found.",
            ))
        try:
            return await asyncio.to_thread(read_run_workspace, row, store)
        except (KeyError, TypeError, ValueError) as exc:
            return JSONResponse(status_code=500, content=error_envelope(
                code="record_unreadable", stage="run_workspace",
                message=f"A retained record of this run is unreadable: {exc}",
            ))

    application.include_router(router)


__all__ = ["RunWorkspace", "derived_setup", "mount_run_workspace", "read_run_workspace"]
