"""Common solve refusals keep each route's complete public envelope."""

import asyncio
import json

import pytest

from server.cadlink.interpretation.solver_frame import REASON as FRAME_CONFIRMATION_REQUIRED
from server.cadlink.store import CadLinkStore
from server.jobs import model_solve
from server.jobs.api import create_jobs_router
from server.jobs.models import SolveBlocker
from server.jobs.runtime import (
    EngineUnavailableError, ImportedSolveRefusal, RemovedSolverError,
    SymmetryValidationError, UnknownEngineError,
)
from server.tests._asgi import SOLVE_BODY
from test_jobs_api import _request
from test_mesh_import import register_mesh, registration
from test_models_api import app_for, setup_for


CASES = [
    (UnknownEngineError("Choose an available engine."), 422, "unknown_engine", None, True),
    (RemovedSolverError("Choose Full 3D."), 422, "removed_solver_mode", None, True),
    (EngineUnavailableError("Prepare this engine, then try again.", blocker=SolveBlocker(
        code="not_ready", engine="metal", message="Prepare this engine, then try again.",
    )), 503, "engine_unavailable", None, True),
    (SymmetryValidationError("Choose a supported symmetry."), 422, "invalid_symmetry", None, False),
    (ImportedSolveRefusal(FRAME_CONFIRMATION_REQUIRED, "Confirm the solver frame.", details={"axis": "z"}),
     409, FRAME_CONFIRMATION_REQUIRED, FRAME_CONFIRMATION_REQUIRED, False),
    (ImportedSolveRefusal("ingest_mesh_unavailable", "Prepare the model again.", details={"prepared_id": "gone"}),
     409, "prepared_unavailable", "ingest_mesh_unavailable", False),
    (ImportedSolveRefusal("unacknowledged_findings", "Review the mesh findings.", details={"findings": ["one"]}),
     409, "findings_need_review", "unacknowledged_findings", False),
]


@pytest.mark.parametrize("route", ["planning", "submission", "retry", "model"])
@pytest.mark.parametrize(("exc", "status", "code", "imported_code", "common"), CASES,
                         ids=["unknown", "removed", "unavailable", "symmetry", "frame", "prepared", "findings"])
def test_refusal_envelope_and_shared_classifier(tmp_path, monkeypatch, route, exc, status, code, imported_code, common):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        app.include_router(create_jobs_router(runtime))
        record = register_mesh(store, tmp_path, registration())
        calls = []
        classify = model_solve.refusal_from

        def counted(error):
            calls.append(error)
            return classify(error)

        async def refuse(*_args, **_kwargs):
            raise exc

        monkeypatch.setattr(model_solve, "refusal_from", counted)
        monkeypatch.setattr(runtime, "validate_submission", refuse)
        monkeypatch.setattr(runtime, "submit", refuse)
        monkeypatch.setattr(runtime, "retry", refuse)
        paths = {"planning": "/api/solve/plan", "submission": "/api/solve", "retry": "/api/jobs/old/retry",
                 "model": f'/api/models/mesh-file:{record["artifact_sha256"]}/solve'}
        body = {**SOLVE_BODY, "client_request_id": "press-1"} if route != "model" else {
            "prepared_id": record["ingest_id"], "client_request_id": "press-1", "setup": setup_for(record),
        }
        expected_status, expected_code = status, code
        if imported_code and route in {"planning", "submission"}:
            expected_status, expected_code = 422, imported_code
        if isinstance(exc, SymmetryValidationError) and route == "planning":
            expected_code = "invalid_solve_plan"
        try:
            actual_status, raw = await _request(app, "POST", paths[route], body=body)
            expected = {"detail": str(exc), "error": {
                "schema_version": 1, "code": expected_code,
                "stage": "submission" if route == "model" else route,
                "message": str(exc), "retryable": False, "details": getattr(exc, "details", {}),
                "client_request_id": None if route == "retry" else "press-1",
            }}
            assert actual_status == expected_status
            assert json.loads(raw) == expected
            if common:
                assert calls == [exc], "every common refusal must use the existing classifier"
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_planning_value_error_keeps_its_deliberate_code(tmp_path, monkeypatch):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        app.include_router(create_jobs_router(runtime))

        async def refuse(*_args, **_kwargs):
            raise ValueError("The design is incomplete. Open it again.")

        monkeypatch.setattr(runtime, "validate_submission", refuse)
        try:
            status, raw = await _request(app, "POST", "/api/solve/plan", body=SOLVE_BODY)
            assert status == 422
            assert json.loads(raw) == {"detail": "The design is incomplete. Open it again.",
                "error": {"schema_version": 1, "code": "invalid_solve_plan", "stage": "planning",
                          "message": "The design is incomplete. Open it again.",
                          "retryable": False, "details": {}, "client_request_id": None}}
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())
