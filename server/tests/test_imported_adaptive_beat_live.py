"""Live adaptive channels agree with BEAT's joint fixed-grid solve."""

import os

import numpy as np
import pytest

from server.solver import beat_imported, official_beat
from server.solver.beat_runtime.manager import WorkerManager
from server.tests import test_imported_beat as cad


pytestmark = [pytest.mark.live, pytest.mark.slow, pytest.mark.real_runtime,
              pytest.mark.skipif(os.environ.get("WG2_RUN_LIVE") != "1",
                                 reason="set WG2_RUN_LIVE=1 for real BEAT qualification")]


def _two_channel_case():
    request = cad._request(drive_channels=[
        {"id": "left", "source_ids": ["source-a"]},
        {"id": "right", "source_ids": ["source-b"]},
    ], mesh={"rigid_size_mm": 8., "transition_mm": 20.,
             "source_size_mm": {"source-a": 3., "source-b": 3.}})
    request.options.frequencies_hz = None
    request.options.frequency_range = [500., 1200.]
    request.options.num_frequencies = 24
    request.options.polar_config.angle_range = (-30., 30., 3)
    request.options.polar_config.enabled_axes = ["horizontal"]
    request.options.polar_config.field_plane = False
    # The recording fixture's tetrahedron is closed but two faces point in.
    # Orient both before asking the real exterior solver to assemble it.
    mesh = (cad.MESH.replace("1 2 2 1 1 1 2 3", "1 2 2 1 1 1 3 2")
            .replace("4 2 2 103 4 1 3 4", "4 2 2 103 4 1 4 3"))
    record = cad._record(msh_text=mesh)
    return mesh, request, record


def test_adaptive_multi_channel_matches_joint_solve_at_acquired_frequencies(monkeypatch):
    pytest.importorskip("beat_engine")
    if not os.environ.get("WG2_BEAT_JULIA"):
        pytest.skip("Set WG2_BEAT_JULIA to the Julia runtime used for this live check")
    # Runtime provisioning is qualified separately; this check exercises real
    # worker negotiation and numerical solves with an explicitly chosen Julia.
    monkeypatch.setattr(official_beat, "production_statuses",
                        lambda **kwargs: {"cpu": {"available": True, "surface_traces": False}})
    mesh, request, record = _two_channel_case()
    manager = WorkerManager(mode="child")
    results = {}
    try:
        for adaptive in (False, True):
            request.options.adaptive_frequency_sampling = adaptive
            native = {}
            envelope = beat_imported.solve_imported_beat_from_msh_text(
                mesh, request, record, backend="cpu", _worker_manager=manager,
                _julia_executable=os.environ["WG2_BEAT_JULIA"],
                _native_result_callback=lambda channel, value: native.__setitem__(channel, value),
            )
            assert set(envelope["channels"]) == {"left", "right"}
            results[adaptive] = native
    finally:
        manager.shutdown()

    for channel in ("left", "right"):
        fixed, adaptive = results[False][channel], results[True][channel]
        solved = np.asarray(adaptive.frequency_status) == "solved"
        assert np.count_nonzero(solved) >= 2
        np.testing.assert_allclose(adaptive.frequencies_hz, fixed.frequencies_hz)
        np.testing.assert_allclose(adaptive.pressure_complex[solved], fixed.pressure_complex[solved],
                                   rtol=2e-5, atol=1e-10)
        np.testing.assert_allclose(adaptive.impedance[solved], fixed.impedance[solved],
                                   rtol=2e-5, atol=1e-10)
        assert np.isfinite(adaptive.pressure_complex).all()
        print(f"{channel}: compared {np.count_nonzero(solved)} acquired rows")
    assert not np.allclose(results[True]["left"].pressure_complex,
                           results[True]["right"].pressure_complex, rtol=1e-5, atol=1e-10)
