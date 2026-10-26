"""Driver-group contract and its shared combination path."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from server.jobs.imported_setup import solve_request_for, validate_setup
from server.jobs.models import ChannelCombineSpec, ImportedGeometrySource
from server.solver.combine import combine_drive_channels, expand_legacy_channels, serialize_channel_bases
from server.solver.field_plane import FieldPlaneService
from server.solver.recombine import recombine_stored_results


def geometry():
    return {
        "type": "imported", "ingest_id": "wgi_" + "0" * 26,
        "manifest_sha256": "sha256:" + "1" * 64,
        "artifact_sha256": "sha256:" + "2" * 64,
        "drive_channels": [
            {"id": "a", "source_ids": ["s1"]},
            {"id": "b", "source_ids": ["s2"]},
            {"id": "c", "source_ids": ["s3"]},
        ],
        "driver_groups": [{"id": "bass", "label": "Bass", "channel_ids": ["a", "b"]}],
        "combine": {"members": ["bass", "c"], "crossovers_hz": [500.]},
    }


def native(value):
    return SimpleNamespace(
        frequencies_hz=np.asarray([100., 500., 1000.]),
        observation_angles_deg=np.asarray([0.]), observation_planes=["horizontal"],
        pressure_complex=np.full((3, 1, 1), value, dtype=np.complex128),
        sphere_pressure_complex=np.full((3, 2), value, dtype=np.complex128),
        sphere_theta_deg=np.asarray([0., 90.]), sphere_phi_deg=np.asarray([0., 180.]),
    )


def test_setup_roundtrip_and_legacy_serialization():
    wire = geometry()
    setup = validate_setup({"geometry": {key: value for key, value in wire.items()
                                        if key not in {"type", "ingest_id", "manifest_sha256", "artifact_sha256"}},
                            "options": {"frequencies_hz": [100., 500., 1000.]}})
    request = solve_request_for(setup, ingest_id=wire["ingest_id"],
                                manifest_sha256=wire["manifest_sha256"], artifact_sha256=wire["artifact_sha256"],
                                acknowledged_findings=[])
    assert request.geometry.physical_members(["bass", "c"]) == ["a", "b", "c"]
    assert request.geometry.driver_groups[0].label == "Bass"
    legacy = deepcopy(wire)
    del legacy["driver_groups"]
    legacy["combine"]["members"] = ["a", "c"]
    assert "driver_groups" not in ImportedGeometrySource.model_validate(legacy).model_dump(mode="json")


@pytest.mark.parametrize("mutation, message", [
    (lambda g: g["driver_groups"][0].update(channel_ids=[]), "at least 1 item"),
    (lambda g: g["driver_groups"][0].update(channel_ids=["a", "missing"]), "unknown drive channels"),
    (lambda g: g["driver_groups"].append({"id": "other", "channel_ids": ["a"]}), "only one driver group"),
    (lambda g: g["driver_groups"][0].update(id="a"), "collides"),
    (lambda g: g["drive_channels"][1].update(motion="axial"), "identical motion and drivers"),
    (lambda g: g["combine"].update(members=["bass", "a"]), "overlap"),
])
def test_invalid_groups_refused(mutation, message):
    value = geometry()
    mutation(value)
    with pytest.raises(ValidationError, match=message):
        ImportedGeometrySource.model_validate(value)


def test_group_sum_and_singleton_match_existing_combiner():
    results = {"a": native(1 + 0.5j), "b": native(2 - 0.5j), "c": native(3)}
    grouped, metadata = combine_drive_channels(
        results, members=["bass", "c"], crossovers_hz=[500.], level_match=False, align=False,
        member_channels={"bass": ["a", "b"], "c": ["c"]},
    )
    expected, _ = combine_drive_channels(
        {"bass": native(3), "c": results["c"]}, members=["bass", "c"],
        crossovers_hz=[500.], level_match=False, align=False,
    )
    np.testing.assert_allclose(grouped.pressure_complex, expected.pressure_complex)
    np.testing.assert_allclose(grouped.sphere_pressure_complex, expected.sphere_pressure_complex)
    assert metadata["member_channels"] == {"bass": ["a", "b"], "c": ["c"]}
    single, _ = combine_drive_channels(results, members=["a", "c"], crossovers_hz=[500.],
                                       member_channels={"a": ["a"], "c": ["c"]})
    old, _ = combine_drive_channels(results, members=["a", "c"], crossovers_hz=[500.])
    np.testing.assert_array_equal(single.pressure_complex, old.pressure_complex)


def test_group_warnings_name_roles_or_labels_not_internal_ids():
    settings = expand_legacy_channels(["internal-group", "internal-high"], [500.])
    settings["internal-group"]["gain"] = {"mode": "max"}
    _, metadata = combine_drive_channels(
        {"a": native(1), "b": native(2), "c": native(3)},
        members=["internal-group", "internal-high"], channels=settings,
        member_channels={"internal-group": ["a", "b"], "internal-high": ["c"]},
        member_roles={"internal-group": "MF", "internal-high": "HF"},
        member_labels={"internal-group": "Mid pair"},
    )
    warnings = " ".join(metadata["warnings"])
    assert "Mid pair" in warnings
    assert "internal-group" not in warnings and "internal-high" not in warnings


def test_recombine_uses_physical_bases_and_provenance():
    setup = geometry()
    request = solve_request_for(validate_setup({"geometry": {k: v for k, v in setup.items()
                                if k not in {"type", "ingest_id", "manifest_sha256", "artifact_sha256"}},
                                "options": {"frequencies_hz": [100., 500., 1000.]}}),
                                ingest_id=setup["ingest_id"], manifest_sha256=setup["manifest_sha256"],
                                artifact_sha256=setup["artifact_sha256"], acknowledged_findings=[])
    bases = serialize_channel_bases({"a": native(1), "b": native(2), "c": native(3)})
    results = {"channels": {name: {"metadata": {"role": "LF" if name != "c" else "HF"}}
                             for name in ("a", "b", "c")}, "channel_order": ["a", "b", "c"]}
    updated = recombine_stored_results(results, bases, ChannelCombineSpec(members=["bass", "c"], crossovers_hz=[500.]), request)
    metadata = updated["channels"]["combined"]["metadata"]
    assert metadata["derived_from_channels"] == ["a", "b", "c"]
    assert metadata["source_ids"] == ["s1", "s2", "s3"]
    assert metadata["combine"]["member_roles"] == ["LF", "HF"]


def test_group_gain_ceiling_is_the_first_physical_driver_limit():
    setup = geometry()
    request = solve_request_for(validate_setup({"geometry": {k: v for k, v in setup.items()
                                if k not in {"type", "ingest_id", "manifest_sha256", "artifact_sha256"}},
                                "options": {"frequencies_hz": [100., 500., 1000.]}}),
                                ingest_id=setup["ingest_id"], manifest_sha256=setup["manifest_sha256"],
                                artifact_sha256=setup["artifact_sha256"], acknowledged_findings=[])
    bases = serialize_channel_bases({"a": native(1), "b": native(2), "c": native(3)})
    def driver(excursion):
        return {"metadata": {"driver": {"spec": {"xmax_mm": 4.},
                "cone_excursion_mm": {"frequencies": [100., 500., 1000.], "values": [excursion] * 3}}}}
    results = {"channels": {"a": driver(1.), "b": driver(2.), "c": {}}, "channel_order": ["a", "b", "c"]}
    grouped = recombine_stored_results(results, bases, ChannelCombineSpec(members=["bass", "c"], crossovers_hz=[500.]), request)
    single = recombine_stored_results(results, bases, ChannelCombineSpec(members=["b", "c"], crossovers_hz=[500.]), request)
    group_limit = grouped["channels"]["combined"]["metadata"]["combine"]["channels"]["bass"]["gain_max_db"]
    single_limit = single["channels"]["combined"]["metadata"]["combine"]["channels"]["b"]["gain_max_db"]
    assert group_limit == pytest.approx(single_limit)


def test_field_plane_synthesizes_group_traces_with_one_branch_weight(monkeypatch):
    service = object.__new__(FieldPlaneService)
    traces = {"a": 1 + 1j, "b": 2 - 1j, "c": 4 + 0j}
    monkeypatch.setattr(service, "_raw_channel_ids", lambda _row: list(traces))
    monkeypatch.setattr(service, "_load_channel", lambda _job, _index, channel: (
        "mesh", 500., 1., None, np.asarray([traces[channel]]), np.asarray([traces[channel] * 2]), "backend"))
    metadata = {"members": ["bass", "c"], "member_channels": {"bass": ["a", "b"], "c": ["c"]},
                "crossovers_hz": [500.], "level_match": {"gains_db": {"bass": 0., "c": 0.}},
                "delays_ms": {"bass": 0., "c": 0.}}
    monkeypatch.setattr(service, "_current_combine", lambda _job: metadata)
    system = SimpleNamespace(response=SimpleNamespace(id="system"), frequency_index=0)
    member = SimpleNamespace(response=SimpleNamespace(id="member:bass"), frequency_index=0)
    system_result = service._load_response_traces("job", {}, system)
    member_result = service._load_response_traces("job", {}, member)
    assert system_result[7] == member_result[7]
    # At an LR4 crossover each branch has raw weight -0.5.
    np.testing.assert_allclose(member_result[4], [-0.5 * (traces["a"] + traces["b"])])
    np.testing.assert_allclose(system_result[4], [-0.5 * sum(traces.values())])
