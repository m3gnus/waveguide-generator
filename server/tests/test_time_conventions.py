"""Exact convention conversion and compatibility of the serialized tags."""

import io
from types import SimpleNamespace

import numpy as np
import pytest

from server.contracts.conventions import (
    ENGINEERING_PHASE_CONVENTION,
    ENGINEERING_TIME_CONVENTION,
    PHASE_TIME_CONVENTION,
    SOLVER_PHASE_CONVENTION,
    SOLVER_TIME_CONVENTION,
    engineering_to_solver,
    solver_to_engineering,
)
from server.jobs.models import ChannelCombineSpec, SolveRequest
from server.solver.combine import serialize_channel_bases
from server.solver.pressure_basis import PRESSURE_PHASE_CONVENTION, export_pressure_basis
from server.solver.recombine import recombine_stored_results


@pytest.mark.parametrize("convert", [solver_to_engineering, engineering_to_solver])
@pytest.mark.parametrize("dtype", [np.complex64, np.complex128])
@pytest.mark.parametrize("scalar", [False, True])
def test_conversion_is_bitwise_numpy_conjugation(convert, dtype, scalar) -> None:
    values = np.asarray(
        [
            complex(1.25, -2.5),
            complex(-0.0, 0.0),
            complex(0.0, -0.0),
            complex(np.inf, -np.inf),
            complex(np.nan, np.nan),
        ],
        dtype=dtype,
    )
    # A non-contiguous view exercises the array path without normalizing it.
    value = values[0] if scalar else np.tile(values, (2, 1)).T
    original = value.tobytes()
    expected = np.conj(value)
    actual = convert(value)
    assert type(actual) is type(expected)
    assert actual.dtype == expected.dtype
    assert actual.shape == expected.shape
    assert actual.tobytes() == expected.tobytes()
    assert value.tobytes() == original
    assert convert(actual).tobytes() == original


@pytest.mark.parametrize("convert", [solver_to_engineering, engineering_to_solver])
def test_conversion_accepts_python_complex(convert) -> None:
    assert convert(1.25 - 2.5j).tobytes() == np.conj(1.25 - 2.5j).tobytes()


def test_convention_constants_preserve_the_wire_spellings() -> None:
    assert SOLVER_TIME_CONVENTION == "exp(-i omega t)"
    assert ENGINEERING_TIME_CONVENTION == "exp(+j omega t)"
    assert PHASE_TIME_CONVENTION == "exp(+ikr)"
    assert SOLVER_PHASE_CONVENTION == "solver_exp_plus_ikr"
    assert ENGINEERING_PHASE_CONVENTION == "engineering_exp_plus_jwt"
    assert PRESSURE_PHASE_CONVENTION == ENGINEERING_PHASE_CONVENTION


def test_constants_match_recombined_envelope_and_pressure_artifacts() -> None:
    native = SimpleNamespace(
        frequencies_hz=np.asarray([100.0, 1000.0]),
        observation_angles_deg=np.asarray([-30.0, 0.0, 30.0]),
        observation_planes=["horizontal"],
        pressure_complex=np.full((2, 1, 3), 1.0 + 2.0j),
    )
    bases = serialize_channel_bases({"low": native, "high": native})
    with np.load(io.BytesIO(bases), allow_pickle=False) as archive:
        assert archive["phase_convention"].item() == SOLVER_PHASE_CONVENTION
    public = export_pressure_basis(bases, {}, "low", {"low": "normal"})
    with np.load(io.BytesIO(public.content), allow_pickle=False) as archive:
        assert archive["phase_convention"].item() == ENGINEERING_PHASE_CONVENTION
        assert archive["pressure_complex"].tobytes() == np.conj(native.pressure_complex).tobytes()
    request = SolveRequest.model_validate(
        {
            "geometry": {
                "type": "imported",
                "ingest_id": "wgi_" + "0" * 26,
                "manifest_sha256": "sha256:" + "1" * 64,
                "artifact_sha256": "sha256:" + "2" * 64,
                "mesh": {
                    "rigid_size_mm": 8.0,
                    "transition_mm": 20.0,
                    "source_size_mm": {"source-low": 3.0, "source-high": 3.0},
                },
                "drive_channels": [
                    {"id": "low", "source_ids": ["source-low"]},
                    {"id": "high", "source_ids": ["source-high"]},
                ],
            },
            "options": {"engine": "metal", "frequencies_hz": [100.0, 1000.0]},
        }
    )
    response = recombine_stored_results(
        {"channels": {"low": {}, "high": {}}},
        bases,
        ChannelCombineSpec(members=["low", "high"], crossovers_hz=[500.0]),
        request,
    )
    assert (
        response["channels"]["combined"]["metadata"]["phase_time_convention"]
        == PHASE_TIME_CONVENTION
    )
