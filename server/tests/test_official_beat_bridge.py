"""Standalone official BEAT bridge protocol and application-boundary tests."""

from __future__ import annotations

import asyncio
import base64
import copy
from pathlib import Path
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from server.solver import official_beat as bridge
from server.solver.context import SolverContext
from server.solver.result_mapping import _gmsh22_observation_frame


MESH = Path(__file__).resolve().parents[1] / "solver" / "warmup_mesh.msh"


def _context(**overrides) -> SolverContext:
    values = {"design": None, "frequency_range": (500.0, 2000.0), "num_frequencies": 3}
    values.update(overrides)
    return SolverContext(**values)


def _wire(values: np.ndarray) -> dict:
    array = np.asarray(values, dtype="<c8")
    return {"encoding": "base64", "dtype": "complex64", "shape": list(array.shape),
            "order": "C", "byte_order": "little",
            "content_base64": base64.b64encode(array.tobytes()).decode("ascii")}


def _result(frequency: float, planes: list[str], angles: np.ndarray) -> dict:
    quantities = [
        {"id": f"pressure:{plane}", "quantity": "exterior_pressure", "unit": "Pa",
         "axes": ["excitation", "observation"],
         "values": _wire(np.full((1, len(angles)), 2 + 3j))}
        for plane in planes
    ]
    quantities.append({"id": "impedance", "quantity": "radiation_impedance",
                       "unit": "N*s/m", "axes": ["radiator"], "values": _wire(np.asarray([4 + 5j]))})
    return {"schema_version": 2, "freq_hz": frequency,
            "excitation_port_ids": [bridge.SOURCE_ID], "quantities": quantities,
            "diagnostics": {"phasor_convention": bridge.PHASOR,
                            "bem_backend": "cpu", "precision": "float32", "symmetry": "off"}}


def test_compiled_request_has_absolute_owned_files_and_exact_frequency_axis(tmp_path) -> None:
    msh = MESH.read_text()
    context = _context(frequencies_hz=(500.0, 700.0, 2000.0))
    request, planes, angles, area = bridge.build_compiled_request(
        tmp_path / "surface.msh", tmp_path / "cancel.marker", context, msh,
        backend="cpu", precision="float32",
    )
    assert request["frequencies_hz"] == [500.0, 2000.0, 700.0]
    assert request["compiled_system"]["meshes"][0]["file"].startswith(str(tmp_path))
    assert request["cancel_path"].startswith(str(tmp_path))
    assert request["compiled_system"]["boundaries"][0]["group"]["tag"] == 2
    assert request["compiled_system"]["regions"][0]["density_kg_per_m3"] > 0
    assert len(planes) >= 1 and len(angles) > 0 and area > 0


def test_millimetre_mesh_scales_origin_and_area_but_not_polar_radius(tmp_path) -> None:
    msh = MESH.read_text()
    context = _context()
    metre, _, _, area = bridge.build_compiled_request(
        tmp_path / "surface.msh", tmp_path / "cancel", context, msh,
        backend="cpu", precision="float32",
    )
    millimetre, _, _, scaled_area = bridge.build_compiled_request(
        tmp_path / "surface.msh", tmp_path / "cancel", context, msh,
        backend="cpu", precision="float32", mesh_scale_to_m=0.001,
    )
    assert millimetre["compiled_system"]["meshes"][0]["scale_to_m"] == 0.001
    assert scaled_area == pytest.approx(area * 1e-6)
    # Each cut retains its requested 2 m source-to-probe distance even when
    # the mesh's raw coordinate units are millimetres.
    a = np.asarray(metre["outputs"][0]["options"]["points_m"])
    b = np.asarray(millimetre["outputs"][0]["options"]["points_m"])
    np.testing.assert_allclose(a - a[0], b - b[0], atol=1e-12)
    _, raw_origin, _, _ = _gmsh22_observation_frame(
        msh, origin_at="mouth", symmetry_plane=None, aperture_tag=None,
    )
    assert np.linalg.norm(raw_origin) > 0.01
    np.testing.assert_allclose(b[0] - a[0], (0.001 - 1.0) * raw_origin, atol=1e-12)
    assert np.linalg.norm(b[0]) > 1.0


def test_metal_float64_is_refused_before_submission(tmp_path) -> None:
    with pytest.raises(bridge.OfficialBeatUnavailable, match="Metal.*float32"):
        bridge.build_compiled_request(
            tmp_path / "surface.msh", tmp_path / "cancel", _context(), MESH.read_text(),
            backend="metal", precision="float64",
        )


