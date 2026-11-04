"""Frozen before P6: legacy request/setup bytes and retained complex bases.

Synthetic acceleration bases exercise the production normalization, retention
and stored recombination owners without starting a native solver.
"""

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from server.jobs.imported_setup import setup_of_request, solve_request_for, validate_setup
from server.jobs.models import SolveRequest
from server.solver.combine import serialize_channel_bases
from server.solver.imported_channels import channel_basis_metadata, normalize_assembly_prescribed_result
from server.solver.recombine import recombine_stored_results


def characterization(origin):
    features = ["wg-design-assembly-v1"] if origin == "assembly" else []
    request = SolveRequest.model_validate({
        "geometry": {
            "type": "imported", "ingest_id": "wgi_" + "0" * 26,
            "manifest_sha256": "sha256:" + "1" * 64, "artifact_sha256": "sha256:" + "2" * 64,
            "required_features": features,
            "drive_channels": [{"id": name, "source_ids": [name]} for name in ("hf", "lf")],
            "combine": {"members": ["lf", "hf"], "reference": "hf", "channels": {
                "lf": {"lp": {"family": "lr", "order": 4, "fc_hz": 300},
                       "gain": {"mode": "manual", "db": -3}, "delay": {"mode": "manual", "ms": 0.2}},
                "hf": {"hp": {"family": "lr", "order": 4, "fc_hz": 300},
                       "gain": {"mode": "manual", "db": 0}, "delay": {"mode": "manual", "ms": 0}},
            }},
        },
        "options": {"engine": "beat-cpu", "frequencies_hz": [100., 300., 500.],
                    "polar_config": {"enabled_axes": ["horizontal"]}},
    })
    wire = request.model_dump_json()
    record = {"producer": {"assembly": "design-assembly", "cad": "cad", "file": "mesh-file"}[origin],
              "source_tags": {"hf": 2, "lf": 3}}
    setup, exact = setup_of_request(json.loads(wire), record)
    assert exact
    replay = solve_request_for(validate_setup(setup), ingest_id=request.geometry.ingest_id,
                               manifest_sha256=request.geometry.manifest_sha256,
                               artifact_sha256=request.geometry.artifact_sha256, acknowledged_findings=[])
    assert replay.model_dump_json() == wire
    frequencies = np.array([100., 300., 500.])
    results = {}
    for index, name in enumerate(("hf", "lf"), 1):
        pressure = np.full((3, 1, 2), index * (1 + 2j))
        native = SimpleNamespace(frequencies_hz=frequencies, pressure_complex=pressure,
                                 sphere_pressure_complex=pressure[:, 0, :].copy(),
                                 sphere_theta_deg=np.array([0., 180.]), sphere_phi_deg=np.array([0.]),
                                 observation_angles_deg=np.array([0., 10.]), observation_planes=["horizontal"])
        normalize_assembly_prescribed_result(native, request.geometry)
        results[name] = native
    metadata = channel_basis_metadata(request.geometry, record, record["source_tags"], {})
    bases = serialize_channel_bases(results, metadata_by_id=metadata)
    # Compare uncompressed arrays, not ZIP timestamps or compression details.
    with np.load(io.BytesIO(bases), allow_pickle=False) as archive:
        arrays = {name: hashlib.sha256(archive[name].tobytes()).hexdigest() for name in archive.files}
    combined = recombine_stored_results({"channels": {name: {"metadata": {}} for name in results}},
                                        bases, request.geometry.combine, request)["channels"]["combined"]
    combined["metadata"].pop("performance", None)  # Wall clock is not physics.
    return {"request_json": wire, "setup": setup, "basis_arrays_sha256": arrays, "combined": combined}


@pytest.mark.parametrize("origin", ["assembly", "cad", "file"])
def test_saved_request_setup_bases_and_recombination_are_unchanged(origin):
    expected = json.loads((Path(__file__).parent / "fixtures/imported_physics_replay.json").read_text())
    assert characterization(origin) == expected[origin]
