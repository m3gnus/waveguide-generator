"""Opt-in tiny real Metal solve through the complete durable job pipeline."""

from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path

import pytest

from server.jobs.models import SolveRequest
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore


# Batch Q's path discipline does not permit a repository-level pytest.ini edit.
# Register the opt-in marker before applying it so default/full runs stay clean.
_pytest_config = getattr(pytest.mark, "_config", None)
if _pytest_config is not None:
    _pytest_config.addinivalue_line("markers", "live: real native solver qualification")


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("WG2_RUN_LIVE") != "1",
    reason="set WG2_RUN_LIVE=1 and select -m live for native Metal qualification",
)
def test_tiny_osse_freestanding_metal_full_pipeline(tmp_path: Path) -> None:
    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "L": 60,
                "a": 30,
                "a0": 10,
                "r0": 10,
                "k": 1,
                "n": 4,
                "q": 0.99,
                "s": 0.8,
                "mesh": {
                    "angular_segments": 12,
                    "length_segments": 4,
                    "throat_resolution": 8,
                    "mouth_resolution": 15,
                    "quadrants": 1,
                    "wall_thickness": 2,
                    "max_triangles": 50000,
                },
                "source": {"shape": 2, "radius": -1, "curvature": 0, "velocity": 1},
                "simulation": {
                    "f1": 500,
                    "f2": 1000,
                    "num_frequencies": 2,
                    "sim_type": "freestanding",
                },
            },
            "options": {
                "engine": "metal",
                "frequency_range": [500, 1000],
                "num_frequencies": 2,
                "stage_delay_ms": 0,
            },
        }
    )

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
        job_id = await runtime.submit(request)
        await runtime.wait_idle(timeout=120.0)
        job = await runtime.get_job(job_id)
        assert job["status"] == "complete", job["error_message"]
        assert job["has_mesh_artifact"] is True
        results = await runtime.get_results(job_id)
        assert results["frequencies"] == [500.0, 1000.0]
        # This deliberately tiny fixture (60 mm horn, quadrant domain, 1 m/s^2
        # acceleration drive) legitimately lands near 0 dB absolute SPL at 1 m
        # (overseer-measured [-0.28, 8.10] dB on 2026-08-03), so assert
        # finiteness and sane magnitude rather than a loudspeaker-scale window.
        assert all(value is None or (math.isfinite(value) and abs(value) < 200.0) for value in results["spl_on_axis"]["spl"])
        assert any(value is not None for value in results["spl_on_axis"]["spl"])
        assert all(value is None or math.isfinite(value) for value in results["impedance"]["real"])
        assert results["metadata"]["solver_backend"] == "metal"
        assert results["metadata"]["phase_time_convention"] == "exp(+ikr)"
        await runtime.shutdown()

    asyncio.run(scenario())