def test_signed_velocity_to_acceleration_scale_preserves_complex_phase() -> None:
    msh = MESH.read_text()
    _, planes, angles, area = bridge.build_compiled_request(
        Path("/tmp/mesh.msh"), Path("/tmp/cancel"), _context(), msh,
        backend="cpu", precision="float32",
    )
    pressure, impedance = bridge.parse_result(
        _result(500.0, planes, angles), frequency_hz=500.0,
        planes=planes, angles=angles, source_area_m2=area,
    )
    scale = 1 / (-1j * 2 * np.pi * 500)
    assert pressure.shape == (len(planes), len(angles))
    np.testing.assert_allclose(pressure, (2 + 3j) * scale)
    np.testing.assert_allclose(impedance, (4 + 5j) / area * scale)


@pytest.mark.parametrize("change", [
    lambda result: result.update(schema_version=1),
    lambda result: result.update(freq_hz=501.0),
    lambda result: result.update(excitation_port_ids=["other"]),
    lambda result: result["quantities"].append(copy.deepcopy(result["quantities"][0])),
    lambda result: result["quantities"][0].update(unit="dB"),
    lambda result: result["quantities"][0].update(axes=["observation", "excitation"]),
    lambda result: result["quantities"][0]["values"].update(dtype="float32"),
    lambda result: result["quantities"][0]["values"].update(order="F"),
    lambda result: result["quantities"][0]["values"].update(byte_order="big"),
    lambda result: result["quantities"][0]["values"].update(shape=[1, 1]),
    lambda result: result["quantities"][0]["values"].update(content_base64="a"),
    lambda result: result["quantities"][0]["values"].update(content_base64=""),
    lambda result: result["diagnostics"].update(phasor_convention="exp(+i omega t)"),
    lambda result: result["diagnostics"].update(symmetry="xy"),
    lambda result: result.update(schema_version=2.0),
    lambda result: result.update(freq_hz=True),
    lambda result: result["quantities"].append("not an object"),
    lambda result: result["quantities"][0].update(values=_wire(np.asarray([[complex(float("nan"), 0), 1j]]))),
])
def test_malformed_result_fails_closed(change) -> None:
    planes, angles = ["horizontal"], np.asarray([0.0, 90.0])
    result = _result(500.0, planes, angles)
    change(result)
    with pytest.raises(bridge.OfficialBeatProtocolError):
        bridge.parse_result(result, frequency_hz=500.0, planes=planes,
                            angles=angles, source_area_m2=1.0)


def test_direct_entry_refuses_unqualified_physics() -> None:
    msh = MESH.read_text()
    for context in (_context(source_motion="axial"), _context(quadrants=1),
                    _context(sim_type=1)):
        with pytest.raises(bridge.OfficialBeatUnavailable):
            bridge.solve_official_beat_from_msh_text(msh, context)


@pytest.mark.parametrize("mode", ["ground", "imported", "axial", "quarter", "baffle", "sphere"])
def test_run_refuses_unsupported_mode_before_mesh_or_artifact(monkeypatch, mode) -> None:
    context = _context(
        source_motion="axial" if mode == "axial" else "normal",
        quadrants=1 if mode == "quarter" else bridge.FULL_DOMAIN_QUADRANTS,
        sim_type=1 if mode == "baffle" else 2,
    )
    if mode == "sphere":
        context.polar_config["spherical_sampling"] = True
    monkeypatch.setattr(bridge.SolverContext, "from_request", lambda *_args, **_kwargs: context)
    monkeypatch.setattr(bridge, "build_solver_mesh", lambda *_args, **_kwargs: pytest.fail("meshed"))
    request = SimpleNamespace(
        options=SimpleNamespace(ground_plane=SimpleNamespace(enabled=mode == "ground"),
                                solver_mode="full_3d"),
        geometry=None, design=object(),
    )

    async def artifact(*_args):
        pytest.fail("artifact published")

    with pytest.raises(bridge.OfficialBeatUnavailable):
        asyncio.run(bridge.OfficialBeatEngine().run(
            request, cancel_cb=lambda: None, stage_cb=lambda *_: None,
            artifact_cb=artifact, imported_record={"source": "other"} if mode == "imported" else None,
        ))


