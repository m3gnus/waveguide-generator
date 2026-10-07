from __future__ import annotations

import base64
import builtins
import copy
import hashlib
import importlib
import json
from pathlib import Path
import struct

import pytest

from server.solver.beat_runtime import probe


def result(real=1.0, imag=-2.0, dtype="complex64", *, backend="cpu"):
    layout = "<ff" if dtype == "complex64" else "<dd"
    return {"type": "result", "result": {
        "schema_version": 2, "freq_hz": 1000.0, "excitation_port_ids": ["excitation:probe"],
        "diagnostics": {"bem_backend": backend, "precision": "float32",
                        "phasor_convention": "exp(-i omega t)"},
        "quantities": [{"id": "pressure", "quantity": "exterior_pressure", "unit": "Pa",
                        "axes": ["excitation", "observation"], "values": {
                            "encoding": "base64", "dtype": dtype, "shape": [1, 1],
                            "order": "C", "byte_order": "little",
                            "content_base64": base64.b64encode(struct.pack(layout, real, imag)).decode(),
                        }}],
    }}


COMPLETED = {"type": "completed", "solved_count": 1}
WORKER_INFO = {
    "type": "ready", "protocol": {"name": "beat-worker", "version": 1},
    "engine": {"name": "BEAT Engine", "version": "0.3.0"},
    "contracts": {"system_request": [1], "compiled_system": [1], "system_result": [2]},
    "operations": ["solve"], "precisions": ["float32"], "solve_kinds": ["exterior_bem"],
    "request_transports": ["file"], "phasor_conventions": ["exp(-i omega t)"],
    "backends": {"cpu": {"available": True}, "metal": {"available": True}},
}


class FakeStream:
    def __init__(self, events, *, error=None, close_error=None):
        self.events = events
        self.error = error
        self.close_error = close_error
        self.closed = False

    def __iter__(self):
        yield from self.events
        if self.error:
            raise self.error

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class FakeWorker:
    def __init__(self, events, *, worker_info=WORKER_INFO, **kwargs):
        self.stream = FakeStream(events, **kwargs)
        self.worker_info = copy.deepcopy(worker_info)

    def submit(self, path):
        self.path = path
        self.request = json.loads(path.read_text())
        self.mesh = Path(self.request["compiled_system"]["meshes"][0]["file"]).read_bytes()
        return self.stream


@pytest.mark.parametrize("backend,dtype,real,imag", [
    ("cpu", "complex64", 1.0, -2.0), ("metal", "complex64", 0.0, 2.0),
    ("cpu", "complex64", 2.0, 0.0),
])
def test_compiled_probe_stages_tiny_request_and_completion(tmp_path, backend, dtype, real, imag):
    worker = FakeWorker([{"type": "status", "message": "solving"},
                         result(real, imag, dtype, backend=backend), COMPLETED])
    verdict = probe.compiled_probe(worker, directory=tmp_path, backend=backend)
    assert verdict.ready, verdict.reason
    assert verdict.completion == {"result_count": 1, "solved_count": 1, "finite_nonzero": True,
                                  "bem_backend": backend}
    assert verdict.fixture_identity == hashlib.sha256(worker.mesh).hexdigest()
    assert worker.stream.closed and not worker.path.exists() and list(tmp_path.iterdir()) == []
    request = worker.request
    assert request["schema_version"] == request["compiled_system"]["contract_version"] == 1
    assert request["frequencies_hz"] == [1000.0]
    assert request["solver_options"]["bem_backend"] == backend
    assert request["solver_options"]["regular_quadrature_mode"] == "fixed"
    assert request["solver_options"]["quadrature_order"] == request["solver_options"]["singular_order"] == 4
    assert Path(request["compiled_system"]["meshes"][0]["file"]).is_absolute()
    assert b"$Nodes\n4\n" in worker.mesh and b"$Elements\n4\n" in worker.mesh
    assert all(line.split()[3] == "2" for line in worker.mesh.decode().split("$Elements\n4\n")[1].splitlines()[:4])


