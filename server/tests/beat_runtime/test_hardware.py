from __future__ import annotations

import builtins
import importlib
import subprocess

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
def test_hardware_gate_on_host_without_gpu(system, machine, version, eligible):
    rows = hardware.gpu_hardware(system=system, machine=machine, macos_version=version, environ={"PATH": ""})
    assert rows["metal"]["available"] is eligible
    for backend in ("cuda", "rocm"):
        assert rows[backend]["available"] is False
        assert "detected" in rows[backend]["reason"] or "requires" in rows[backend]["reason"]


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


@pytest.mark.parametrize("version,eligible", [("13.3", True), ("26.0", True), ("12.7", False), ("bad", False)])
def test_old_sdk_macos_compatibility_version_uses_sw_vers(monkeypatch, version, eligible):
    monkeypatch.setattr(hardware.platform, "mac_ver", lambda: ("10.16", (), ""))
    calls = []

    def sw_vers(command, **kwargs):
        calls.append((command, kwargs))
        return version + "\n"

    monkeypatch.setattr(hardware.subprocess, "check_output", sw_vers)
    rows = hardware.gpu_hardware(system="Darwin", machine="arm64")
    assert rows["metal"]["available"] is eligible
    assert calls == [(["/usr/bin/sw_vers", "-productVersion"], dict(
        text=True, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
    ))]


@pytest.mark.parametrize("error", [OSError("missing sw_vers"), subprocess.TimeoutExpired("sw_vers", 2)])
def test_old_sdk_macos_version_fallback_failure_refuses_metal(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(hardware.subprocess, "check_output", fail)
    assert hardware.gpu_hardware(system="Darwin", machine="arm64", macos_version="10.16")["metal"]["available"] is False


def test_rosetta_python_refuses_metal_without_version_probe(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Rosetta should be refused before probing macOS")

    monkeypatch.setattr(hardware.subprocess, "check_output", forbidden)
    assert hardware.gpu_hardware(system="Darwin", machine="x86_64", macos_version="10.16")["metal"]["available"] is False


@pytest.mark.parametrize("system", ["Linux", "Windows"])
@pytest.mark.parametrize("outcome,available", [(0, True), (1, False), (OSError("missing"), False),
                                              (subprocess.TimeoutExpired("nvidia-smi", 15), False)])
def test_nvidia_inventory_is_bounded_and_fail_closed(monkeypatch, system, outcome, available):
    from types import SimpleNamespace

    calls = []
    env = {"PATH": "driver-bin"}
    monkeypatch.setattr(hardware.shutil, "which", lambda name, **kwargs:
                        "nvidia-smi" if name == "nvidia-smi" else None)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(returncode=outcome)

    monkeypatch.setattr(hardware.subprocess, "run", run)
    assert hardware.gpu_hardware(system=system, environ=env)["cuda"]["available"] is available
    assert calls == [(["nvidia-smi", "-L"], dict(capture_output=True, timeout=15.0,
                      check=False, stdin=subprocess.DEVNULL, env=env))]


@pytest.mark.parametrize("system", ["Linux", "Windows"])
@pytest.mark.parametrize("name", ["BLAB_ROCM_PATH", "ROCM_PATH", "HIP_PATH", "ROCM_HOME"])
def test_rocm_directory_inventory(monkeypatch, tmp_path, system, name):
    monkeypatch.setattr(hardware.shutil, "which", lambda *args, **kwargs: None)
    env = {"PATH": "", name: str(tmp_path)}
    assert hardware.gpu_hardware(system=system, environ=env)["rocm"]["available"]
    env[name] = str(tmp_path / "missing")
    assert not hardware.gpu_hardware(system=system, environ=env)["rocm"]["available"]


@pytest.mark.parametrize("name", ["rocminfo", "hipinfo", "hipInfo"])
def test_rocm_path_inventory_starts_no_process(monkeypatch, name):
    monkeypatch.setattr(hardware.shutil, "which", lambda tool, **kwargs: tool if tool == name else None)
    monkeypatch.setattr(hardware.subprocess, "run", lambda *a, **k: pytest.fail("ROCm inventory must not launch"))
    assert hardware.gpu_hardware(system="Windows", environ={"PATH": "tools"})["rocm"]["available"]


def test_macos_never_offers_cuda_or_rocm(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware.shutil, "which", lambda *a, **k: pytest.fail("macOS must not probe NVIDIA/ROCm"))
    rows = hardware.gpu_hardware(system="Darwin", machine="arm64", macos_version="14",
                                 environ={"ROCM_PATH": str(tmp_path)})
    assert rows["metal"]["available"]
    assert not rows["cuda"]["available"] and not rows["rocm"]["available"]


@pytest.mark.parametrize("available,selected", [(("cuda", "rocm", "metal"), "cuda"),
                                                (("rocm", "metal"), "rocm"), (("metal",), "metal"), ((), None)])
def test_detection_order_matches_hbb(monkeypatch, available, selected):
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs:
                        {name: {"available": name in available} for name in hardware.GPU_BACKENDS})
    assert hardware.detect_gpu_backend() == selected


def test_launcher_hint_does_not_wait_for_nvidia_smi(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda name, **kwargs: name if name == "nvidia-smi" else None)
    monkeypatch.setattr(hardware.subprocess, "run", lambda *a, **k: pytest.fail("launcher must not wait"))
    assert hardware.gpu_hardware(system="Windows", environ={"PATH": ""}, probe_nvidia=False)["cuda"]["available"]
