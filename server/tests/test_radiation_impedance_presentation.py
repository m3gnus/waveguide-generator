from __future__ import annotations

import asyncio
from datetime import datetime
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException

from server.jobs.api import create_jobs_router
from server.jobs.radiation_impedance import radiation_impedance_presentation
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore


def artifact_bytes(**updates: np.ndarray) -> bytes:
    values: dict[str, np.ndarray] = {
        "frequencies_hz": np.asarray([100.0, 200.0]),
        "aperture_names": np.asarray(["PORT_L", "PORT_R", "MF"]),
        "aperture_area_m2": np.asarray([0.01, 0.01, 0.02]),
        "aperture_tag": np.asarray([31, 32, 33]),
        # This is deliberately not the conjugate of the presentation matrix:
        # the reader must use the archived engineering value, never infer one
        # from a field whose convention belongs to the solver.
        "solver_impedance_matrix": np.full((2, 3, 3), 99.0 + 88.0j),
        "engineering_impedance_matrix": np.asarray(
            [
                [[1 + 2j, 3 + 4j, 5 + 6j], [7 + 8j, 9 + 10j, 11 + 12j], [13 + 14j, 15 + 16j, 17 + 18j]],
                [[21 + 22j, 23 + 24j, 25 + 26j], [27 + 28j, 29 + 30j, 31 + 32j], [33 + 34j, 35 + 36j, 37 + 38j]],
            ],
            dtype=np.complex128,
        ),
        "in_phase_aperture_names": np.asarray(["PORT_L", "PORT_R"]),
        "in_phase_termination_load": np.asarray(
            [[4 + 6j, 16 + 18j], [44 + 46j, 56 + 58j]],
            dtype=np.complex128,
        ),
    }
    values.update(updates)
    buffer = BytesIO()
    np.savez_compressed(buffer, **values)
    return buffer.getvalue()


def test_presentation_preserves_engineering_units_phase_and_aperture_identity() -> None:
    presentation = radiation_impedance_presentation(artifact_bytes())

    assert presentation["schema_version"] == 1
    assert presentation["units"] == "Pa*s/m^3"
    assert presentation["phase_time_convention"] == "engineering_exp_plus_jwt"
    assert presentation["quantity"] == "average_aperture_pressure_per_volume_velocity"
    assert presentation["apertures"] == [
        {"name": "PORT_L", "area_m2": 0.01, "tag": 31},
        {"name": "PORT_R", "area_m2": 0.01, "tag": 32},
        {"name": "MF", "area_m2": 0.02, "tag": 33},
    ]
    assert presentation["engineering_matrix"]["real"][0][0][1] == 3.0
    assert presentation["engineering_matrix"]["imaginary"][0][0][1] == 4.0
    assert presentation["in_phase_termination"] == {
        "aperture_names": ["PORT_L", "PORT_R"],
        "real": [[4.0, 16.0], [44.0, 56.0]],
        "imaginary": [[6.0, 18.0], [46.0, 58.0]],
    }


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"engineering_impedance_matrix": np.ones((2, 2, 2))},
            "engineering_impedance_matrix",
        ),
        (
            {"in_phase_aperture_names": np.asarray(["UNKNOWN", "PORT_R"])},
            "in-phase aperture names",
        ),
        (
            {"frequencies_hz": np.asarray([100.0, np.nan])},
            "frequencies_hz must be finite",
        ),
    ],
)
def test_presentation_refuses_ambiguous_or_nonfinite_artifacts(
    updates: dict[str, np.ndarray], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        radiation_impedance_presentation(artifact_bytes(**updates))


def test_presentation_refuses_missing_contract_fields() -> None:
    buffer = BytesIO()
    np.savez_compressed(buffer, frequencies_hz=np.asarray([100.0]))
    with pytest.raises(ValueError, match="missing.*engineering_impedance_matrix"):
        radiation_impedance_presentation(buffer.getvalue())


def test_presentation_endpoint_reads_the_retained_artifact_and_404s_when_absent(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        now = datetime.now().isoformat()
        for job_id in ("cardioid", "ordinary"):
            store.create_job(
                {
                    "id": job_id,
                    "status": "complete",
                    "created_at": now,
                    "updated_at": now,
                    "queued_at": now,
                    "completed_at": now,
                    "progress": 1.0,
                    "stage": "complete",
                    "config_json": {"design": {"formula": "OSSE"}},
                    "config_summary_json": {"formula_type": "OSSE"},
                    "task_metadata": {},
                }
            )
        store.store_radiation_impedance("cardioid", artifact_bytes())
        runtime = JobRuntime(store)
        runtime._started = True
        endpoint = next(
            route.endpoint
            for route in create_jobs_router(runtime).routes
            if getattr(route, "path", None)
            == "/api/radiation-impedance/{job_id}/presentation"
        )

        response = await endpoint("cardioid")
        assert response.units == "Pa*s/m^3"
        assert response.in_phase_termination.aperture_names == ["PORT_L", "PORT_R"]
        with pytest.raises(HTTPException) as absent:
            await endpoint("ordinary")
        assert absent.value.status_code == 404
        assert "no passive-cardioid" in str(absent.value.detail)
        await runtime.shutdown()

    asyncio.run(scenario())
