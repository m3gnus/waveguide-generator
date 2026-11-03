"""Mmd suggestions reuse the coupling guard and never change driver physics."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hornlab_sim.methods import driver_coupling, radiation_impedance
import pytest

from server.drivers.api import create_drivers_router
from server.drivers.library import DriverLibrary
from server.drivers.mass import suggest_mmd
from server.drivers.models import ExteriorDriverInputs
from server.jobs.models import DriveChannel


@pytest.fixture
def client(tmp_path: Path):
    app = FastAPI()
    app.include_router(create_drivers_router(DriverLibrary(tmp_path)))
    with TestClient(app) as client:
        yield client


def test_suggestion_uses_the_pinned_helper(monkeypatch):
    calls = []

    def correction(driver, derived, rho):
        calls.append((driver, derived, rho))
        return 0.0163, 0.0016, "Mms_corrected"

    monkeypatch.setattr(driver_coupling, "_effective_mmd", correction)
    result = suggest_mmd(17.9, 124.7).suggestion
    assert result.mmd_kg == 0.0163
    driver, derived, rho = calls[0]
    assert driver is derived
    assert driver.Mms == pytest.approx(0.0179)
    assert driver.Sd == pytest.approx(0.01247)
    assert driver.Mmd is None
    assert rho == radiation_impedance.RHO_AIR


def test_bundled_examples_and_no_library_mutation(client):
    rs180 = client.get("/api/drivers", params={"q": "Dayton RS180-8"}).json()["items"][0]
    before = client.get(f"/api/drivers/{rs180['id']}").json()
    answer = client.get("/api/drivers/mmd-suggestion", params={
        "mms_g": before["spec"]["mms_g"], "sd_cm2": before["spec"]["sd_cm2"],
    })
    assert answer.status_code == 200
    suggestion = answer.json()["suggestion"]
    assert suggestion["mmd_kg"] == pytest.approx(0.0162940347065)
    assert suggestion["correction_fraction"] == pytest.approx(0.08971873148)
    assert client.get(f"/api/drivers/{rs180['id']}").json() == before
    refused = client.get("/api/drivers/mmd-suggestion", params={"mms_g": 55, "sd_cm2": 850}).json()
    assert refused["suggestion"] is None
    assert "exceeds 50%" in refused["reason"]


@pytest.mark.parametrize("fraction,accepted,warned", [(0.3, True, False), (0.4, True, True), (0.5, True, True), (0.50001, False, False)])
def test_existing_guard_boundaries(fraction, accepted, warned):
    correction = suggest_mmd(1000, 100).suggestion.correction_kg
    result = suggest_mmd(correction / fraction * 1000, 100)
    assert (result.suggestion is not None) is accepted
    assert (result.warning is not None) is warned
    if not accepted:
        assert "exceeds 50%" in result.reason


@pytest.mark.parametrize("key,value", [("mms_g", 0), ("mms_g", -1), ("sd_cm2", 0), ("sd_cm2", -1), ("mms_g", "nan"), ("sd_cm2", "inf")])
def test_invalid_inputs_are_refused(client, key, value):
    params = {"mms_g": 17.9, "sd_cm2": 124.7, key: value}
    assert client.get("/api/drivers/mmd-suggestion", params=params).status_code == 422


def test_missing_helper_offers_no_suggestion(monkeypatch):
    monkeypatch.delattr(driver_coupling, "_effective_mmd")
    result = suggest_mmd(17.9, 124.7)
    assert result.suggestion is None
    assert "unavailable" in result.reason


def test_provenance_roundtrip_is_separate_from_physics():
    inputs = ExteriorDriverInputs(mms_g=17.9, driver_id="library:rs180", derived_mmd=suggest_mmd(17.9, 124.7).suggestion)
    physics = dict(version=2, re_ohm=6.4, le_h=0.00073, bl_n_per_a=7.82,
                   mmd_kg=inputs.derived_mmd.mmd_kg, cms_m_per_n=0.00112,
                   rms_n_s_per_m=1, sd_m2=0.01247, motion_axis=[0, 0, 1])
    original = DriveChannel(id="lf", source_ids=["front"], motion="axial", exterior_transducer=physics)
    accepted = DriveChannel.model_validate({**original.model_dump(), "exterior_driver_inputs": inputs.model_dump()})
    reopened = DriveChannel.model_validate_json(accepted.model_dump_json())
    assert reopened.exterior_driver_inputs == inputs
    assert reopened.exterior_transducer == original.exterior_transducer
    assert "exterior_driver_inputs" not in original.model_dump()
