"""Parametric and imported Metal solves always run on the native interface.

The BEAT-compatible worker route was retired for both after it measured slower
with the same results. Only exterior-transducer networks still use the worker
(``server/tests/beat_adapter/test_metal_transducer_jobs.py``).
"""

from types import SimpleNamespace

import pytest

from server.solver import metal, metal_worker
from server.tests.test_engines_adapters import _cabinet_msh, _context, _result as native_result
from server.tests.test_imported_jobs import _native_result as imported_result, _request, _roled_record

NATIVE_REASON = "This Metal solve requires the native interface"
PREPARING = "Preparing Metal mesh and solver request"


def _no_worker(monkeypatch) -> None:
    for name in ("worker_available", "validate_worker_request", "transducer_worker_available",
                 "solve_transducer_worker", "validate_transducer_worker_request"):
        monkeypatch.setattr(metal_worker, name, lambda *_a, _n=name, **_k: pytest.fail(f"worker {_n} used"))
    assert not hasattr(metal_worker, "select_worker_request")
    assert not hasattr(metal_worker, "solve_worker")


def _assert_native_setup_stage(stages, configuring: str) -> None:
    """Configure first, then the native load's own setup stage, then any frequency."""

    names = [message for _stage, _fraction, message in stages]
    assert ("setup", 0.0, PREPARING) in stages
    assert names.index(configuring) < names.index(PREPARING)
    assert all(stage != "frequency_solve" for stage, *_ in stages[:names.index(PREPARING)])


def test_parametric_metal_always_solves_on_the_native_interface(monkeypatch):
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(metal, "native_solve", lambda *_: native_result())
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "version": "test"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    _no_worker(monkeypatch)
    for axial in (False, True):
        stages = []
        response = metal.solve_metal_from_msh_text(_cabinet_msh(), _context(axial=axial, field_plane=False),
                                                 stage_callback=lambda *values: stages.append(values))
        assert response["metadata"]["metal"]["interface"] == "native"
        assert response["metadata"]["metal"]["interface_reason"] == NATIVE_REASON
        _assert_native_setup_stage(stages, "Configuring Metal BEM solve")


def test_imported_single_source_solves_on_the_native_interface(monkeypatch, tmp_path):
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
    calls = []

    def native_multi_source(path, specs, config, *, frequencies_hz=None):
        calls.append(len(specs))
        result = imported_result()
        # Stream one live frame, as the native helper does per frequency.
        assert config.on_frequency_result(0, 100.0, {
            "observation_angles_deg": result.observation_angles_deg,
            "observation_planes": result.observation_planes,
            "observation_pressure_complex": result.pressure_complex[0],
            "observation_directivity_db": result.directivity_db[0],
            "impedance": result.impedance[0],
        }) is True
        return [result]

    monkeypatch.setattr(metal, "native_solve_multi_source", native_multi_source)
    _no_worker(monkeypatch)
    request = _request("wgi_" + "0" * 26, drive_channels=[{"id": "left", "source_ids": ["source-a"]}],
                       mesh={"rigid_size_mm": 8., "transition_mm": 20., "source_size_mm": {"source-a": 3.}})
    request.options.frequencies_hz = [100., 200.]
    mesh_path = tmp_path / "imported.msh"
    msh = _cabinet_msh()
    mesh_path.write_text(msh)
    record = _roled_record(mesh_path)
    record["source_tags"]["source-a"] = 2
    stages, streamed = [], []
    response = metal.solve_imported_metal_from_msh_text(
        msh, request, record, stage_callback=lambda *values: stages.append(values),
        result_callback=lambda index, frame: streamed.append((index, frame)),
    )
    assert calls == [1]
    assert streamed and streamed[0][1]["channels"]["left"]["metadata"]["role"] == "HF"
    assert response["metadata"]["solver_engine"]["interface"] == "native"
    assert response["metadata"]["solver_engine"]["interface_reason"] == NATIVE_REASON
    assert response["channel_order"] == ["left"]
    assert response["channels"]["left"]["metadata"]["role"] == "HF"
    _assert_native_setup_stage(stages, "Configuring imported multi-source Metal BEM solve")
