"""Saved setups for retained models, independent of the preparation producer."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException

from server.cadlink.joblane.project_setup import inventory_sha256
from server.jobs.imported_setup import (
    ImportedSolveSetup,
    setup_content,
    setup_digest,
    solve_request_for,
    validate_setup,
)
from server.mesh.records import get_prepared_mesh


def setup_owner(store: Any, record: dict[str, Any]) -> tuple[str, str]:
    """Derive the owner server-side. A file has no synthetic CAD lineage."""
    if record.get("producer") == "mesh-file":
        return "mesh-file:" + record["ingest_id"], str(record["manifest_sha256"])
    project = record.get("project") or {}
    lineage = project.get("lineage_id")
    if not lineage:
        design_id = (record.get("anchor") or {}).get("design_id")
        design = store.get_design(design_id) if design_id else None
        lineage = (design or {}).get("lineage_id")
    if not lineage:
        raise ValueError("Prepare this CAD model in its project before saving its setup.")
    return str(lineage), inventory_sha256(record.get("sources") or [])


def mount_imported_models(application: FastAPI) -> None:
    router = APIRouter(prefix="/api/imported-models", tags=["Imported models"])
    store = application.state.cadlink_store

    def record_for(ingest_id: str) -> dict[str, Any]:
        record = get_prepared_mesh(store, ingest_id)
        if record is None:
            raise HTTPException(404, "Retained model not found. Import or prepare it again.")
        return record

    @router.get("/{ingest_id}/setup")
    async def read_setup(ingest_id: str) -> dict[str, Any]:
        try:
            record = await asyncio.to_thread(record_for, ingest_id)
            owner, inventory = await asyncio.to_thread(setup_owner, store, record)
            row = await asyncio.to_thread(store.get_model_setup, owner, inventory)
            revision = (
                await asyncio.to_thread(store.get_setup_revision, row["revision_id"])
                if row
                else None
            )
            return {
                "setup": json.loads(revision["setup_json"]) if revision else None,
                "revision_id": revision["revision_id"] if revision else None,
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.put("/{ingest_id}/setup")
    async def save_setup(ingest_id: str, body: ImportedSolveSetup) -> dict[str, Any]:
        runtime = application.state.jobs_runtime
        if runtime._restart_pending():
            raise HTTPException(409, "An update restart is pending. Save after restart.")
        try:
            record = await asyncio.to_thread(record_for, ingest_id)
            setup = validate_setup(body.model_dump(mode="json"))
            request = solve_request_for(
                setup,
                ingest_id=ingest_id,
                manifest_sha256=record["manifest_sha256"],
                artifact_sha256=record["artifact_sha256"],
                acknowledged_findings=[],
            )
            if record.get("native_source"):
                request.geometry.required_features = list(
                    record["native_source"]["required_features"]
                )
            # Share setup checks with admission, without approving findings or
            # requiring a confirmed CAD frame merely to save editable settings.
            await runtime._prepared_model_record(request)
            await asyncio.to_thread(
                runtime._validate_imported_model_setup, request.geometry, record
            )
            owner, inventory = await asyncio.to_thread(setup_owner, store, record)
            revision = await asyncio.to_thread(
                store.create_setup_revision, setup_content(setup), setup_digest(setup)
            )
            await asyncio.to_thread(
                store.record_model_setup, owner, inventory, revision["revision_id"]
            )
            return {
                "revision_id": revision["revision_id"],
                "content_sha256": revision["content_sha256"],
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    application.include_router(router)
