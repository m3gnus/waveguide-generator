from __future__ import annotations

import builtins
import importlib

import pytest

from server.solver.beat_runtime import gpu, hardware


@pytest.mark.parametrize("system,machine,version,eligible", [
    ("Darwin", "arm64", "13.3", True), ("Darwin", "arm64", "13.3.1", True),
    ("Darwin", "aarch64", "14", True), ("Darwin", "arm64", "26.0", True),
    ("Darwin", "arm64", "13.2.9", False), ("Darwin", "arm64", "12.7", False),
    ("Darwin", "arm64", "", False), ("Darwin", "arm64", "unknown", False),
    ("Darwin", "x86_64", "26.0", False), ("Linux", "aarch64", "26.0", False),
    ("Windows", "ARM64", "26.0", False),
])
def test_hardware_gate_and_unsupported_backends(system, machine, version, eligible):
    rows = hardware.gpu_hardware(system=system, machine=machine, macos_version=version)
    assert rows["metal"]["available"] is eligible
    for backend in ("cuda", "rocm"):
        assert rows[backend] == {"available": False, "reason": "not supported in this build"}


@pytest.mark.parametrize("machine,version,selected", [
    ("arm64", "13.3", "metal"), ("arm64", "13.2", None), ("x86_64", "26", None),
])
def test_backend_selection_uses_platform(monkeypatch, machine, version, selected):
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware.platform, "machine", lambda: machine)
    monkeypatch.setattr(hardware.platform, "mac_ver", lambda: (version, (), ""))
    assert hardware.detect_gpu_backend() == gpu.detect_gpu_backend() == selected


def test_optional_engine_and_hbb_are_not_imported(monkeypatch):
    original = builtins.__import__

    def deny(name, *args, **kwargs):
        if name.startswith(("beat_engine", "hornlab_beat_bem")):
            raise AssertionError("optional package imported during hardware discovery")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny)
    importlib.reload(hardware)
    importlib.reload(gpu)
    hardware.gpu_hardware(system="Linux", machine="x86_64")
