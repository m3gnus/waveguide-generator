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