def test_worker_negotiates_before_submit_and_cleans_job_files(monkeypatch) -> None:
    msh = MESH.read_text()
    calls = []
    paths = []
    requests = []

    class Stream:
        def __init__(self, events):
            self.events = iter(events)
            self.closed = False

        def __iter__(self):
            return self

        def __next__(self):
            return next(self.events)

        def close(self):
            self.closed = True
            calls.append("close")

    class Worker:
        def __init__(self, **kwargs):
            self.worker_info = {"ready": True}
            self.stream = None
            calls.append("construct")

        def ensure_started(self):
            calls.append("ready")

        def submit(self, request_path):
            calls.append("submit")
            paths.append(request_path)
            request = __import__("json").loads(request_path.read_text())
            requests.append(request)
            _, planes, angles, _ = bridge.build_compiled_request(
                Path(request["compiled_system"]["meshes"][0]["file"]),
                Path(request["cancel_path"]), _context(), msh,
                backend="cpu", precision="float32")
            frequencies = request["frequencies_hz"]
            events = [{"type": "result", "result": _result(f, planes, angles)} for f in frequencies]
            events.append({"type": "completed", "solved_count": len(frequencies)})
            self.stream = Stream(events)
            return self.stream

        def terminate(self):
            calls.append("terminate")

    def negotiate(ready, request, operation):
        assert ready == {"ready": True} and operation == "solve"
        calls.append("negotiate")

    original_import = bridge.importlib.import_module

    def fake_import(name):
        if name == "beat_engine":
            return SimpleNamespace(engine_paths=lambda backend: SimpleNamespace(
                system_solver=Path("/fake/solver.jl"), project=Path("/fake/project")))
        if name == "beat_engine.beat_contract.worker":
            return SimpleNamespace(validate_solve_request=lambda request: calls.append("validate"),
                                   negotiate_submission=negotiate)
        return original_import(name)

    monkeypatch.setattr(bridge.importlib, "import_module", fake_import)
    provisional = []
    final = bridge.solve_official_beat_from_msh_text(
        msh, _context(), worker_factory=Worker,
        result_callback=lambda index, response: provisional.append((index, response)),
    )
    assert calls.index("negotiate") < calls.index("submit")
    assert len(provisional) == 3
    assert final["metadata"]["solver_backend"] == "beat"
    frame = final["metadata"]["observation_frame_basis"]
    first_point = requests[0]["outputs"][0]["options"]["points_m"][0]
    np.testing.assert_allclose(
        first_point,
        np.asarray(frame["origin_m"]) + 2.0 * np.asarray(frame["axis"]),
        atol=1e-12,
    )
    assert final["frequencies"] == [500.0, 1000.0, 2000.0]
    assert [response["frequencies"] for _, response in provisional] == [
        [500.0], [2000.0], [1000.0]
    ]
    assert final["spl_on_axis"]["phase_degrees"][0] == pytest.approx(
        provisional[0][1]["spl_on_axis"]["phase_degrees"][0]
    )
    assert all(not path.exists() for path in paths)
    assert calls[-2:] == ["close", "terminate"]


def test_worker_failed_event_is_error_and_cleans_files(monkeypatch) -> None:
    msh = MESH.read_text()
    observed_paths = []
    calls = []

    class Stream:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            yield {"type": "failed", "error": "assembly failed"}

        def close(self):
            self.closed = True
            calls.append("close")

    class Worker:
        worker_info = {"ready": True}

        def __init__(self, **kwargs):
            self.stream = Stream()

        def ensure_started(self):
            pass

        def submit(self, path):
            observed_paths.append(path)
            return self.stream

        def terminate(self):
            calls.append("terminate")

    _fake_package(monkeypatch)
    with pytest.raises(bridge.OfficialBeatProtocolError, match="assembly failed"):
        bridge.solve_official_beat_from_msh_text(msh, _context(), worker_factory=Worker)
    assert calls == ["close", "terminate"]
    assert observed_paths and not observed_paths[0].exists()


def test_stream_close_failure_still_terminates_worker(monkeypatch) -> None:
    _fake_package(monkeypatch)
    calls = []
    paths = []

    class Stream:
        def __iter__(self):
            return self

        def __next__(self):
            raise StopIteration

        def close(self):
            calls.append("close")
            raise OSError("close failed")

    class Worker:
        worker_info = {"ready": True}

        def __init__(self, **kwargs):
            pass

        def ensure_started(self):
            pass

        def submit(self, path):
            paths.append(path)
            return Stream()

        def terminate(self):
            calls.append("terminate")

    with pytest.raises(OSError, match="close failed"):
        bridge.solve_official_beat_from_msh_text(
            MESH.read_text(), _context(), worker_factory=Worker,
        )
    assert calls == ["close", "terminate"]
    assert paths and not paths[0].exists()


