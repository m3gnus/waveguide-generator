"""The shared imported envelope keeps the shape all three adapters wrote by hand.

Metal, BEMPP and BEAT each used to assemble the imported ``multi_channel``
envelope and decorate driver channels inline. These tests pin the shared
builders to that exact shape, key order included, since the envelope is
stored as JSON text.
"""

from __future__ import annotations

from types import SimpleNamespace

from server.solver.imported_channels import (
    finish_imported_channel_response,
    imported_envelope,
    imported_fem_exclusion,
)


def _geometry(**overrides):
    values = dict(
        ingest_id="ing-1", manifest_sha256="m" * 64, artifact_sha256="a" * 64,
        acknowledged_findings=("finding-1",), exterior_only=False,
        drive_voltage_v=2.83, rg_ohm=0.1,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _record(fem_air_volumes=None):
    record = {
        "tag_namespace": "cad",
        "tag_map": {"src": 2},
        "polar_grid_derivation": {"rule": "x"},
    }
    if fem_air_volumes is not None:
        record["evidence"] = {"fem_air_volumes": fem_air_volumes}
    return record


def test_envelope_keys_values_and_order_match_the_adapter_contract() -> None:
    channels = {"a": {"frequencies": [200.0, 100.0]}, "b": {"frequencies": [300.0, 100.0]}}
    envelope = imported_envelope(
        geometry=_geometry(), record=_record(), channels=channels, channel_order=["a", "b"],
        per_source_validity={"src": {"ok": True}}, domain_planes=["yz", "xz"],
        frame_basis={"axis": [0.0, 0.0, 1.0]}, solver_backend="beat",
        solver_engine={"engine": "beat-cpu", "device": "cpu"}, started=0.0,
        trace_estimated_bytes=10, trace_cap_bytes=20,
        leading_metadata={"_solver_fingerprints": []},
    )

    assert list(envelope) == [
        "result_kind", "result_contract_version", "channels", "channel_order", "metadata", "frequencies",
    ]
    assert envelope["frequencies"] == [100.0, 200.0, 300.0]
    metadata = envelope["metadata"]
    assert list(metadata) == [
        "_solver_fingerprints", "result_contract_version", "geometry_type", "solver_backend",
        "solver_mode", "solve_path", "solver_engine", "ingest_id", "manifest_sha256",
        "artifact_sha256", "tag_namespace", "tag_map", "per_source_frequency_validity",
        "symmetry_planes_used", "polar_grid_derivation", "observation_origin_effective",
        "observation_frame_basis", "acknowledged_findings", "exterior_only", "fem_exclusion",
        "performance", "field_trace_retention",
    ]
    performance = metadata.pop("performance")
    assert list(performance) == ["total_time_seconds"]
    assert {key: metadata[key] for key in metadata if key != "_solver_fingerprints"} == {
        "result_contract_version": 2, "geometry_type": "imported", "solver_backend": "beat",
        "solver_mode": "full_3d", "solve_path": "full-3d",
        "solver_engine": {"engine": "beat-cpu", "device": "cpu"},
        "ingest_id": "ing-1", "manifest_sha256": "m" * 64, "artifact_sha256": "a" * 64,
        "tag_namespace": "cad", "tag_map": {"src": 2},
        "per_source_frequency_validity": {"src": {"ok": True}},
        "symmetry_planes_used": ["xz", "yz"], "polar_grid_derivation": {"rule": "x"},
        "observation_origin_effective": "throat", "observation_frame_basis": {"axis": [0.0, 0.0, 1.0]},
        "acknowledged_findings": ["finding-1"], "exterior_only": False,
        "fem_exclusion": {"excluded": False, "declared_volume_count": 0},
        "field_trace_retention": {"estimated_bytes": 10, "cap_bytes": 20},
    }


def test_an_envelope_without_channel_frequencies_has_no_frequency_mirror() -> None:
    envelope = imported_envelope(
        geometry=_geometry(), record=_record(), channels={}, channel_order=[],
        per_source_validity={}, domain_planes=[], frame_basis={}, solver_backend="metal",
        solver_engine={}, started=0.0, trace_estimated_bytes=None, trace_cap_bytes=None,
    )
    assert "frequencies" not in envelope
    assert next(iter(envelope["metadata"])) == "result_contract_version"


def test_fem_exclusion_names_only_an_exterior_only_override() -> None:
    assert imported_fem_exclusion(_record([{"id": 1}, {"id": 2}]), _geometry(exterior_only=True)) == {
        "excluded": True, "declared_volume_count": 2, "reason": "Phase 2 exterior_only override",
    }
    assert imported_fem_exclusion(_record([{"id": 1}]), _geometry()) == {
        "excluded": False, "declared_volume_count": 1,
    }
    assert imported_fem_exclusion({"evidence": "not a mapping"}, _geometry(exterior_only=True)) == {
        "excluded": False, "declared_volume_count": 0,
    }


def test_driver_channel_reports_electrical_impedance_in_the_adapter_order() -> None:
    response = {"impedance": {"real": [1.0]}, "metadata": {"warnings": ["w0"]}}
    payload = {
        "electrical_impedance_ohm": {"frequencies": [100.0], "real": [6.0], "imaginary": [1.0]},
        "warnings": ["w1"],
    }
    applied = finish_imported_channel_response(
        response, geometry=_geometry(), driver_payload=payload,
        rms_velocity=True, omit_impedance=True,
    )

    assert applied is True
    assert response["impedance"] == {"frequencies": [100.0], "real": [6.0], "imaginary": [1.0]}
    assert list(response["metadata"]) == [
        "warnings", "impedance_drive", "impedance_omitted", "impedance_units", "impedance_quantity",
        "impedance_phase_convention", "driver", "drive", "warning_count",
    ]
    assert response["metadata"]["impedance_phase_convention"] == "engineering_exp_plus_jwt"
    assert response["metadata"]["impedance_drive"] == "voltage"
    assert response["metadata"]["driver"] == {"warnings": ["w1"]}
    assert response["metadata"]["drive"] == {"voltage_v": 2.83, "rg_ohm": 0.1}
    assert response["metadata"]["warnings"] == ["w0", "w1"]
    assert response["metadata"]["warning_count"] == 2


def test_channel_without_driver_keeps_its_acoustic_impedance() -> None:
    response = {"impedance": {"real": [1.0]}, "metadata": {}}
    applied = finish_imported_channel_response(
        response, geometry=_geometry(), driver_payload=None, rms_velocity=False, omit_impedance=False,
    )
    assert applied is False
    assert response == {"impedance": {"real": [1.0]}, "metadata": {}}
