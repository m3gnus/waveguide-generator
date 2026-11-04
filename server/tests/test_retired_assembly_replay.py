"""Retiring authoring must leave stored native assembly results readable."""

import asyncio
import json

from server.app import create_app
from server.tests.test_jobs_api import _request_with_headers


def test_saved_assembly_results_replay_exact_bytes_without_rebuilding(tmp_path, monkeypatch):
    from hornlab_mesher.source_assembly import SourceAssembly

    app = create_app(data_dir=tmp_path)
    store = app.state.jobs_runtime.store
    store.initialize()
    job_id = "legacy-assembly"
    at = "2026-10-20T12:00:00"
    store.create_job({
        "id": job_id, "status": "complete", "created_at": at, "updated_at": at,
        "queued_at": at, "completed_at": at, "progress": 1.0,
        "stage": "complete", "stage_message": "Complete",
        "config_json": {
            "geometry": {"type": "imported", "ingest_id": "wgi_old_assembly",
                         "required_features": ["native-shared-horn-woofer-v1"]},
            "options": {"engine": "metal"},
        },
        "config_summary_json": {"geometry_type": "imported", "import_origin": "native"},
        "task_metadata": {},
    })
    results = {
        "frequencies": [100, 200], "result_kind": "imported",
        "drive_channels": [{"id": "hf", "source_ids": ["horn/piston"]},
                           {"id": "lf", "source_ids": ["woofer/piston"]}],
        "spl_on_axis": [82.125, 85.375],
    }
    store.store_results(job_id, results)
    expected, digest = store.get_results_payload(job_id)

    def refuse_rebuild(*args, **kwargs):
        raise AssertionError("Reading saved results must not rebuild the assembly")

    monkeypatch.setattr(SourceAssembly, "from_dict", refuse_rebuild)

    async def scenario():
        try:
            status, content, headers = await _request_with_headers(
                app, "GET", f"/api/results/{job_id}"
            )
            assert status == 200, content
            assert content == expected.encode("utf-8")
            replayed = json.loads(content)
            assert all(replayed[key] == value for key, value in results.items())
            assert headers["x-wg-results-sha256"] == digest
            assert headers["etag"] == f'"sha256:{digest}"'
        finally:
            await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())
