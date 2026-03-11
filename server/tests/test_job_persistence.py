import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from api.routes_simulation import (
    clear_failed_jobs,
    delete_job,
    get_mesh_artifact,
    get_results,
    list_jobs,
)
import services.job_runtime as _jrt
from contracts import SimulationRequest
from db import SimulationDB
from fastapi import HTTPException


class JobPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = _jrt.db
        self.original_db_initialized = _jrt.db_initialized

        _jrt.jobs.clear()
        _jrt.job_queue.clear()
        _jrt.running_jobs.clear()
        _jrt.scheduler_loop_running = False

        new_db = SimulationDB(Path(self.tmp.name) / "simulations.db")
        _jrt.db = new_db
        _jrt.db_initialized = False
        _jrt.ensure_db_ready()

    def tearDown(self):
        _jrt.db = self.original_db
        _jrt.db_initialized = self.original_db_initialized
        _jrt.jobs.clear()
        _jrt.job_queue.clear()
        _jrt.running_jobs.clear()
        _jrt.scheduler_loop_running = False
        self.tmp.cleanup()

    def _request_dump(self):
        return {
            "mesh": {
                "vertices": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                "indices": [0, 1, 2],
                "surfaceTags": [2],
                "format": "msh",
                "boundaryConditions": {},
                "metadata": {},
            },
            "frequency_range": [100.0, 1000.0],
            "num_frequencies": 8,
            "sim_type": "2",
            "options": {},
            "polar_config": None,
            "use_optimized": True,
            "enable_symmetry": True,
            "verbose": True,
            "mesh_validation_mode": "warn",
            "frequency_spacing": "log",
            "device_mode": "auto",
        }

    def _create_db_job(self, job_id: str, status: str):
        now = "2026-02-22T18:20:31.305018"
        _jrt.db.create_job(
            {
                "id": job_id,
                "status": status,
                "created_at": now,
                "updated_at": now,
                "queued_at": now,
                "started_at": now if status == "running" else None,
                "completed_at": now if status in {"complete", "error", "cancelled"} else None,
                "progress": 0.0,
                "stage": status,
                "stage_message": f"{status} stage",
                "error_message": None,
                "cancellation_requested": False,
                "config_json": self._request_dump(),
                "config_summary_json": {
                    "formula_type": "OSSE",
                    "frequency_range": [100.0, 1000.0],
                    "num_frequencies": 8,
                    "sim_type": "2",
                },
                "has_results": status == "complete",
                "has_mesh_artifact": False,
                "label": None,
            }
        )

    def test_startup_recovery_marks_running_error_and_requeues_queued(self):
        self._create_db_job("job-running", "running")
        self._create_db_job("job-queued", "queued")

        with patch("services.job_runtime.asyncio.create_task") as create_task:
            create_task.side_effect = lambda coro: (coro.close(), None)[1]
            asyncio.run(_jrt.startup_jobs_runtime())

        recovered_running = _jrt.db.get_job_row("job-running")
        self.assertEqual(recovered_running["status"], "error")
        self.assertEqual(recovered_running["error_message"], "Server restarted during execution")

        recovered_queued = _jrt.db.get_job_row("job-queued")
        self.assertEqual(recovered_queued["status"], "queued")
        self.assertIn("job-queued", list(_jrt.job_queue))

    def test_create_simulation_job_persists_cache_and_scheduler_trigger(self):
        request = SimulationRequest(**self._request_dump())
        job_id = "11111111-1111-1111-1111-111111111111"

        with patch("services.job_runtime.uuid.uuid4", return_value=uuid.UUID(job_id)), patch(
            "services.job_runtime.asyncio.create_task"
        ) as create_task:
            create_task.side_effect = lambda coro: (coro.close(), None)[1]
            created_job_id = _jrt.create_simulation_job(request)

        self.assertEqual(created_job_id, job_id)
        self.assertIn(job_id, _jrt.jobs)
        self.assertIn(job_id, list(_jrt.job_queue))

        row = _jrt.db.get_job_row(job_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["config_json"]["mesh"]["surfaceTags"], [2])
        create_task.assert_called_once()

    def test_delete_job_rejects_active_and_allows_terminal(self):
        self._create_db_job("job-active", "queued")
        self._create_db_job("job-complete", "complete")

        _jrt.jobs["job-active"] = {"id": "job-active", "status": "queued"}
        _jrt.jobs["job-complete"] = {"id": "job-complete", "status": "complete"}

        with self.assertRaises(HTTPException) as active_ctx:
            asyncio.run(delete_job("job-active"))
        self.assertEqual(active_ctx.exception.status_code, 409)

        resp = asyncio.run(delete_job("job-complete"))
        self.assertEqual(resp["deleted"], True)
        self.assertIsNone(_jrt.db.get_job_row("job-complete"))

    def test_clear_failed_jobs_deletes_failed_from_db_and_runtime_cache(self):
        self._create_db_job("job-error-1", "error")
        self._create_db_job("job-error-2", "error")
        self._create_db_job("job-complete", "complete")

        _jrt.jobs["job-error-1"] = {"id": "job-error-1", "status": "error"}
        _jrt.jobs["job-error-2"] = {"id": "job-error-2", "status": "error"}
        _jrt.jobs["job-complete"] = {"id": "job-complete", "status": "complete"}

        resp = asyncio.run(clear_failed_jobs())
        self.assertEqual(resp["deleted"], True)
        self.assertEqual(resp["deleted_count"], 2)
        self.assertCountEqual(resp["deleted_ids"], ["job-error-1", "job-error-2"])

        self.assertIsNone(_jrt.db.get_job_row("job-error-1"))
        self.assertIsNone(_jrt.db.get_job_row("job-error-2"))
        self.assertIsNotNone(_jrt.db.get_job_row("job-complete"))

        self.assertNotIn("job-error-1", _jrt.jobs)
        self.assertNotIn("job-error-2", _jrt.jobs)
        self.assertIn("job-complete", _jrt.jobs)

    def test_list_jobs_supports_status_filter_and_pagination(self):
        self._create_db_job("job-1", "complete")
        self._create_db_job("job-2", "error")
        self._create_db_job("job-3", "queued")

        resp = asyncio.run(list_jobs(status="complete,error", limit=1, offset=0))
        self.assertEqual(resp["limit"], 1)
        self.assertEqual(resp["offset"], 0)
        self.assertEqual(resp["total"], 2)
        self.assertEqual(len(resp["items"]), 1)
        self.assertIn(resp["items"][0]["status"], {"complete", "error"})

    def test_results_and_mesh_artifact_loaded_from_sqlite(self):
        self._create_db_job("job-finished", "complete")
        _jrt.db.store_results("job-finished", {"frequencies": [100], "directivity": {}})
        _jrt.db.store_mesh_artifact("job-finished", "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n")

        _jrt.jobs.clear()
        results = asyncio.run(get_results("job-finished"))
        self.assertEqual(results["frequencies"], [100])

        mesh_resp = asyncio.run(get_mesh_artifact("job-finished"))
        self.assertIn("$MeshFormat", mesh_resp.body.decode())


if __name__ == "__main__":
    unittest.main()
