"""Solve-job gmsh mesh builds must not block the FastAPI event loop.

A gmsh mesh build runs for multiple seconds. Executed inline in the job
coroutine it froze every HTTP response (status polling, job list, viewport)
until the build finished; pushed through asyncio.to_thread it would hop
between pool threads, which the gmsh Python API does not tolerate. All gmsh
work therefore funnels through one persistent worker thread
(services/gmsh_worker.py). These tests pin that contract:

* /api/status answers over live HTTP in under 100 ms while a solve-job mesh
  build is in flight;
* concurrently running jobs serialize their mesh builds on the single worker
  thread, without overlap, and still both complete;
* a real gmsh build executes correctly on the persistent non-main worker
  thread (the core assumption of the worker design).
"""

import asyncio
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

import uvicorn
from fastapi import FastAPI

import api.routes_simulation as _routes
import services.job_runtime as _jrt
import services.simulation_runner as _runner
from api.routes_simulation import router as simulation_router
from db import SimulationDB
from services.gmsh_worker import GMSH_WORKER_THREAD_NAME, run_on_gmsh_worker

# Known-good bare waveguide parameters (same values as the bare half-model
# mesh test); the real-gmsh test builds them, the fake tests just carry them
# through request validation.
_WAVEGUIDE_PARAMS = {
    "formula_type": "OSSE",
    "L": "80",
    "r0": 12.7,
    "a": "40",
    "a0": 10.0,
    "k": 1.0,
    "n": 4.0,
    "q": 0.99,
    "s": "0.6",
    "n_angular": 32,
    "n_length": 8,
    "throat_res": 8.0,
    "mouth_res": 18.0,
    "rear_res": 24.0,
    "wall_thickness": 0.0,
    "enc_depth": 0.0,
    "source_shape": 2,
}


def _solve_request_dump() -> dict:
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
        "num_frequencies": 4,
        "sim_type": "2",
        "options": {
            "mesh": {
                "strategy": "hornlab_mesher",
                "waveguide_params": dict(_WAVEGUIDE_PARAMS),
            }
        },
        "polar_config": None,
        "use_optimized": True,
        "verbose": False,
        "mesh_validation_mode": "warn",
        "frequency_spacing": "log",
        "device_mode": "auto",
    }


def _fake_mesh_result() -> dict:
    return {
        "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
        "stats": {
            "vertexCount": 3,
            "triangleCount": 1,
            "tagCounts": {"1": 0, "2": 1, "3": 0, "4": 0},
            "units": "m",
            "source": "hornlab_waveguide_mesher",
            "generatedBy": "hornlab-waveguide-mesher",
        },
        "canonical_mesh": {
            "vertices": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            "indices": [0, 1, 2],
            "surfaceTags": [2],
            "metadata": {},
        },
    }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _make_test_app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(simulation_router)
    return test_app


class _LoopbackServer:
    """Run uvicorn in a background thread on a loopback port."""

    def __init__(self, app: FastAPI):
        self._config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=_free_port(),
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(self._config)
        self.port = self._config.port
        self._thread = threading.Thread(
            target=self.server.run, name="uvicorn-test-server", daemon=True
        )

    def __enter__(self) -> "_LoopbackServer":
        self._thread.start()
        deadline = time.monotonic() + 10.0
        while not self.server.started:
            if not self._thread.is_alive():
                raise RuntimeError("test server thread exited before startup")
            if time.monotonic() > deadline:
                raise RuntimeError("test server did not start within 10 s")
            time.sleep(0.01)
        return self

    def __exit__(self, *_exc) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=10.0)


