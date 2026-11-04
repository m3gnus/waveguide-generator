"""A design assembly's mixed BEAT v3 request: prescribed HF beside voltage-driven woofers.

The wire is checked against the official BEAT contract; the native sweep is a
synthetic stand-in, so these tests qualify the request, packaging, bases and
recombination, never acoustics.
"""

import io
import numpy as np
import pytest
from pydantic import ValidationError

from server.contracts.conventions import engineering_to_solver
from server.jobs.models import SolveRequest
from server.solver import beat_transducer_imported as jobs
from server.solver.beat_adapter.observations import build_observations
from server.solver.beat_adapter.transducers import (
    TransducerSweep, build_transducer_request, parse_transducer_frequency,
)
from server.solver.combine import combine_drive_channels, deserialize_channel_bases, member_processing_weights
from server.solver.recombine import recombine_stored_results
from server.solver.pressure_basis import export_pressure_basis
from server.tests.beat_adapter.test_transducers import raw

DRIVER = dict(version=1, re_ohm=6.0, le_h=0.0005, bl_n_per_a=7.0, mmd_kg=0.02,
              cms_m_per_n=0.001, rms_n_s_per_m=1.5, motion_axis=[0.0, 0.0, 1.0])
W1, W2 = "00000000-0000-4000-8000-0000000000aa", "00000000-0000-4000-8000-0000000000ab"
G_HF, G_LF = "00000000-0000-4000-8000-0000000000b1", "00000000-0000-4000-8000-0000000000b2"


@pytest.fixture
def solid(make_mesh):
    return make_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                     [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], [2, 3, 4, 5])


@pytest.fixture
def record():
    return {"producer": "design-assembly", "required_features": ["wg-design-assembly-v1"],
            "source_tags": {"horn-throat": 2, f"{W1}/piston": 3, f"{W2}/piston": 4},
            "symmetry": {"cut_planes": []}, "normalisation": {"assembly_frame_is_solver_frame": True}}


def request(*, combine=True, processing=None, w2_driver=False, **geometry_changes):
    channels = [
        dict(id="horn-throat", source_ids=["horn-throat"], motion="normal"),
        dict(id=W1, source_ids=[f"{W1}/piston"], motion="axial", exterior_transducer=DRIVER),
        dict(id=W2, source_ids=[f"{W2}/piston"], motion="axial",
             **({"exterior_transducer": {**DRIVER, "motion_axis": [-1.0, 0.0, 0.0]}} if w2_driver else {})),
    ]
    groups = [dict(id=G_HF, channel_ids=["horn-throat"], label="HF"),
              dict(id=G_LF, channel_ids=[W1, W2], label="LF",
                   member_processing=processing or {W1: {}, W2: {"delay_ms": 0.25, "invert": True}})]
    geometry = dict(
        type="imported", required_features=["wg-design-assembly-v1"],
        ingest_id="wgi_" + "0" * 26, manifest_sha256="sha256:" + "1" * 64, artifact_sha256="sha256:" + "2" * 64,
        drive_channels=channels, driver_groups=groups,
        **({"combine": dict(members=[G_LF, G_HF], reference=G_HF, channels={
            G_LF: {"lp": {"family": "lr", "order": 4, "fc_hz": 300}, "gain": {"mode": "manual", "db": -3},
                   "delay": {"mode": "manual", "ms": 0.1}},
            G_HF: {"hp": {"family": "lr", "order": 4, "fc_hz": 300}, "gain": {"mode": "manual", "db": 0},
                   "delay": {"mode": "manual", "ms": 0}, "invert": True},
        })} if combine else {}),
    )
    geometry.update(geometry_changes)
    return SolveRequest.model_validate(dict(geometry=geometry, options=dict(
        engine="beat-cpu", frequencies_hz=[100.0, 300.0, 500.0],
        polar_config=dict(enabled_axes=["horizontal"], spherical_theta_count=5, spherical_phi_count=8))))


