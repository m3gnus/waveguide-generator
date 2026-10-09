"""Native prescribed-source contracts cannot silently absorb voltage drivers."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from server.contracts.source_contour import validate_contour_request
from server.integration.provenance import canonical_json_sha256
from server.jobs.models import DriveChannel, SolveRequest
from server.jobs.runtime import JobRuntime, _submission_identity
from server.jobs.store import JobStore, SubmissionConflictError
from server.tests.beat_adapter.test_transducer_jobs import PARAMETERS
from server.tests.test_imported_jobs import _PausedRegistry
from server.tests.test_jobs_store import _job


def channel_wire():
    return dict(id="motor", source_ids=["piston"], motion="axial",
                physical_source_id="diaphragm", patch_weights={"piston": -0.5})


def test_weighted_native_channel_refuses_coupled_transducer():
    with pytest.raises(ValidationError, match="weighted prescribed patches"):
        DriveChannel.model_validate({**channel_wire(), "exterior_transducer": PARAMETERS})


def test_native_authority_refuses_post_validation_transducer_mutation():
    wire = channel_wire()
    channel = DriveChannel.model_validate(wire)
    channel.exterior_transducer = PARAMETERS  # Mutable callers must be revalidated at dispatch.
    excitation = dict(channel_id=wire["id"], weights=wire["patch_weights"], motion="axial")
    digest = hashlib.sha256(json.dumps(excitation, sort_keys=True,
                            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    record = dict(native_source=dict(required_features=["native-source-contour-v1"],
                  channel=wire, excitation_sha256="sha256:" + digest))
    geometry = SimpleNamespace(required_features=["native-source-contour-v1"],
                               drive_channels=[channel], skipped_source_ids=[])
    with pytest.raises(ValueError, match="contradicts authoritative"):
        validate_contour_request(geometry, record)


def test_ordinary_coupled_transducer_still_admitted():
    channel = DriveChannel.model_validate(dict(id="driver", source_ids=["front", "rear"],
                               motion="axial", exterior_transducer=PARAMETERS))
    geometry = SimpleNamespace(required_features=[], drive_channels=[channel])
    validate_contour_request(geometry, {})
    assert channel.exterior_transducer is not None


def pre_native_replay():
    return json.loads((Path(__file__).parent / "fixtures" /
                       "pre-native-imported-replay.json").read_text())


def test_pre_native_imported_submission_replays_existing_job(tmp_path):
    frozen = pre_native_replay()
    request = SolveRequest.model_validate(frozen["request"])
    assert _submission_identity(request) == frozen["submission_identity"]
    assert canonical_json_sha256(_submission_identity(request)) == frozen["request_sha256"]

    async def scenario():
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        job = _job("existing-imported", status="complete")
        job["config_json"] = frozen["request"]
        store.create_job_idempotent(job, submission_key=request.client_request_id,
                                   request_sha256=frozen["request_sha256"])
        runtime = JobRuntime(store, engine_registry=_PausedRegistry())
        try:
            assert await runtime.submit(request) == "existing-imported"
            request.options.frequencies_hz = [123.0]
            with pytest.raises(SubmissionConflictError, match="different request"):
                await runtime.submit(request)
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("features", [
    ["native-source-contour-v1"],
    ["native-source-contour-v1", "native-front-baffle-woofer-v1"],
    ["native-source-contour-v1", "native-shared-horn-woofer-v1"],
])
def test_native_features_remain_serialized_and_distinguish_identity(features):
    frozen = pre_native_replay()
    wire = frozen["request"]
    wire["geometry"]["required_features"] = features
    request = SolveRequest.model_validate(wire)
    assert request.model_dump(mode="json")["geometry"]["required_features"] == features
    assert _submission_identity(request)["geometry"]["required_features"] == features
    assert canonical_json_sha256(_submission_identity(request)) != frozen["request_sha256"]
