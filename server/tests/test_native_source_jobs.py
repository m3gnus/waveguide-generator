import asyncio

import pytest

from server.cadlink.store import CadLinkStore
from server.jobs.runtime import JobRuntime, ImportedSolveRefusal
from server.jobs.store import JobStore
from server.tests.test_imported_jobs import _PausedRegistry
from server.tests.test_native_source_contour import artifacts as artifacts, ingest, request


def test_real_job_submission_and_refusal(artifacts, tmp_path):
    record = ingest(artifacts[0], tmp_path)

    async def scenario():
        store = CadLinkStore(tmp_path / "registry.sqlite")
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.sqlite"),
            engine_registry=_PausedRegistry(),
            cadlink_store=store,
        )
        try:
            bad = request(record)
            bad.geometry.drive_channels[0].patch_weights["piston"] = 0.4
            with pytest.raises(ImportedSolveRefusal, match="contradicts"):
                await runtime.submit(bad)
            req = request(record)
            job_id = await runtime.submit(req)
            row = runtime.store.get_job_row(job_id)
            assert row["config_json"]["geometry"]["drive_channels"][0]["patch_weights"] == {
                "piston": 1
            }
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())
