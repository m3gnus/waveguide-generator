"""Legacy comparison identity comes from the saved request's replay decoder."""
import hashlib
import json

import pytest

from server.jobs.result_basis import with_result_basis
from server.tests.test_imported_adapter_replay import saved_request


@pytest.mark.parametrize("origin,engine,explicit,expected", [
    ("cad", "beat-cpu", None, "acceleration_legacy"),
    ("file", "metal", None, "acceleration_legacy"),
    ("assembly", "beat-cpu", None, "rms_velocity"),
    ("assembly", "bempp", None, "acceleration_legacy"),
    ("cad", "beat-cpu", "rms_velocity", "rms_velocity"),
    ("assembly", "beat-cpu", "acceleration_legacy", "acceleration_legacy"),
])
def test_read_time_basis_uses_replay_decoder(origin, engine, explicit, expected):
    changes = {} if explicit is None else {"excitation_normalization": explicit}
    request = saved_request(origin, engine, "prescribed", **changes)
    payload = {"channels": {"left": {"frequencies": [100], "spl_on_axis": {"spl": [72]}}}}
    text = json.dumps(payload)
    served, digest = with_result_basis(text, "stored", {"config_json": request.model_dump(mode="json")})
    decoded = json.loads(served)
    assert decoded["metadata"]["excitation_normalization"] == expected
    assert decoded["channels"]["left"]["metadata"]["source_normalization"] == (
        "native_rms_velocity" if expected == "rms_velocity" else "unit_normal_acceleration")
    assert decoded["channels"]["left"]["spl_on_axis"] == {"spl": [72]}
    assert json.loads(text) == payload
    assert digest == hashlib.sha256(served.encode()).hexdigest()


def test_existing_voltage_and_unknown_identity_are_preserved():
    payload = {"channels": {name: {"metadata": {"source_normalization": basis}}
                           for name, basis in [("v", "native_rms_voltage"), ("u", "future")]}}
    text = json.dumps(payload)
    assert with_result_basis(text, "stored", {}) == (text, "stored")


def test_missing_request_stays_unknown_and_combined_is_not_tagged_as_prescribed():
    payload = {"channels": {"a": {"frequencies": [100]},
                            "combined": {"metadata": {"combine": {"members": ["a", "b"]}}}}}
    text = json.dumps(payload)
    assert with_result_basis(text, "stored", {}) == (text, "stored")
    served, _ = with_result_basis(text, "stored", {"config_json": saved_request("cad", "metal", "prescribed").model_dump(mode="json")})
    assert "source_normalization" not in json.loads(served)["channels"]["combined"]["metadata"]


def test_legacy_driver_identity_comes_from_saved_channel_when_public_tag_is_missing():
    request = saved_request("cad", "beat-cpu", "coupled")
    payload = {"channels": {"left": {"frequencies": [100]}, "right": {"frequencies": [100]}}}
    served, _ = with_result_basis(json.dumps(payload), "stored", {"config_json": request.model_dump(mode="json")})
    assert all(channel["metadata"]["source_normalization"] == "voltage_driven_driver_lem"
               for channel in json.loads(served)["channels"].values())


def test_voltage_driven_lem_channel_is_never_tagged_as_a_prescribed_basis():
    # LEM paths keep the solver's prescribed tag after scaling to the drive voltage.
    payload = {"channels": {"lem": {"metadata": {"source_normalization": "native_rms_velocity",
                                                 "impedance_drive": "voltage"}},
                            "coupled": {"metadata": {"source_normalization": "native_rms_voltage",
                                                     "impedance_drive": "voltage"}}}}
    served, _ = with_result_basis(json.dumps(payload), "stored", {})
    channels = json.loads(served)["channels"]
    assert channels["lem"]["metadata"]["source_normalization"] == "voltage_driven_driver_lem"
    assert channels["coupled"]["metadata"]["source_normalization"] == "native_rms_voltage"


def test_deferred_passive_cardioid_driver_stays_prescribed():
    payload = {"channels": {"mf": {"metadata": {"source_normalization": "native_rms_velocity",
                                                "impedance_drive": "rms_velocity", "driver": {"re_ohm": 6},
                                                "driver_coupling_deferred_to": "passive_cardioid"}}}}
    served, _ = with_result_basis(json.dumps(payload), "stored", {})
    assert json.loads(served)["channels"]["mf"]["metadata"]["source_normalization"] == "native_rms_velocity"


