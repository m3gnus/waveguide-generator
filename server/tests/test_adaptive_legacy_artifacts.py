"""Byte fixtures captured by executing base 44226173, not stripping new fields."""

import json
from pathlib import Path

import pytest

from server.engines.registry import SELECTABLE_ENGINE_NAMES
from server.integration.provenance import enrich_result_contract
from server.jobs.models import JobItem, JobStatusResponse, SolveRequest
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore


FIXTURE = Path(__file__).parent / "fixtures" / "adaptive-legacy-artifacts.json"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def artifacts(directory, engine, *, explicit_off=False):
    wire = {
        "design": {"formula": "OSSE", "L": 120, "a": 45,
                   "enclosure": {"depth": 0}, "simulation": {"sim_type": "freestanding"}},
        "options": {"engine": engine}, "client_request_id": "legacy-adaptive-replay",
    }
    if explicit_off:
        wire["options"]["adaptive_frequency_sampling"] = False
    request = SolveRequest.model_validate(wire)
    store = JobStore(directory / f"{engine}.db")
    store.initialize()
    try:
        store.create_job({
            "id": "legacy", "status": "queued", "progress": 0.0,
            "created_at": "2026-08-12T00:00:00", "queued_at": "2026-08-12T00:00:00",
            "updated_at": "2026-08-12T00:00:00", "config_json": request.model_dump(mode="json"),
            "config_summary_json": {}, "task_metadata": {},
        })
        row = store.get_job_row("legacy")
        response = JobItem.model_validate(JobRuntime._serialize_job(row))
        detailed = JobStatusResponse.model_validate(JobRuntime._serialize_job(row, detailed=True))
        provenance = enrich_result_contract({}, request)["provenance"]
        return {
            "model_json": request.model_dump_json(),
            "stored_config_json": store._connect().execute(
                "SELECT config_json FROM simulation_jobs WHERE id = 'legacy'"
            ).fetchone()[0],
            "response_json": response.model_dump_json(),
            "detailed_response_json": detailed.model_dump_json(),
            "provenance_digests": encoded({k: v for k, v in provenance.items() if k.endswith("sha256")}),
        }
    finally:
        store.close()


@pytest.mark.parametrize("engine", sorted(SELECTABLE_ENGINE_NAMES))
@pytest.mark.parametrize("explicit_off", [False, True])
def test_full_default_off_artifacts_match_base_bytes(tmp_path, engine, explicit_off):
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["base"] == "44226173"
    assert artifacts(tmp_path, engine, explicit_off=explicit_off) == fixture["artifacts"][engine]


def test_enabled_option_survives_every_model_encoding():
    request = SolveRequest.model_validate({
        "design": {"formula": "OSSE", "L": 120, "a": 45},
        "options": {"adaptive_frequency_sampling": True},
    })
    assert request.model_dump(mode="json")["options"]["adaptive_frequency_sampling"] is True
    assert json.loads(request.model_dump_json())["options"]["adaptive_frequency_sampling"] is True
