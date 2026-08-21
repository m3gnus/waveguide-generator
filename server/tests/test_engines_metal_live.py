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
                    "solver_mode": "full_3d",
                },
            },
            "options": {
                "engine": "metal",
                "solver_mode": "full_3d",
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
        assert results["metadata"]["solver_mode"] == "full_3d"
        assert results["metadata"]["solve_path"] == "full-3d"
        assert results["metadata"]["phase_time_convention"] == "exp(+ikr)"
        await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("WG2_RUN_LIVE") != "1",
    reason="set WG2_RUN_LIVE=1 and select -m live for native Metal qualification",
)
def test_explicit_axisymmetric_path_matches_explicit_full_3d(tmp_path: Path) -> None:
    def request_for(mode: str, symmetry: str) -> SolveRequest:
        return SolveRequest.model_validate(
            {
                "design": {
                    "formula": "OSSE",
                    "L": 80,
                    "a": 35,
                    "a0": 0,
                    "r0": 12.7,
                    "k": 1,
                    "n": 4,
                    "q": 0.995,
                    "s": 0,
                    "mesh": {
                        "angular_segments": 32,
                        "length_segments": 10,
                        "throat_resolution": 5,
                        "mouth_resolution": 10,
                        "rear_resolution": 10,
                        "quadrants": 1234,
                        "wall_thickness": 6,
                        "max_triangles": 50000,
                        "allow_large_mesh": 1,
                    },
                    "source": {"shape": 2, "velocity": 1},
                    "simulation": {
                        "f1": 1000,
                        "f2": 1001,
                        "num_frequencies": 1,
                        "sim_type": "freestanding",
                        "solver_mode": mode,
                    },
                },
                "options": {
                    "engine": "metal",
                    "solver_mode": mode,
                    "symmetry": symmetry,
                    "frequency_range": [1000, 1001],
                    "num_frequencies": 1,
                    "frequency_spacing": "linear",
                    "polar_config": {
                        "angle_range": [0, 180, 5],
                        "enabled_axes": ["horizontal"],
                        "distance": 2,
                    },
                    "stage_delay_ms": 0,
                },
            }
        )

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / "parity-jobs.db"))
        results = {}
        variants = {
            "axisymmetric": ("circsym", "full"),
            "full_3d": ("full_3d", "full"),
            "quarter_3d": ("full_3d", "quarter"),
        }
        for label, (mode, symmetry) in variants.items():
            job_id = await runtime.submit(request_for(mode, symmetry))
            await runtime.wait_idle(timeout=120.0)
            job = await runtime.get_job(job_id)
            assert job["status"] == "complete", job["error_message"]
            results[label] = await runtime.get_results(job_id)
        axisymmetric = results["axisymmetric"]
        full = results["full_3d"]
        quarter = results["quarter_3d"]
        assert axisymmetric["metadata"]["solve_path"] == "axisymmetric-meridian"
        assert full["metadata"]["solve_path"] == "full-3d"
        assert quarter["metadata"]["solve_path"] == "full-3d"
        assert full["metadata"]["symmetry"]["resolved_quadrants"] == 1234
        assert quarter["metadata"]["symmetry"]["resolved_quadrants"] == 1

        def assert_response_close(left, right, *, spl_db, phase_deg, pattern_db):
            left_spl = left["spl_on_axis"]["spl"][0]
            right_spl = right["spl_on_axis"]["spl"][0]
            assert left_spl is not None and right_spl is not None
            assert abs(left_spl - right_spl) < spl_db

            left_phase = left["spl_on_axis"]["phase_degrees"][0]
            right_phase = right["spl_on_axis"]["phase_degrees"][0]
            assert left_phase is not None and right_phase is not None
            phase_delta = abs(
                (left_phase - right_phase + 180.0) % 360.0 - 180.0
            )
            assert phase_delta < phase_deg

            left_pattern = left["directivity"]["horizontal"][0][0]
            right_pattern = right["directivity"]["horizontal"][0][0]
            pairs = [
                (left, right)
                for left, right in zip(left_pattern, right_pattern, strict=True)
                if left is not None and right is not None
            ]
            assert pairs
            assert max(abs(left - right) for left, right in pairs) < pattern_db

        assert_response_close(
            axisymmetric,
            full,
            spl_db=1.0,
            phase_deg=5.0,
            pattern_db=1.0,
        )
        assert_response_close(
            full,
            quarter,
            spl_db=0.2,
            phase_deg=1.0,
            pattern_db=0.2,
        )
        await runtime.shutdown()

    asyncio.run(scenario())