def distinct(compiled, frequency):
    """Native rows whose pressure differs per excitation column, so sums are checkable."""
    data = raw(compiled, frequency)
    from server.tests.beat_adapter.conftest import wire

    for quantity in data["quantities"]:
        if quantity["quantity"] == "radiation_impedance_matrix":
            count = len(quantity["metadata"]["component_ids"])
            quantity["metadata"]["effective_volume_area_m2"] = [0.02] * count
        if quantity["quantity"] == "exterior_pressure":
            count, size = quantity["values"]["shape"]
            values = np.asarray([(index + 1) * (1 + 2j) * (frequency / 100) for index in range(count)])
            quantity["values"] = wire(np.repeat(values[:, None], size, axis=1))
    return data


def test_mixed_request_is_one_official_v3_system_with_ideal_and_voltage_ports(solid, record, official_contract):
    geometry = request().geometry
    ideal, order, indices = jobs.excitation_plan(record, solid, geometry.drive_channels)
    compiled = build_transducer_request(
        solid, drivers=jobs.drivers_for(record, geometry.drive_channels), layout=build_observations(sphere_grid=None),
        frame={"origin": [0, 0, 0], "axis": [0, 0, 1], "u": [1, 0, 0], "v": [0, 1, 0]},
        frequencies_hz=[100.0], ideal_sources=ideal, excitation_port_ids=order)
    official_contract(compiled)
    assert order == ["excitation:normal:horn-throat", f"voltage:{W1}", f"excitation:axial:{W2}%2Fpiston"]
    assert indices == {"horn-throat": [0], W1: [1], W2: [2]}
    kinds = {p["id"]: p["kind"] for p in compiled.wire["compiled_system"]["excitation_ports"]}
    assert kinds[f"voltage:{W1}"] == "voltage" and kinds["excitation:normal:horn-throat"] != "voltage"
    # Every other driver is shorted while a column is excited: mutual loading.
    assert compiled.wire["compiled_system"]["metadata"]["undriven_transducer_termination"] == "shorted"
    assert compiled.transducer_ids == (f"transducer:{W1}",)


def test_every_producer_can_mix_prescribed_and_driven_channels(solid, record):
    channels = request().geometry.drive_channels
    assert jobs.preflight(record, solid, channels, backend="cpu") is None
    cad = {key: value for key, value in record.items() if key not in {"producer", "required_features"}}
    assert jobs.preflight(cad, solid, channels, backend="cpu") is None
    from server.jobs.runtime.imported_refusals import _imported_request_refusal

    payload = request().model_dump(mode="json")
    refusal = _imported_request_refusal(request(), {})
    assert refusal is None or refusal[0] != "exterior_transducer_mixed_channels"
    payload["geometry"]["required_features"] = []
    payload["geometry"]["driver_groups"][1].pop("member_processing")
    payload["geometry"].pop("combine")
    with pytest.raises(ValidationError, match="identical motion and drivers"):
        SolveRequest.model_validate(payload)
    payload["geometry"]["driver_groups"] = None
    refusal = _imported_request_refusal(SolveRequest.model_validate(payload), {})
    assert refusal is None or refusal[0] != "exterior_transducer_mixed_channels"


@pytest.mark.parametrize(("gain", "message"), [({"mode": "max"}, "maximum-output")])
def test_maximum_output_stays_refused_for_coupled_drivers(gain, message):
    payload = request().model_dump(mode="json")
    payload["geometry"]["combine"]["channels"][G_LF]["gain"] = gain
    with pytest.raises(ValidationError, match=message):
        SolveRequest.model_validate(payload)
    payload = request().model_dump(mode="json")
    payload["geometry"]["max_drive_voltage_v"] = 20.0
    with pytest.raises(ValidationError, match="ceilings"):
        SolveRequest.model_validate(payload)


def test_member_processing_names_exactly_the_group_for_every_producer():
    with pytest.raises(ValidationError, match="exactly the group"):
        request(processing={W1: {}})
    payload = request().model_dump(mode="json")
    payload["geometry"]["required_features"] = []
    assert SolveRequest.model_validate(payload).geometry.branch_processing([G_LF])


