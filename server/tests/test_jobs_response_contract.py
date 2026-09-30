"""The response contract describes serialized rows, including historical jobs."""

import asyncio
from copy import deepcopy
import json

from fastapi import FastAPI
from pydantic import ValidationError
import pytest

from server.jobs.api import create_jobs_router
from server.jobs.models import JobItem, JobStatusResponse, SolveOptions, SolveRequest
from server.jobs.runtime import JobRuntime
from server.tests.test_jobs_api import _request


NULLABLE_FIELDS = (
    "stage", "stage_message", "started_at", "completed_at", "label",
    "error_message", "mesh_stats", "script_snapshot", "rating",
    "auto_export_completed_at", "raw_results_file", "mesh_artifact_file",
)
OPTION_FIELDS = (
    "engine", "accuracy", "solver_mode", "symmetry", "frequency_range",
    "num_frequencies", "frequency_spacing", "frequencies_hz", "verbose",
    "mesh_ladder", "mesh_validation_mode", "polar_config", "ground_plane",
    "stage_delay_ms",
)
CAD_SOURCE_FIELDS = (
    "ingest_id", "design_id", "lineage_id", "archive_stem", "manifest_sha256",
    "document_name", "return_state_hash",
)


def _row(config=None, metadata=None):
    return {
        "id": "historical", "status": "complete", "progress": 1.0,
        "created_at": "2026-08-12T00:00:00", "queued_at": "2026-08-12T00:00:00",
        "updated_at": "2026-08-12T00:00:00", "config_json": config or {},
        "task_metadata": metadata or {},
    }


@pytest.mark.parametrize("config", [
    {},
    {"geometry": {"type": "imported", "ingest_id": "legacy-ingest"}},
    {"type": "cad_intent", "operation_id": "preparing-operation"},
])
def test_serialized_historical_rows_satisfy_response_contract(config):
    row = _row(config)
    if config.get("type") == "cad_intent":
        row["status"] = "preparing"
    wire = JobRuntime._serialize_job(row)
    parsed = JobItem.model_validate(wire)
    assert parsed.model_dump(mode="json") == wire
    assert all(name in wire and wire[name] is None for name in NULLABLE_FIELDS)
    assert set(wire["design_availability"]) == {
        "reopenable", "source", "reason_code", "reason", "note",
    }
    assert set(OPTION_FIELDS) == set(wire["solve_options"])
    if wire["cad_source"] is not None:
        assert set(CAD_SOURCE_FIELDS) <= set(wire["cad_source"])
    JobStatusResponse.model_validate(JobRuntime._serialize_job(row, detailed=True))


@pytest.mark.parametrize("detailed", [False, True])
def test_stored_solve_options_preserve_nondefault_values(detailed):
    options = SolveOptions(
        accuracy="accurate", mesh_ladder="auto",
        ground_plane={"enabled": True, "axis": "x", "height_m": 1.5},
    ).model_dump(mode="json")
    wire = JobRuntime._serialize_job(_row({
        "geometry": {"type": "imported"}, "options": options,
    }), detailed=detailed)
    response = JobStatusResponse if detailed else JobItem
    parsed = response.model_validate(wire)
    assert wire["solve_options"] == options
    assert parsed.solve_options.model_dump(mode="json") == options


@pytest.mark.parametrize("field", NULLABLE_FIELDS)
def test_missing_always_present_nullable_key_is_rejected(field):
    wire = JobRuntime._serialize_job(_row())
    del wire[field]
    with pytest.raises(ValidationError) as caught:
        JobItem.model_validate(wire)
    assert (field,) in {error["loc"] for error in caught.value.errors()}


@pytest.mark.parametrize("field", OPTION_FIELDS)
def test_missing_dumped_solve_option_is_rejected_only_in_response(field):
    wire = JobRuntime._serialize_job(_row())
    del wire["solve_options"][field]
    with pytest.raises(ValidationError) as caught:
        JobItem.model_validate(wire)
    assert ("solve_options", field) in {error["loc"] for error in caught.value.errors()}
    assert SolveOptions.model_validate({}).model_dump() == SolveOptions().model_dump()


@pytest.mark.parametrize("field", CAD_SOURCE_FIELDS)
def test_missing_serialized_cad_source_key_is_rejected(field):
    wire = JobRuntime._serialize_job(_row({"geometry": {"type": "imported"}}))
    del wire["cad_source"][field]
    with pytest.raises(ValidationError) as caught:
        JobItem.model_validate(wire)
    assert ("cad_source", field) in {error["loc"] for error in caught.value.errors()}


def test_typed_execution_and_client_authored_exports_survive_rest_validation():
    execution = {"accuracy": "fast", "engine": "dryrun", "formulation": None}
    # The store accepts legacy/exporter-specific entries, not just UI statuses.
    metadata = {
        "solve_execution": execution,
        "channel_solve_executions": {"hf": dict(execution)},
        "auto_export_formats": {"step": {"file": "a.step", "status": "done"}},
    }
    row = _row(metadata=metadata)

    class Runtime:
        async def start(self):
            pass

        async def shutdown(self):
            pass

        async def get_job(self, _job_id):
            return JobRuntime._serialize_job(row, detailed=True)

        async def list_jobs(self, **_kwargs):
            return [JobRuntime._serialize_job(row)], 1

    app = FastAPI()
    app.include_router(create_jobs_router(Runtime()))
    async def exercise():
        for path in ("/api/jobs", "/api/status/historical"):
            status, raw = await _request(app, "GET", path)
            assert status == 200
            body = json.loads(raw)
            item = body["items"][0] if path == "/api/jobs" else body
            assert item["solve_execution"] == execution
            assert item["channel_solve_executions"] == {"hf": execution}
            assert item["auto_export_formats"] == metadata["auto_export_formats"]
        execution["fallback_reason"] = "engine unavailable"
        status, raw = await _request(app, "GET", "/api/status/historical")
        assert status == 200
        assert json.loads(raw)["solve_execution"] == execution

    asyncio.run(exercise())


@pytest.mark.parametrize("path", [
    ("solve_execution", "accuracy"),
    ("solve_execution", "engine"),
    ("solve_execution", "formulation"),
    ("channel_solve_executions", "hf", "formulation"),
    ("design_availability", "reason"),
    ("design_availability", "note"),
])
def test_missing_fixed_metadata_members_are_rejected(path):
    execution = {"accuracy": "fast", "engine": "dryrun", "formulation": None}
    wire = JobRuntime._serialize_job(_row(metadata={
        "solve_execution": execution, "channel_solve_executions": {"hf": execution},
    }))
    wire = deepcopy(wire)
    target = wire
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    with pytest.raises(ValidationError) as caught:
        JobItem.model_validate(wire)
    assert path in {error["loc"] for error in caught.value.errors()}


def test_schema_uses_response_option_requirements_without_changing_requests():
    schema = JobItem.model_json_schema()
    assert set(NULLABLE_FIELDS) <= set(schema["required"])
    assert {"solve_execution", "channel_solve_executions", "design_availability", "client_metadata"} <= set(schema["required"])
    response_options = schema["$defs"]["SolveOptionsResponse"]
    assert set(OPTION_FIELDS) == set(response_options["required"])
    assert set(response_options["properties"]) == set(response_options["required"])
    assert set(schema["$defs"]["DesignAvailability"]["properties"]) == set(
        schema["$defs"]["DesignAvailability"]["required"]
    )
    assert not SolveOptions.model_json_schema().get("required")
    request = SolveRequest.model_json_schema()
    assert not request["$defs"]["SolveOptions"].get("required")
    assert schema["properties"]["auto_export_formats"]["additionalProperties"] is True