def _identity_cases():
    imported = {"config_json": saved_request("cad", "beat-cpu", "coupled").model_dump(mode="json")}
    prescribed = {"config_json": saved_request("assembly", "beat-cpu", "prescribed").model_dump(mode="json")}
    parametric = {"config_json": {"geometry": {"type": "parametric", "design": {"L": 120}}}}
    two = {"channels": {"left": {"frequencies": [100, 200], "spl_on_axis": {"spl": [72, 73.5]}},
                        "right": {"frequencies": [100, 200], "metadata": {"driver": {"re_ohm": 6}}},
                        "combined": {"metadata": {"combine": {"members": ["left", "right"]}}}}}
    tagged = {"metadata": {"excitation_normalization": "rms_velocity"},
              "channels": {"v": {"metadata": {"source_normalization": "native_rms_voltage"}},
                           "lem": {"metadata": {"source_normalization": "native_rms_velocity",
                                                "impedance_drive": "voltage"}}}}
    single = {"frequencies": [100.0, 1e3], "metadata": {"solver": "beat"}, "spl": [80.25, 81.0]}
    bare = {"frequencies": [100.0], "spl": [80.0]}
    return [
        (two, imported), (two, prescribed), (two, parametric), (two, {}),
        (tagged, {}), (tagged, imported), (single, parametric), (single, {}), (bare, parametric),
        ({"channels": {}, "frequencies": [1.0]}, parametric),
    ]


@pytest.fixture
def fresh_served_cache():
    from server.jobs import result_basis

    result_basis._SERVED.clear()
    yield
    result_basis._SERVED.clear()


@pytest.mark.parametrize("case", range(len(_identity_cases())))
def test_served_identity_is_byte_identical_and_parsed_once(monkeypatch, fresh_served_cache, case):
    from server.jobs import result_basis

    payload, row = _identity_cases()[case]
    text = json.dumps(payload, separators=(", ", ": "))
    digest = hashlib.sha256(text.encode()).hexdigest()
    expected = with_result_basis(text, digest, row)
    assert result_basis.served_result_basis(text, digest, row) == expected

    def no_parse(*_args, **_kwargs):
        raise AssertionError("a repeated read parsed the stored record")

    monkeypatch.setattr(result_basis.json, "loads", no_parse)
    for _ in range(3):
        served = result_basis.served_result_basis(text, digest, row)
        assert served == expected
        # Unchanged records keep serving the stored string itself, not a copy.
        assert (served[0] is text) == (expected[0] == text)


def test_served_identity_follows_the_saved_request_not_just_the_digest(fresh_served_cache):
    from server.jobs import result_basis

    payload, _ = _identity_cases()[0]
    text = json.dumps(payload)
    digest = hashlib.sha256(text.encode()).hexdigest()
    rows = [{"config_json": saved_request("cad", engine, mode).model_dump(mode="json")}
            for engine, mode in (("beat-cpu", "coupled"), ("beat-cpu", "prescribed"), ("bempp", "prescribed"))]
    rows += [{}, {"config_json": {"geometry": {"type": "parametric"}}}]
    for _ in range(2):
        for row in rows:
            assert result_basis.served_result_basis(text, digest, row) == with_result_basis(text, digest, row)


@pytest.mark.parametrize("case", range(len(_identity_cases())))
def test_in_memory_identity_matches_the_text_round_trip(case):
    from server.jobs.json_encoding import result_json_text
    from server.jobs.result_basis import with_result_basis_payload

    payload, row = _identity_cases()[case]
    before = json.dumps(payload)
    presented = with_result_basis_payload(payload, row)
    # The crossover reply before this change: dump, present as text, parse.
    reference = json.loads(with_result_basis(json.dumps(payload), "", row)[0])
    assert result_json_text(presented) == result_json_text(reference)
    assert json.dumps(payload) == before, "the stored results object was mutated"


def test_results_route_serves_the_same_bytes_on_every_read(tmp_path, fresh_served_cache):
    import asyncio
    from datetime import datetime

    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    now = datetime.now().isoformat()
    request = saved_request("cad", "beat-cpu", "coupled").model_dump(mode="json")
    store.create_job({
        "id": "run", "status": "complete", "created_at": now, "updated_at": now, "queued_at": now,
        "completed_at": now, "progress": 1.0, "stage": "complete", "stage_message": "complete",
        "config_json": request, "config_summary_json": {}, "task_metadata": {},
    })
    store.store_results("run", _identity_cases()[0][0])
    expected = with_result_basis(*store.get_results_payload("run"), store.get_job_row("run"))
    assert "voltage_driven_driver_lem" in expected[0]

    async def reads():
        runtime = JobRuntime(store)
        await runtime.start()
        try:
            return [await runtime.get_results_payload("run") for _ in range(3)]
        finally:
            await runtime.shutdown()

    assert asyncio.run(reads()) == [expected] * 3