def _fake_package(monkeypatch, negotiate=None):
    original_import = bridge.importlib.import_module

    def fake_import(name):
        if name == "beat_engine":
            return SimpleNamespace(engine_paths=lambda backend: SimpleNamespace(
                system_solver=Path("/fake/solver.jl"), project=Path("/fake/project")))
        if name == "beat_engine.beat_contract.worker":
            return SimpleNamespace(validate_solve_request=lambda request: None,
                                   negotiate_submission=negotiate or (lambda *_: None))
        return original_import(name)

    monkeypatch.setattr(bridge.importlib, "import_module", fake_import)


def test_incompatible_worker_never_receives_submit(monkeypatch) -> None:
    submitted = []

    class Worker:
        worker_info = {"protocol_version": 0}

        def __init__(self, **kwargs):
            pass

        def ensure_started(self):
            pass

        def submit(self, path):
            submitted.append(path)

        def terminate(self):
            pass

    def refuse(*_):
        raise RuntimeError("compiled_system version 1 unavailable")

    _fake_package(monkeypatch, refuse)
    with pytest.raises(RuntimeError, match="compiled_system version 1"):
        bridge.solve_official_beat_from_msh_text(MESH.read_text(), _context(), worker_factory=Worker)
    assert submitted == []


def test_one_frequency_completion_rejects_boolean_count(monkeypatch) -> None:
    _fake_package(monkeypatch)
    context = _context(frequency_range=(500.0, 500.0), num_frequencies=1,
                       frequencies_hz=(500.0,))
    paths = []

    class Stream:
        def __iter__(self):
            return self

        def __next__(self):
            if not hasattr(self, "sent"):
                self.sent = True
                return {"type": "completed", "solved_count": True}
            raise StopIteration

        def close(self):
            pass

    class Worker:
        worker_info = {"ready": True}

        def __init__(self, **kwargs):
            pass

        def ensure_started(self):
            pass

        def submit(self, path):
            paths.append(path)
            return Stream()

        def terminate(self):
            pass

    with pytest.raises(bridge.OfficialBeatProtocolError, match="completion count"):
        bridge.solve_official_beat_from_msh_text(
            MESH.read_text(), context, worker_factory=Worker,
        )
    assert paths and not paths[0].exists()


def test_cancel_interrupts_blocked_startup_before_submit(monkeypatch) -> None:
    cancel = threading.Event()
    terminated = threading.Event()
    submitted = []

    class Worker:
        worker_info = {"ready": True}

        def __init__(self, **kwargs):
            pass

        def ensure_started(self):
            cancel.set()
            assert terminated.wait(timeout=2)

        def submit(self, path):
            submitted.append(path)

        def terminate(self):
            terminated.set()

    def cancel_cb():
        if cancel.is_set():
            raise RuntimeError("job cancelled")

    _fake_package(monkeypatch)
    with pytest.raises(RuntimeError, match="job cancelled"):
        bridge.solve_official_beat_from_msh_text(
            MESH.read_text(), _context(), worker_factory=Worker,
            cancellation_callback=cancel_cb,
        )
    assert submitted == [] and terminated.is_set()


def test_cancel_interrupts_blocked_result_read_and_discards_worker(monkeypatch) -> None:
    cancel = threading.Event()
    terminated = threading.Event()
    paths = []

    class Stream:
        def __iter__(self):
            return self

        def __next__(self):
            cancel.set()
            assert terminated.wait(timeout=2)
            raise StopIteration

        def close(self):
            pass

    class Worker:
        worker_info = {"ready": True}

        def __init__(self, **kwargs):
            pass

        def ensure_started(self):
            pass

        def submit(self, path):
            paths.append(path)
            return Stream()

        def terminate(self):
            terminated.set()

    def cancel_cb():
        if cancel.is_set():
            raise RuntimeError("job cancelled")

    _fake_package(monkeypatch)
    with pytest.raises(RuntimeError, match="job cancelled"):
        bridge.solve_official_beat_from_msh_text(
            MESH.read_text(), _context(), worker_factory=Worker,
            cancellation_callback=cancel_cb,
        )
    assert terminated.is_set() and paths and not paths[0].exists()


def test_julia_resolution_prefers_explicit_and_refuses_wrong_path(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "julia"
    executable.write_text("stub")
    executable.chmod(0o755)
    assert bridge.resolve_julia_executable(str(executable)) == str(executable)
    assert bridge.resolve_julia_executable(str(tmp_path / "missing")) is None


def test_installed_official_contract_accepts_compiled_request_if_present(tmp_path) -> None:
    contract = pytest.importorskip("beat_engine.beat_contract")
    request, _, _, _ = bridge.build_compiled_request(
        tmp_path / "surface.msh", tmp_path / "cancel.marker", _context(), MESH.read_text(),
        backend="cpu", precision="float32",
    )
    contract.validate_solve_request(request)