def _solve(solid, record, monkeypatch, solve_request):
    from server.solver import official_beat

    monkeypatch.setattr(official_beat, "engine_version", lambda: "1.2.3")
    captured = []

    def native(compiled, **kwargs):
        captured.append(compiled)
        rows = tuple(parse_transducer_frequency(distinct(compiled, f), compiled, f) for f in [100.0, 300.0, 500.0])
        kwargs["on_frequency_result"](TransducerSweep(rows[:1], False, 3))
        return TransducerSweep(rows, False, 3)

    monkeypatch.setattr(official_beat, "solve_transducer_compiled", native)
    partial = []
    result = jobs.solve(solid, solve_request, record, backend="cpu",
                        result_callback=lambda revision, frame: partial.append(frame))
    return result, captured[0], partial


@pytest.mark.parametrize("producer", ["cad", "mesh-file"])
@pytest.mark.parametrize("normalization", ["rms_velocity", "acceleration_legacy"])
def test_identical_explicit_physics_ignores_geometry_producer(solid, record, monkeypatch, producer, normalization):
    """Same admitted system, native wire, retained bases and recombined result."""
    monkeypatch.setattr(jobs.time, "time", lambda: 1000.)
    assembly = request(excitation_normalization=normalization)
    imported = request(required_features=[], excitation_normalization=normalization)
    other_record = {**record, "producer": producer, "required_features": []}
    assert jobs.preflight(other_record, solid, imported.geometry.drive_channels, backend="cpu") is None
    before, compiled_before, _ = _solve(solid, record, monkeypatch, assembly)
    after, compiled_after, _ = _solve(solid, other_record, monkeypatch, imported)
    assert compiled_after.wire == compiled_before.wire
    assert after == before
    replay = SolveRequest.model_validate_json(imported.model_dump_json())
    stored = {key: value for key, value in after.items() if not key.startswith("_")}
    recombined = recombine_stored_results(stored, after["_channel_bases_npz"], replay.geometry.combine, replay)
    np.testing.assert_array_equal(recombined["channels"]["combined"]["spl_on_axis"]["spl"],
                                  after["channels"]["combined"]["spl_on_axis"]["spl"])


def test_acceleration_convention_changes_only_prescribed_bases(solid, record, monkeypatch):
    velocity, _, _ = _solve(solid, record, monkeypatch, request(excitation_normalization="rms_velocity"))
    acceleration, _, partial = _solve(solid, record, monkeypatch, request(
        required_features=[], excitation_normalization="acceleration_legacy"))
    v = deserialize_channel_bases(velocity["_channel_bases_npz"])["results_by_id"]
    a = deserialize_channel_bases(acceleration["_channel_bases_npz"])["results_by_id"]
    scale = 1 / (-2j * np.pi * np.asarray([100., 300., 500.]))
    for name in ("horn-throat", W2):
        np.testing.assert_array_equal(a[name].pressure_complex, v[name].pressure_complex * scale[:, None, None])
        np.testing.assert_array_equal(a[name].sphere_pressure_complex, v[name].sphere_pressure_complex * scale[:, None])
    np.testing.assert_array_equal(a[W1].pressure_complex, v[W1].pressure_complex)
    assert partial[0]["channels"]["horn-throat"]["spl_on_axis"]["spl"][0] == acceleration["channels"]["horn-throat"]["spl_on_axis"]["spl"][0]


def test_fem_does_not_silently_ignore_an_explicit_unsupported_normalization():
    from server.jobs.runtime.imported_refusals import _imported_request_refusal

    record = {"fem_regions": [{}]}
    explicit = request(required_features=[], excitation_normalization="rms_velocity")
    assert _imported_request_refusal(explicit, record)[0] == "fem_excitation_normalization_unsupported"
    # Missing fields keep the retained FEM decoder and admission path.
    legacy = request(required_features=[])
    refusal = _imported_request_refusal(legacy, record)
    assert refusal is None or refusal[0] != "fem_excitation_normalization_unsupported"