@pytest.mark.parametrize("real,imag,reason", [
    (0.0, 0.0, "zero"), (float("nan"), 1.0, "non-finite"),
    (1.0, float("inf"), "non-finite"), (-float("inf"), 0.0, "non-finite"),
])
def test_numerical_failure(tmp_path, real, imag, reason):
    worker = FakeWorker([result(real, imag), COMPLETED])
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and reason in verdict.reason
    assert verdict.completion == {} and worker.stream.closed


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 1), ("freq_hz", 999),
    ("excitation_port_ids", ["wrong"]), ("quantities", []), ("quantities", [None]),
])
def test_malformed_result(tmp_path, field, value):
    event = result()
    event["result"][field] = value
    worker = FakeWorker([event, COMPLETED])
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and "Malformed" in verdict.reason and worker.stream.closed


@pytest.mark.parametrize("field,value", [("id", "wrong"), ("quantity", "bem_boundary_pressure"),
                                       ("unit", "dB"), ("axes", ["observation", "excitation"])])
def test_malformed_quantity(tmp_path, field, value):
    event = result()
    event["result"]["quantities"][0][field] = value
    worker = FakeWorker([event, COMPLETED])
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and "identity or axes" in verdict.reason and worker.stream.closed


@pytest.mark.parametrize("field,value", [
    ("dtype", "float32"), ("shape", [1, 2]), ("shape", [True, 1]), ("encoding", "json"),
    ("byte_order", "big"), ("order", "F"), ("content_base64", "AAAA"),
    ("content_base64", "!"), ("content_base64", None),
])
def test_malformed_binary(tmp_path, field, value):
    event = result()
    event["result"]["quantities"][0]["values"][field] = value
    worker = FakeWorker([event, COMPLETED])
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and verdict.reason and worker.stream.closed


@pytest.mark.parametrize("events,reason", [
    ([], "one result"), ([result()], "completion"), ([COMPLETED], "one result"),
    ([result(), result(), COMPLETED], "one result"),
    ([result(), COMPLETED, COMPLETED], "after terminal"),
    ([None], "Malformed"), ([{"type": "ready"}], "Unexpected"),
    ([result(), {"type": "failed", "error": "synthetic error"}], "synthetic error"),
    ([result(), {"type": "cancelled"}], "cancelled"),
])
def test_event_lifecycle_fails_with_reason(tmp_path, events, reason):
    worker = FakeWorker(copy.deepcopy(events))
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and reason in verdict.reason and worker.stream.closed


@pytest.mark.parametrize("count", [None, 0, 2, True, 1.0, "1"])
def test_wrong_terminal_count(tmp_path, count):
    worker = FakeWorker([result(), {"type": "completed", "solved_count": count}])
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and "solved_count" in verdict.reason


@pytest.mark.parametrize("kwargs", [{"error": RuntimeError("worker broke")},
                                   {"close_error": RuntimeError("closure broke")}])
def test_worker_and_closure_errors_fail(tmp_path, kwargs):
    worker = FakeWorker([result(), COMPLETED], **kwargs)
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and "broke" in verdict.reason and worker.stream.closed
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field,value", [
    ("bem_backend", "cpu"), ("precision", "float64"),
    ("phasor_convention", "exp(+i omega t)"), ("precision", None),
])
def test_result_diagnostics_must_match_request(tmp_path, field, value):
    event = result(backend="metal")
    event["result"]["diagnostics"][field] = value
    worker = FakeWorker([event, COMPLETED])
    verdict = probe.compiled_probe(worker, directory=tmp_path, backend="metal")
    assert not verdict.ready and field in verdict.reason and worker.stream.closed


def test_fabricated_result_without_diagnostics_is_not_ready(tmp_path):
    event = result()
    del event["result"]["diagnostics"]
    worker = FakeWorker([event, COMPLETED])
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and "diagnostics" in verdict.reason and worker.stream.closed


def test_float32_request_rejects_complex128_pressure(tmp_path):
    worker = FakeWorker([result(dtype="complex128"), COMPLETED])
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and "pressure array" in verdict.reason and worker.stream.closed