class _SolveJobHarnessTest(unittest.TestCase):
    """Job runtime on a temp DB with fake mesher/solver, served over HTTP."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        _jrt.jobs.clear()
        _jrt.job_queue.clear()
        _jrt.running_jobs.clear()
        _jrt.scheduler_loop_running = False
        self.addCleanup(_jrt.jobs.clear)
        self.addCleanup(_jrt.job_queue.clear)
        self.addCleanup(_jrt.running_jobs.clear)

        test_db = SimulationDB(Path(self.tmp.name) / "simulations.db")
        self._start_patch(_jrt, "db", test_db)
        # simulation_runner binds the db object at import time; patch its copy.
        self._start_patch(_runner, "db", test_db)
        self._start_patch(_jrt, "db_initialized", False)

        def _fake_resolve(backend, mesh_strategy=None):
            return "metal"

        def _fake_solve(
            msh_path, request, progress_callback=None, stage_callback=None,
            source_motion=None,
        ):
            return {"frequencies": [], "metadata": {}}

        for target, name, value in (
            (_routes, "HORNLAB_MESHER_AVAILABLE", True),
            (_routes, "HORNLAB_MESHER_RUNTIME_READY", True),
            (_routes, "build_waveguide_mesh", _fake_mesh_result),
            (_routes, "resolve_solver_backend", _fake_resolve),
            (_routes, "metal_backend_status", dict),
            (_routes, "is_metal_fast_solve_ready", lambda status: True),
            (_runner, "HORNLAB_MESHER_AVAILABLE", True),
            (_runner, "HORNLAB_MESHER_RUNTIME_READY", True),
            (_runner, "resolve_solver_backend", _fake_resolve),
            (_runner, "solve_metal_from_msh", _fake_solve),
        ):
            self._start_patch(target, name, value)

    def _start_patch(self, target, name, value):
        patcher = patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _install_fake_build(self, fake_build):
        self._start_patch(_runner, "build_waveguide_mesh", fake_build)

    # ── HTTP helpers ────────────────────────────────────────────────────────

    def _submit_job(self, port: int) -> str:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/solve",
            data=json.dumps(_solve_request_dump()).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10.0) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body["job_id"]

    def _get_status(self, port: int, job_id: str) -> tuple[float, dict]:
        started = time.monotonic()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/status/{job_id}", timeout=10.0
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
        return time.monotonic() - started, body

    def _wait_terminal(self, port: int, job_id: str, deadline_s: float = 20.0) -> dict:
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            _latency, body = self._get_status(port, job_id)
            if body["status"] in {"complete", "error", "cancelled"}:
                return body
            time.sleep(0.02)
        raise AssertionError(f"job {job_id} did not reach a terminal state")

    # ── Tests ───────────────────────────────────────────────────────────────

    def test_status_stays_responsive_while_mesh_build_is_in_flight(self):
        build_started = threading.Event()
        release_build = threading.Event()
        build_finished = threading.Event()

        def fake_build(payload, *, include_canonical=False, cancellation_callback=None):
            build_started.set()
            try:
                # An inline (event-loop-blocking) build would freeze every
                # status request below until this timeout expires.
                release_build.wait(timeout=8.0)
            finally:
                build_finished.set()
            return _fake_mesh_result()

        self._install_fake_build(fake_build)

        with _LoopbackServer(_make_test_app()) as server:
            job_id = self._submit_job(server.port)
            self.assertTrue(
                build_started.wait(timeout=10.0), "mesh build never started"
            )

            latencies = []
            samples = []
            for _ in range(6):
                latency, body = self._get_status(server.port, job_id)
                latencies.append(latency)
                samples.append((body.get("status"), body.get("stage")))
                time.sleep(0.02)
            sampled_in_flight = not build_finished.is_set()

            release_build.set()
            final = self._wait_terminal(server.port, job_id)

        self.assertLess(
            max(latencies),
            0.1,
            f"/api/status stalled while the mesh build was in flight: {latencies}",
        )
        self.assertTrue(
            sampled_in_flight, "latency samples were not taken during the build"
        )
        self.assertEqual(samples, [("running", "mesh_prepare")] * len(samples))
        self.assertEqual(final["status"], "complete", final)

    def test_concurrent_jobs_serialize_mesh_builds_on_one_worker_thread(self):
        records = []
        records_lock = threading.Lock()
        first_build_started = threading.Event()
        concurrency_observed = threading.Event()

        def fake_build(payload, *, include_canonical=False, cancellation_callback=None):
            entered = time.monotonic()
            first_build_started.set()
            # The first build holds the worker until the test has confirmed
            # both jobs are running; the second build sees the event already
            # set and passes straight through.
            concurrency_observed.wait(timeout=8.0)
            time.sleep(0.05)
            with records_lock:
                records.append(
                    {
                        "ident": threading.get_ident(),
                        "name": threading.current_thread().name,
                        "start": entered,
                        "end": time.monotonic(),
                    }
                )
            return _fake_mesh_result()

        self._install_fake_build(fake_build)
        # Let the scheduler admit both jobs at once so the serialization under
        # test comes from the gmsh worker, not from the FIFO job queue.
        self._start_patch(_jrt, "max_concurrent_jobs", 2)

        with _LoopbackServer(_make_test_app()) as server:
            job_a = self._submit_job(server.port)
            job_b = self._submit_job(server.port)
            self.assertTrue(
                first_build_started.wait(timeout=10.0), "no mesh build started"
            )

            deadline = time.monotonic() + 5.0
            while True:
                _latency, body_a = self._get_status(server.port, job_a)
                _latency, body_b = self._get_status(server.port, job_b)
                if body_a["status"] == "running" and body_b["status"] == "running":
                    break
                if time.monotonic() > deadline:
                    self.fail(
                        "jobs were not admitted concurrently: "
                        f"{body_a['status']} / {body_b['status']}"
                    )
                time.sleep(0.01)
            concurrency_observed.set()

            final_a = self._wait_terminal(server.port, job_a)
            final_b = self._wait_terminal(server.port, job_b)

        self.assertEqual(final_a["status"], "complete", final_a)
        self.assertEqual(final_b["status"], "complete", final_b)

        self.assertEqual(len(records), 2, records)
        idents = {record["ident"] for record in records}
        self.assertEqual(
            len(idents), 1, f"mesh builds ran on multiple threads: {records}"
        )
        self.assertNotIn(
            threading.main_thread().ident,
            idents,
            "mesh builds must not run on the main thread",
        )
        for record in records:
            self.assertTrue(
                record["name"].startswith(GMSH_WORKER_THREAD_NAME), record
            )
        earlier, later = sorted(records, key=lambda record: record["start"])
        self.assertLessEqual(
            earlier["end"],
            later["start"],
            f"mesh builds overlapped in time: {records}",
        )


def _mesher_runtime_ready() -> bool:
    try:
        from solver.mesher_adapter import build_waveguide_mesh
    except Exception:  # pragma: no cover - adapter import guarded below
        return False
    if build_waveguide_mesh is None:
        return False
    try:
        from solver_bootstrap import (
            HORNLAB_MESHER_AVAILABLE,
            HORNLAB_MESHER_RUNTIME_READY,
        )
    except Exception:
        return False
    return bool(HORNLAB_MESHER_AVAILABLE and HORNLAB_MESHER_RUNTIME_READY)


@unittest.skipUnless(_mesher_runtime_ready(), "hornlab-waveguide-mesher runtime not available")
class GmshWorkerRealBuildTest(unittest.TestCase):
    """Real gmsh builds must succeed on the persistent worker thread.

    Guards the core assumption of the worker design: gmsh accepts a non-main
    thread as long as it is always the same thread. Two builds submitted
    concurrently must both produce valid tagged meshes.
    """

    def test_two_real_builds_share_the_worker_thread_and_stay_valid(self):
        from contracts import WaveguideParamsRequest
        from solver.mesher_adapter import build_waveguide_mesh

        payload = WaveguideParamsRequest(**_WAVEGUIDE_PARAMS).model_dump()
        payload["quadrants"] = "12"

        build_threads = []

        def build_and_record(build_payload):
            build_threads.append(threading.current_thread())
            return build_waveguide_mesh(build_payload, include_canonical=False)

        async def _run_two_builds():
            return await asyncio.gather(
                run_on_gmsh_worker(build_and_record, dict(payload)),
                run_on_gmsh_worker(build_and_record, dict(payload)),
            )

        first, second = asyncio.run(_run_two_builds())

        for result in (first, second):
            self.assertTrue(result["msh_text"].strip())
            self.assertGreater(int(result["stats"]["tagCounts"]["2"]), 0)
        self.assertEqual(len(build_threads), 2)
        self.assertEqual(len({thread.ident for thread in build_threads}), 1)
        self.assertIsNot(build_threads[0], threading.main_thread())
        self.assertTrue(build_threads[0].name.startswith(GMSH_WORKER_THREAD_NAME))


if __name__ == "__main__":
    unittest.main()