def test_mixed_results_keep_each_basis_and_build_the_crossover_once(solid, record, monkeypatch):
    solve_request = request()
    result, compiled, partial = _solve(solid, record, monkeypatch, solve_request)
    assert result["channel_order"] == ["horn-throat", W1, W2, "combined"]
    hf, woofer, prescribed = (result["channels"][key] for key in ("horn-throat", W1, W2))
    assert hf["metadata"]["source_normalization"] == "native_rms_velocity"
    assert hf["metadata"]["drive"] == {"velocity_rms_m_per_s": 1.0, "motion": "normal"}
    assert "impedance" not in hf and "impedance" in woofer
    assert woofer["metadata"]["impedance_units"] == "ohms"
    assert woofer["metadata"]["driver"]["cone_excursion_mm"]["peak_mm"] > 0
    assert prescribed["metadata"]["beat_transducer"]["undriven_transducers"] == "shorted"
    combined = result["channels"]["combined"]
    assert combined["metadata"]["combine"]["member_channels"] == {G_LF: [W1, W2], G_HF: ["horn-throat"]}
    assert combined["metadata"]["combine"]["member_processing"][W2] == {"weight": 1.0, "delay_ms": 0.25, "invert": True}
    assert "impedance" not in combined  # never a sum of basis impedances
    bases = deserialize_channel_bases(result["_channel_bases_npz"])
    assert bases["channel_ids"] == ["horn-throat", W1, W2]
    # Provisional frames carry no crossover: only the durable final does.
    assert all("combined" not in frame["channels"] for frame in partial)
    # Stored recombination rebuilds the same system from the retained bases.
    stored = {key: value for key, value in result.items() if not key.startswith("_")}
    again = recombine_stored_results(stored, result["_channel_bases_npz"], solve_request.geometry.combine, solve_request)
    np.testing.assert_allclose(again["channels"]["combined"]["spl_on_axis"]["spl"], combined["spl_on_axis"]["spl"])


def test_assembly_without_a_combination_can_recombine_its_stored_bases(solid, record, monkeypatch):
    """Old assembly runs retain the bases and processing even with combine off."""
    from copy import deepcopy

    processing = {W1: {"weight": 0.7}, W2: {"weight": 0.8, "delay_ms": 0.25, "invert": True}}
    old_request = request(combine=False, processing=processing)
    result, _compiled, _partial = _solve(solid, record, monkeypatch, old_request)
    assert "combined" not in result["channels"]
    stored = {key: value for key, value in result.items() if not key.startswith("_")}
    before = deepcopy(stored)
    spec = request(processing=processing).geometry.combine
    updated = recombine_stored_results(stored, result["_channel_bases_npz"], spec, old_request)
    assert updated["channel_order"] == ["horn-throat", W1, W2, "combined"]
    combined = updated["channels"]["combined"]
    assert combined["metadata"]["recombined"] is True
    assert combined["metadata"]["combine"]["member_processing"][W2] == processing[W2]
    assert np.isfinite(combined["spl_on_axis"]["spl"]).all()
    assert stored == before
    assert old_request.geometry.combine is None


def test_removing_drivers_preserves_the_prescribed_hf_velocity_basis(make_mesh, record, monkeypatch):
    from server.solver import beat_imported, official_beat
    from server.solver.beat_adapter.results import SweepResult, acceleration_scale

    solid = make_mesh([[0, 0, 0], [-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0], [0, 0, 1]],
                     [[0, 2, 1], [0, 3, 2], [0, 4, 3], [0, 1, 4],
                      [1, 2, 5], [2, 3, 5], [3, 4, 5], [4, 1, 5]], [2, 3, 4, 1, 1, 1, 1, 1])
    driven = request(combine=False)
    mixed, _compiled, _partial = _solve(solid, record, monkeypatch, driven)
    payload = driven.model_dump(mode="json")
    for channel in payload["geometry"]["drive_channels"]:
        channel.pop("exterior_transducer", None)
    fallback = SolveRequest.model_validate(payload)
    monkeypatch.setattr(official_beat, "production_statuses", lambda **kw: {"cpu": {"available": True}})

    def native(compiled, **kwargs):
        frequencies = np.asarray(compiled.wire["frequencies_hz"])
        scale = np.asarray([acceleration_scale(f) for f in frequencies])
        channels = {}
        for index, channel_id in enumerate(compiled.channel_ports):
            pressure = np.full((len(frequencies), len(compiled.layout.planes), len(compiled.layout.angles_deg)), 1 + 2j)
            pressure *= ((index + 1) * frequencies / 100 * scale)[:, None, None]
            sphere = None if "sphere" not in compiled.layout.points_m else np.repeat(
                pressure[:, 0, :1], len(compiled.layout.points_m["sphere"]), axis=1)
            channels[channel_id] = SweepResult(frequencies, pressure, np.zeros(pressure.shape),
                np.ones(len(frequencies), dtype=complex), compiled.layout.angles_deg, list(compiled.layout.planes),
                sphere, compiled.layout.sphere_theta_deg, compiled.layout.sphere_phi_deg,
                None, None, False, len(frequencies), [])
        return channels

    monkeypatch.setattr(official_beat, "solve_compiled", native)
    result = beat_imported.solve_imported_beat_from_msh_text(solid, fallback, record, backend="cpu")
    before = deserialize_channel_bases(mixed["_channel_bases_npz"])["results_by_id"]["horn-throat"]
    after = deserialize_channel_bases(result["_channel_bases_npz"])["results_by_id"]["horn-throat"]
    np.testing.assert_allclose(after.pressure_complex, before.pressure_complex, rtol=1e-12)
    np.testing.assert_allclose(after.sphere_pressure_complex, before.sphere_pressure_complex, rtol=1e-12)
    np.testing.assert_allclose(result["channels"]["horn-throat"]["spl_on_axis"]["spl"],
                               mixed["channels"]["horn-throat"]["spl_on_axis"]["spl"])
    assert result["channels"]["horn-throat"]["metadata"]["source_normalization"] == "native_rms_velocity"