@pytest.mark.parametrize("info", [None, {}, {"type": "status"}])
def test_missing_worker_info_is_not_ready(tmp_path, info):
    worker = FakeWorker([result(), COMPLETED], worker_info=info)
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and "worker_info" in verdict.reason and worker.stream.closed


@pytest.mark.parametrize("field,value", [("name", "wg-beat-host"), ("version", 2), ("version", True)])
def test_worker_protocol_must_match_official_engine(tmp_path, field, value):
    info = copy.deepcopy(WORKER_INFO)
    info["protocol"][field] = value
    worker = FakeWorker([result(), COMPLETED], worker_info=info)
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and "protocol" in verdict.reason and worker.stream.closed


@pytest.mark.parametrize("field,value", [
    ("engine", None), ("engine", {"name": "HBB", "version": "0.3.0"}),
    ("engine", {"name": "BEAT Engine", "version": ""}),
    ("contracts", {}), ("contracts", {"system_request": [True]}), ("contracts", None),
    ("operations", []), ("precisions", ["float64"]), ("solve_kinds", ["interior_fem"]),
    ("request_transports", ["inline_json"]), ("phasor_conventions", ["exp(+i omega t)"]),
    ("backends", {"cpu": {"available": False}}), ("backends", {"cpu": {"available": 1}}),
    ("backends", {"cpu": {"available": True, "phasor_conventions": ["exp(+i omega t)"]}}),
])
def test_worker_capabilities_must_support_probe_request(tmp_path, field, value):
    info = copy.deepcopy(WORKER_INFO)
    info[field] = value
    worker = FakeWorker([result(), COMPLETED], worker_info=info)
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert not verdict.ready and worker.stream.closed


@pytest.mark.parametrize("root_env", ["HORNLAB_BEAT_RUNTIME_DIR", "HORNLAB_BEAT_WORKER_DIR"])
@pytest.mark.parametrize("via_link", [False, True])
def test_probe_staging_refuses_hbb_root_and_symlink(tmp_path, monkeypatch, root_env, via_link):
    protected = tmp_path / "protected"
    protected.mkdir()
    monkeypatch.setenv(root_env, str(protected))
    directory = protected / "must-not-be-created"
    if via_link:
        directory = tmp_path / "alias"
        directory.symlink_to(protected, target_is_directory=True)
    worker = FakeWorker([result(), COMPLETED])
    verdict = probe.compiled_probe(worker, directory=directory)
    assert not verdict.ready and "overlaps" in verdict.reason
    assert list(protected.iterdir()) == [] and not hasattr(worker, "path")


def test_ready_verdict_requires_probe_proof():
    with pytest.raises(ValueError, match="compiled_probe"):
        probe.ProbeResult(ready=True, reason="fabricated")
    assert not probe.ProbeResult(ready=False, reason="unavailable").ready


def test_windows_handle_cleanup_errors_do_not_invalidate_probe(tmp_path, monkeypatch):
    original = probe.tempfile.TemporaryDirectory._rmtree

    def held_handle(name, ignore_errors=False, repeated=False):
        if not ignore_errors:
            raise PermissionError("Julia still holds the mesh")
        original(name, ignore_errors=ignore_errors, repeated=repeated)

    monkeypatch.setattr(probe.tempfile.TemporaryDirectory, "_rmtree", staticmethod(held_handle))
    worker = FakeWorker([result(), COMPLETED])
    verdict = probe.compiled_probe(worker, directory=tmp_path)
    assert verdict.ready, verdict.reason
    assert worker.stream.closed and list(tmp_path.iterdir()) == []


def test_submit_failure_and_optional_import(tmp_path, monkeypatch):
    original = builtins.__import__

    def deny(name, *args, **kwargs):
        if name.startswith(("beat_engine", "hornlab_beat_bem")):
            raise AssertionError("Probe must only use its injected worker")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny)
    importlib.reload(probe)

    class BrokenWorker:
        def submit(self, request_path):
            raise RuntimeError("startup broke")

    verdict = probe.compiled_probe(BrokenWorker(), directory=tmp_path)
    assert not verdict.ready and "startup broke" in verdict.reason
    assert list(tmp_path.iterdir()) == []
