"""Heterogeneous assembly driver groups retain their physical ratings."""

import pytest

from server.jobs.models import SolveRequest
from server.solver.recombine import recombine_stored_results
from .test_assembly_native_solve import G_LF, W1, W2, _solve, request
from .test_assembly_native_solve import record as record, solid as solid


@pytest.mark.parametrize("gain_mode", ["manual", "auto"])
def test_heterogeneous_driver_group_packages_and_recombines_with_real_ratings(solid, record, monkeypatch, gain_mode):
    payload = request(w2_driver=True).model_dump(mode="json")
    for channel, xmax, power in zip(payload["geometry"]["drive_channels"][1:], [2.0, 7.0], [30.0, 100.0]):
        channel["exterior_transducer"].update(version=2, xmax_mm=xmax, power_w=power)
    payload["geometry"]["combine"]["channels"][G_LF]["gain"] = {"mode": gain_mode, **({"db": -3} if gain_mode == "manual" else {})}
    payload["geometry"]["driver_groups"][1]["member_processing"][W1] = {"weight": 0.25}
    solve_request = SolveRequest.model_validate(payload)
    result, _compiled, _partial = _solve(solid, record, monkeypatch, solve_request)
    combined = result["channels"]["combined"]
    combine = combined["metadata"]["combine"]
    assert combine["channels"][G_LF]["gain_max_db"] is None
    assert "max_output" not in combine
    ratings = combine["driver_basis_limits"][G_LF]
    assert ratings["xmax_mm"] is None
    assert ratings["physical_driver_limits"][W1] == {"xmax_mm": 2.0, "rated_power_w": 30.0,
                                                      "max_voltage_v": None, "member_weight": 0.25}
    assert ratings["physical_driver_limits"][W2]["xmax_mm"] == 7.0
    again = recombine_stored_results(result, result["_channel_bases_npz"], solve_request.geometry.combine, solve_request)
    assert again["channels"]["combined"]["metadata"]["combine"] == combined["metadata"]["combine"]
    assert result["channels"][W1]["metadata"]["driver"]["spec"]["xmax_mm"] == 2.0
    assert result["channels"][W2]["metadata"]["driver"]["spec"]["xmax_mm"] == 7.0