@pytest.mark.parametrize(("channel_id", "normalization"), [
    ("horn-throat", "native_rms_velocity"), (W1, "native_rms_voltage")])
def test_mixed_pressure_basis_archive_preserves_its_physical_normalization(solid, record, monkeypatch, channel_id, normalization):
    result, _compiled, _partial = _solve(solid, record, monkeypatch, request())
    exported = export_pressure_basis(result["_channel_bases_npz"], result, channel_id)
    retained = deserialize_channel_bases(result["_channel_bases_npz"])["results_by_id"][channel_id]
    with np.load(io.BytesIO(exported.content), allow_pickle=False) as archive:
        assert archive["source_normalization"].item() == normalization
        np.testing.assert_allclose(archive["pressure_complex"], np.conjugate(retained.pressure_complex))
        np.testing.assert_allclose(archive["frequencies_hz"], retained.frequencies_hz)




def test_member_delay_polarity_and_weight_precede_the_group_sum():
    freqs = np.asarray([100.0, 1000.0])
    angles = np.asarray([0.0, 10.0])

    def result(value):
        field = np.full((2, 1, 2), value, dtype=np.complex128)
        from types import SimpleNamespace
        return SimpleNamespace(frequencies_hz=freqs, observation_angles_deg=angles, observation_planes=["horizontal"],
                               pressure_complex=field, sphere_pressure_complex=None, solver_log=[])

    results = {"a": result(1 + 0j), "b": result(2 + 0j), "c": result(1 + 0j)}
    processing = {"a": {"weight": 0.5}, "b": {"delay_ms": 0.25, "invert": True}}
    combined, payload = combine_drive_channels(
        results, members=["g", "c"], channels={
            "g": {"gain": {"mode": "manual", "db": 0}, "delay": {"mode": "manual", "ms": 0}, "invert": False},
            "c": {"gain": {"mode": "manual", "db": 0}, "delay": {"mode": "manual", "ms": 0}, "invert": False}},
        member_channels={"g": ["a", "b"], "c": ["c"]}, member_labels={"g": "LF", "c": None},
        member_processing=processing)
    expected_group = 0.5 * 1 + engineering_to_solver(-np.exp(-1j * 2 * np.pi * freqs * 0.25e-3)) * 2
    np.testing.assert_allclose(combined.pressure_complex[:, 0, 0], expected_group + 1)
    assert payload["member_processing"] == processing
    weights = member_processing_weights(freqs, processing)
    np.testing.assert_allclose(weights["a"], 0.5)
    np.testing.assert_allclose(weights["b"], engineering_to_solver(-np.exp(-1j * 2 * np.pi * freqs * 0.25e-3)))


