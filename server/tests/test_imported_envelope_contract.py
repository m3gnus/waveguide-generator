"""The shared imported envelope keeps the shape all three adapters wrote by hand.

Metal, BEMPP and BEAT each used to assemble the imported ``multi_channel``
envelope and decorate driver channels inline. These tests pin the shared
builders to that exact shape, key order included, since the envelope is
stored as JSON text.
"""

from __future__ import annotations

from types import SimpleNamespace

from server.solver.imported_channels import (
    IMPEDANCE_OMITTED_MULTI_SOURCE,
    IMPEDANCE_OMITTED_WEIGHTED,
    finish_imported_channel_response,
    impedance_omitted_reason,
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


def _channel(source_ids, patch_weights=None):
    return SimpleNamespace(source_ids=list(source_ids), patch_weights=patch_weights)


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
        rms_velocity=True, channel=_channel(["a", "b"]),
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
        response, geometry=_geometry(), driver_payload=None, rms_velocity=False, channel=_channel(["a"]),
    )
    assert applied is False
    assert response == {"impedance": {"real": [1.0]}, "metadata": {}}


def test_one_impedance_rule_for_every_backend() -> None:
    """Only one source at unit weight has a channel impedance in the raw field."""

    assert impedance_omitted_reason(_channel(["a"])) is None
    assert impedance_omitted_reason(_channel(["a"], {"a": 1.0})) is None
    # Derived weights within float noise of 1 are unit weight.
    assert impedance_omitted_reason(_channel(["a"], {"a": 1.0 + 1e-12})) is None
    assert impedance_omitted_reason(_channel(["a"], {"a": 1.0 - 1e-6})) == IMPEDANCE_OMITTED_WEIGHTED
    assert impedance_omitted_reason(_channel(["a", "b"])) == IMPEDANCE_OMITTED_MULTI_SOURCE
    assert impedance_omitted_reason(_channel(["a", "b"], {"a": 1.0, "b": 0.5})) == IMPEDANCE_OMITTED_MULTI_SOURCE
    assert impedance_omitted_reason(_channel(["a"], {"a": 0.5})) == IMPEDANCE_OMITTED_WEIGHTED
    assert impedance_omitted_reason(_channel(["a"], {"a": -1.0})) == IMPEDANCE_OMITTED_WEIGHTED


def test_a_weighted_single_source_channel_drops_its_acoustic_curve_with_the_reason() -> None:
    response = {"impedance": {"real": [1.0]}, "metadata": {}}
    applied = finish_imported_channel_response(
        response, geometry=_geometry(), driver_payload=None, rms_velocity=False,
        channel=_channel(["a"], {"a": 0.5}),
    )
    assert applied is False
    assert response == {"metadata": {"impedance_omitted": IMPEDANCE_OMITTED_WEIGHTED}}


def _modal_result(averages, impedance=None):
    import numpy as np

    count = len(next(iter(averages.values())))
    return SimpleNamespace(frequencies_hz=np.arange(1.0, count + 1.0),
                           surface_pressure_avg={tag: np.asarray(v, dtype=complex) for tag, v in averages.items()},
                           impedance=np.asarray(impedance if impedance is not None else [0j] * count))


def test_modal_impedance_is_the_power_consistent_channel_value() -> None:
    import numpy as np

    from server.solver.imported_channels import modal_channel_impedance

    tags = {"a": 2, "b": 3}
    areas = {2: 1.0, 3: 3.0}
    result = _modal_result({2: [1 + 2j, 2 + 0j], 3: [3 - 1j, 4 + 4j]})
    # One unit-weight patch: exactly its own area mean, i.e. today's raw value.
    one = modal_channel_impedance(result, SimpleNamespace(source_ids=["a"], patch_weights=None, motion="normal"),
                                  tags, areas)
    np.testing.assert_array_equal(one, result.surface_pressure_avg[2])
    # Uniform drive over two patches: the area-weighted mean over their union.
    uniform = modal_channel_impedance(
        result, SimpleNamespace(source_ids=["a", "b"], patch_weights=None, motion="normal"), tags, areas)
    np.testing.assert_allclose(uniform, (1 * result.surface_pressure_avg[2] + 3 * result.surface_pressure_avg[3]) / 4)
    # Weighted: sum w S <p> / sum w^2 S, which is independent of the weights' scale.
    weights = {"a": 1.0, "b": -0.5}
    weighted = modal_channel_impedance(
        result, SimpleNamespace(source_ids=["a", "b"], patch_weights=weights, motion="normal"), tags, areas)
    expected = (1.0 * 1 * result.surface_pressure_avg[2] - 0.5 * 3 * result.surface_pressure_avg[3]) / (1 + 0.25 * 3)
    np.testing.assert_allclose(weighted, expected)
    scaled = modal_channel_impedance(
        result, SimpleNamespace(source_ids=["a", "b"], patch_weights={k: 4 * v for k, v in weights.items()},
                                motion="normal"), tags, areas)
    # Same means under a 4x drive give Zs/4: real means scale with the drive, so Zs is invariant.
    np.testing.assert_allclose(scaled * 4, weighted)
    # Undefined cases keep the rule's drop.
    assert modal_channel_impedance(
        result, SimpleNamespace(source_ids=["a"], patch_weights=None, motion="axial"), tags, areas) is None
    assert modal_channel_impedance(
        result, SimpleNamespace(source_ids=["a", "b"], patch_weights={"a": 0.0, "b": 0.0}, motion="normal"),
        tags, areas) is None
    assert modal_channel_impedance(
        _modal_result({2: [1j, 1j]}), SimpleNamespace(source_ids=["a", "b"], patch_weights=None, motion="normal"),
        tags, areas) is None


def test_apply_modal_touches_only_channels_the_rule_rejects() -> None:
    import numpy as np

    from server.solver.imported_channels import apply_modal_channel_impedance

    unit = _modal_result({2: [5j, 6j]}, impedance=[7j, 8j])
    pair = _modal_result({2: [1j, 2j], 3: [3j, 4j]}, impedance=[9j, 9j])
    channels = [SimpleNamespace(id="unit", source_ids=["a"], patch_weights=None, motion="normal"),
                SimpleNamespace(id="pair", source_ids=["a", "b"], patch_weights=None, motion="normal")]
    modal = apply_modal_channel_impedance({"unit": unit, "pair": pair}, channels, {"a": 2, "b": 3}, "",
                                          tag_areas={2: 1.0, 3: 1.0})
    assert modal == {"pair"}
    np.testing.assert_array_equal(unit.impedance, [7j, 8j])  # left exactly as solved
    np.testing.assert_allclose(pair.impedance, [2j, 3j])
    # No readable mesh areas: no Zs, the drop stands.
    assert apply_modal_channel_impedance({"pair": _modal_result({2: [1j], 3: [1j]})}, channels[1:],
                                         {"a": 2, "b": 3}, "not a mesh") == set()


def test_a_modal_channel_keeps_its_curve_and_says_what_it_is() -> None:
    response = {"impedance": {"real": [1.0]}, "metadata": {}}
    finish_imported_channel_response(
        response, geometry=_geometry(), driver_payload=None, rms_velocity=False,
        channel=_channel(["a", "b"]), modal_impedance=True,
    )
    assert response == {"impedance": {"real": [1.0]},
                        "metadata": {"impedance_projection": "modal_mean_square_velocity"}}
