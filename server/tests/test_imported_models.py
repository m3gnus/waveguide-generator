"""Shared setup revisions retain file models without inventing CAD projects."""

import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from server.cadlink.store import CadLinkStore
from server.jobs.imported_models import mount_imported_models, setup_owner
from server.jobs.imported_setup import setup_content, setup_digest, validate_setup
from server.jobs.runtime import ImportedSolveRefusal, JobRuntime
from server.jobs.store import JobStore
from test_imported_jobs import _AlwaysRegistry
from test_jobs_api import _request
from test_mesh_import import register_mesh, registration, request


@pytest.mark.parametrize("findings", [False, True])
def test_saved_file_setup_survives_restart_without_a_job(tmp_path, findings):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        record = register_mesh(store, tmp_path, registration())
        if findings:
            original = record
            row = store.allocate_ingest(
                manifest_sha256=record["manifest_sha256"],
                artifact_sha256=record["artifact_sha256"],
                record_builder=lambda ingest_id, created_at: json.dumps(
                    {
                        **original,
                        "ingest_id": ingest_id,
                        "created_at": created_at,
                        "report_sha256": "sha256:review",
                        "findings": [{"id": "review", "blocking": True}],
                    }
                ),
            )
            record = json.loads(row["record_json"])
        runtime = JobRuntime(
            JobStore.for_data_dir(tmp_path),
            cadlink_store=store,
            engine_registry=_AlwaysRegistry(SimpleNamespace(name="metal")),
        )
        runtime.store.initialize()
        app = FastAPI()
        app.state.cadlink_store, app.state.jobs_runtime = store, runtime
        mount_imported_models(app)
        geometry = request(record).geometry.model_dump(mode="json", exclude_none=True)
        for field in (
            "type",
            "ingest_id",
            "manifest_sha256",
            "artifact_sha256",
            "acknowledged_findings",
        ):
            geometry.pop(field, None)
        setup = {
            "geometry": geometry,
            "options": {
                "engine": "metal",
                "frequency_range": [250, 8000],
                "num_frequencies": 17,
                "polar_config": {"angle_range": [-180, 180, 73]},
            },
        }
        path = f"/api/imported-models/{record['ingest_id']}/setup"
        try:
            status, raw = await _request(app, "PUT", path, body=setup)
            assert status == 200, raw
            assert (await _request(app, "PUT", path, body=setup))[1] == raw
            expected = setup_digest(validate_setup(setup))
            owner, inventory = setup_owner(store, record)
            assert owner == "mesh-file:" + record["ingest_id"]
            revision = store.get_setup_revision(
                store.get_model_setup(owner, inventory)["revision_id"]
            )
            assert revision["content_sha256"] == expected
            assert runtime.store.list_jobs()[1] == 0
            if findings:
                with pytest.raises(ImportedSolveRefusal, match="unacknowledged_findings"):
                    await runtime._prepare_imported_submission(request(record))
            store.close()
            store.initialize()
            status, raw = await _request(app, "GET", path)
            assert status == 200 and b"8000" in raw and b"17" in raw
            invalid = {
                **setup,
                "geometry": {
                    **geometry,
                    "drive_channels": [{"id": "wrong", "source_ids": ["wrong"]}],
                },
            }
            assert (await _request(app, "PUT", path, body=invalid))[0] == 422
            assert (
                store.get_setup_revision(store.get_model_setup(owner, inventory)["revision_id"])[
                    "content_sha256"
                ]
                == expected
            )
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_shared_setup_keeps_legacy_schema_bytes_and_imports():
    from server.cadlink.joblane.setup import CadSolveSetup as LegacySetup
    from server.jobs.imported_setup import ImportedSolveSetup

    assert LegacySetup is ImportedSolveSetup
    setup = validate_setup(
        {
            "geometry": {"drive_channels": [{"id": "source", "source_ids": ["source"]}]},
            "options": {"frequencies_hz": [100, 200]},
            "label": "Métré",
        }
    )
    content = setup_content(setup)
    assert '"origin"' not in content and "Métré" in content
    assert (
        setup_digest(setup)
        == "sha256:" + __import__("hashlib").sha256(content.encode()).hexdigest()
    )
