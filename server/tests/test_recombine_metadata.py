"""Post-solve crossover edits retain the solve's frame and execution identity."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from server.jobs.models import ChannelCombineSpec, SolveRequest
from server.solver.combine import serialize_channel_bases
from server.solver.recombine import recombine_stored_results


@pytest.mark.parametrize("backend,engine", [
    ("metal", "metal"), ("beat", "beat-cpu"), ("bempp", "bempp"),
])
@pytest.mark.parametrize("existing", [False, True])
def test_recombine_preserves_solve_metadata(backend, engine, existing):
    request = SolveRequest.model_validate({
        "geometry": {
            "type": "imported", "ingest_id": "wgi_" + "0" * 26,
            "manifest_sha256": "sha256:" + "1" * 64,
            "artifact_sha256": "sha256:" + "2" * 64,
            "mesh": {"rigid_size_mm": 8., "transition_mm": 20.,
                     "source_size_mm": {"source-low": 3., "source-high": 3.}},
            "drive_channels": [
                {"id": "low", "source_ids": ["source-low"]},
                {"id": "high", "source_ids": ["source-high"]},
            ],
        },
        "options": {"engine": engine, "frequencies_hz": [100., 1000.]},
    })
    native = SimpleNamespace(
        frequencies_hz=np.asarray([100., 1000.]),
        observation_angles_deg=np.asarray([-30., 0., 30.]),
        observation_planes=["horizontal"],
        pressure_complex=np.full((2, 1, 3), 1. + 2.j),
    )
    bases = serialize_channel_bases({"low": native, "high": native})
    shared = {
        "device_interface": {"selected": engine},
        "mesh_validation": {"mode": "warn"},
        backend: {"formulation": "burton_miller", "native_symmetry_plane": None},
        "solver_engine": {"engine": engine, "provider": "test", "package_version": "1.2.3"},
        "observation_frame_basis": {
            "axis": [1., 0., 0.], "u": [0., 1., 0.], "v": [0., 0., 1.],
            "origin_m": [.05, .02, .03], "mouth_center_m": [.06, .02, .03],
            "source_center_m": [.04, .02, .03],
        },
        "solve_execution": {"engine": engine, "accuracy": "balanced", "formulation": "burton_miller"},
        "cad_identity": {"model_id": "model", "setup_revision_id": "setup"},
    }
    member_metadata = {
        **deepcopy(shared), "solver_backend": backend, "engine": engine,
        "role": "LF", "drive_channel_id": "low", "source_ids": ["source-low"],
    }
    member_fields = {
        "beat": {"merged_source_tags": [101], "axially_reversed_source_tags": [101],
                 "solver_log": ["member log"]},
        "bempp": {"velocity_source_tags": [101], "source_motion": "normal",
                  "solver_log": ["member log"]},
    }.get(backend, {})
    member_metadata[backend].update(member_fields)
    results = {"channels": {"low": {"metadata": member_metadata}, "high": {}},
               "channel_order": ["low", "high"]}
    if existing:
        results["channels"]["combined"] = {"metadata": {"observation_frame_basis": {"stale": True}}}
        results["channel_order"].append("combined")
    original = deepcopy(results)

    updated = recombine_stored_results(
        results, bases, ChannelCombineSpec(members=["low", "high"], crossovers_hz=[500.]), request,
    )

    combined = updated["channels"]["combined"]
    for name, value in shared.items():
        assert combined["metadata"][name] == value
    assert all(field not in combined["metadata"][backend] for field in member_fields)
    assert combined["metadata"]["drive_channel_id"] == "combined"
    assert combined["metadata"]["source_ids"] == ["source-low", "source-high"]
    assert "role" not in combined["metadata"]
    assert combined["metadata"]["combine"]["crossovers_hz"] == [500.]
    assert "impedance" not in combined
    assert updated["channel_order"] == ["low", "high", "combined"]
    assert results == original
