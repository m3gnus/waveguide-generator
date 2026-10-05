"""A small real infinite-baffle solve through the WG job path, on live Metal.

Opt-in (WG2_RUN_LIVE=1, -m live); skipped where the native Metal backend cannot run. It pins the application-path
contract of real k: the formulation the native solver executed is the one the
result records (metadata, execution summary, power-qualification provenance),
and the answer is a sane forward-radiating half-space field.

Under heavy host load metal-bem's native batch solve has been seen to abort
with a NaN ("NaN in JSON write"). That is a separate, known threading issue; a
failure with that message means rerun on a quieter machine, not a formulation
regression.
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
from pathlib import Path

import pytest

from server.jobs.models import SolveRequest
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore


# Opt-in like test_engines_metal_live.py: nothing probes Metal at collection.
_pytest_config = getattr(pytest.mark, "_config", None)
if _pytest_config is not None:
    _pytest_config.addinivalue_line("markers", "live: real native solver qualification")


def _live_enabled() -> bool:
    return os.environ.get("WG2_RUN_LIVE") == "1"


def _metal_available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        from server.solver import metal

        return bool(metal.metal_status().get("available"))
    except Exception:  # noqa: BLE001 - any probe failure means "cannot run here"
        return False


pytestmark = [
    pytest.mark.live,
    pytest.mark.real_runtime,
    pytest.mark.skipif(
        not _live_enabled(),
        reason="set WG2_RUN_LIVE=1 and select -m live for native Metal qualification",
    ),
]


@pytest.fixture(autouse=True)
def _require_metal() -> None:
    if not _metal_available():
        pytest.skip("native Metal backend is not available on this host")


def _request(sim_type: str) -> SolveRequest:
    return SolveRequest.model_validate(
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
                    "f2": 2000,
                    "num_frequencies": 3,
                    "sim_type": sim_type,
                    "solver_mode": "full_3d",
                },
            },
            "options": {
                "engine": "metal",
                "solver_mode": "full_3d",
                "frequency_range": [500, 2000],
                "num_frequencies": 3,
                "stage_delay_ms": 0,
            },
        }
    )


def _solve(tmp_path: Path, sim_type: str) -> dict:
    async def scenario() -> dict:
        runtime = JobRuntime(JobStore(tmp_path / f"{sim_type}.db"))
        job_id = await runtime.submit(_request(sim_type))
        await runtime.wait_idle(timeout=240.0)
        job = await runtime.get_job(job_id)
        assert job["status"] == "complete", job["error_message"]
        results = await runtime.get_results(job_id)
        await runtime.shutdown()
        return results

    return asyncio.run(scenario())


def test_infinite_baffle_solves_with_real_k_end_to_end(tmp_path: Path) -> None:
    results = _solve(tmp_path, "infinite-baffle")

    metadata = results["metadata"]
    assert metadata["solver_backend"] == "metal"
    recorded = metadata["metal"]
    assert (recorded["formulation"], recorded["complex_k_shift"]) == ("standard", 0.0)
    assert metadata["solve_execution"]["formulation"] == "standard"
    assert metadata["infinite_baffle"]["backend"] == "full_3d_coupled"
    provenance = metadata["power_qualification"]["provenance"]
    assert (provenance["formulation"], provenance["complex_k_shift"]) == ("standard", 0.0)

    spl = results["spl_on_axis"]["spl"]
    assert len(spl) == 3 and all(value is not None and math.isfinite(value) for value in spl)
    # A 60 mm horn driven at unit acceleration is quiet (measured 4.8, 8.8 and
    # 15.6 dB at 1 m for 500/1000/2000 Hz) and rises with frequency, as an
    # acceleration-driven source does; a window keeps this a sanity check.
    assert all(0.0 < value < 60.0 for value in spl), spl
    assert spl[0] < spl[1] < spl[2], spl
    assert all(math.isfinite(value) for value in results["impedance"]["real"])
    # Forward-radiating: within the modelled half space (0-90 degrees) nothing is
    # louder than the axis. Rows are [angle_deg, level_db] per frequency.
    for plane in results["directivity"].values():
        for rows in plane:
            forward = [level for angle, level in rows if angle <= 90.0 and level is not None]
            assert forward and max(forward) <= 0.5, rows


def test_free_standing_still_runs_complex_k_end_to_end(tmp_path: Path) -> None:
    results = _solve(tmp_path, "freestanding")

    recorded = results["metadata"]["metal"]
    assert (recorded["formulation"], recorded["complex_k_shift"]) == ("complex_k", 0.005)
    assert results["metadata"]["solve_execution"]["formulation"] == "complex_k"