def test_an_all_driver_assembly_keeps_its_bases_and_a_driverless_one_uses_the_prescribed_route(solid, record, monkeypatch):
    result, compiled, _ = _solve(solid, record, monkeypatch, request(w2_driver=True, combine=False))
    assert compiled.transducer_ids == (f"transducer:{W1}", f"transducer:{W2}")
    assert "_channel_bases_npz" in result and "channel_bases_unavailable_reason" not in result["metadata"]
    payload = request(combine=False).model_dump(mode="json")
    payload["geometry"]["drive_channels"][1].pop("exterior_transducer")
    assert not jobs.enabled(SolveRequest.model_validate(payload).geometry.drive_channels)


@pytest.mark.parametrize("kind", ["series", "parallel"])
@pytest.mark.parametrize("combine", [True, False])
def test_wired_solve_and_stored_recombine_share_network_math(solid, record, monkeypatch, kind, combine):
    from server.tests.beat_adapter.conftest import wire
    from server.solver.driver_network import prepare_network_bases

    network_id = "00000000-0000-4000-8000-0000000000c1"
    solve_request = request(w2_driver=True, processing={W1: {}, W2: {}}, combine=combine,
        wiring_groups=[dict(id=network_id, label="LF network", network=dict(kind=kind, children=[
            dict(kind="driver", radiator_id=W1), dict(kind="driver", radiator_id=W2)]))])
    original = distinct

    def coupled(compiled, frequency):
        data = original(compiled, frequency)
        for item in data["quantities"]:
            if item["quantity"] == "voice_coil_current":
                item["values"] = wire([[.01+.02j, .02-.01j], [.3-.05j, .04+.02j], [.04+.02j, .2-.06j]])
        return data

    monkeypatch.setitem(globals(), "distinct", coupled)
    result, _compiled, partial = _solve(solid, record, monkeypatch, solve_request)
    assert all(network_id in frame["channel_order"] and "wiring" not in frame["channels"][network_id]["metadata"] for frame in partial)
    network = result["channels"][network_id]
    bundle = deserialize_channel_bases(result["_channel_bases_npz"])
    prepared = prepare_network_bases(bundle["results_by_id"], solve_request.geometry)
    diagnostics = prepared.diagnostics({W1: np.ones(3)})
    assert network["impedance"]["real"] == diagnostics["networks"][network_id]["input_impedance_ohm"]["real"]
    assert set(network["metadata"]["wiring"]["drivers"]) == {W1, W2}
    spec = request(w2_driver=True, processing={W1: {}, W2: {}}).geometry.combine
    stored = {key: value for key, value in result.items() if not key.startswith("_")}
    replay = recombine_stored_results(stored, result["_channel_bases_npz"], spec, solve_request)
    if combine:
        assert replay["channels"]["combined"]["metadata"]["combine"]["wiring"] == result["channels"]["combined"]["metadata"]["combine"]["wiring"]
        np.testing.assert_allclose(replay["channels"]["combined"]["spl_on_axis"]["spl"], result["channels"]["combined"]["spl_on_axis"]["spl"])
    assert "max_output" not in replay["channels"]["combined"]["metadata"]["combine"]
    spec.channels[G_LF].gain.db = -9
    changed = recombine_stored_results(stored, result["_channel_bases_npz"], spec, solve_request)
    assert changed["channels"]["combined"]["metadata"]["combine"]["wiring"] != replay["channels"]["combined"]["metadata"]["combine"]["wiring"]
    from server.solver.errors import RecombineError

    spec.channels[G_LF].gain.mode = "max"
    with pytest.raises(RecombineError, match="choose Manual or Auto"):
        recombine_stored_results(stored, result["_channel_bases_npz"], spec, solve_request)


@pytest.mark.parametrize("engine", ["auto", "bempp", "metal"])
def test_wired_submission_needs_a_coupled_beat_engine(engine):
    payload = request(w2_driver=True, processing={W1: {}, W2: {}}).model_dump(mode="json")
    payload["geometry"]["wiring_groups"] = [dict(id="00000000-0000-4000-8000-0000000000c1", label="LF", network=dict(
        kind="series", children=[dict(kind="driver", radiator_id=W1), dict(kind="driver", radiator_id=W2)]))]
    payload["options"]["engine"] = engine
    with pytest.raises(ValidationError, match="Wired woofers need BEAT CPU or BEAT Metal"):
        SolveRequest.model_validate(payload)
