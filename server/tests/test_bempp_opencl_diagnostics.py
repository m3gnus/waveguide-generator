"""Compiler diagnostics survive disposable-child failures without passing them."""

from __future__ import annotations

import json
import sys
import types

import pytest

from server.solver import bempp_opencl as probe
from server.tests.test_bempp_opencl import CPU, probe_session as probe_session
from server.tests.test_bempp_opencl_lifecycle import child_for
from server.tests.test_real_pipeline import _opencl_diagnosis


BUILD_LOG = ("clBuildProgram failed: BUILD_PROGRAM_FAILURE\n"
             "Build on CPU device:\nunit-kernel.cl:17: error: unknown type name 'unit_type'\n"
             "(options: -DPRECISION=64 -I unit-include)\n(source saved as unit-kernel.cl)")


@pytest.fixture(autouse=True)
def clear_probe(probe_session):
    probe.clear_cache()
    yield
    probe.clear_cache()


def test_real_child_result_retains_multiline_compile_log_and_traceback(monkeypatch):
    for name in ("bempp_cl", "bempp_cl.api", "bempp_cl.core", "bempp_cl.core.opencl_kernels",
                 "hornlab_bempp_bem", "hornlab_bempp_bem.device", "pyopencl"):
        module = types.ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
        parent, _, attribute = name.rpartition(".")
        if parent:
            monkeypatch.setattr(sys.modules[parent], attribute, module, raising=False)

    def fail(_device):
        raise RuntimeError(BUILD_LOG)

    monkeypatch.setattr(probe, "smoke_test", fail)
    result = probe._child_result("smoke", CPU)
    assert not result["ok"]
    assert result["opencl_unavailable_reason"] == "probe_error"
    assert result["reason"] == "RuntimeError: clBuildProgram failed: BUILD_PROGRAM_FAILURE"
    assert BUILD_LOG in result["probe_diagnostic"]
    assert "Traceback (most recent call last)" in result["probe_diagnostic"]
    assert result["stage"] == "opencl"
    probe._validate_probe_result(result, "smoke")


def test_failure_report_crosses_real_child_channel_and_logs_compiler_evidence(monkeypatch, caplog):
    result = {"ok": False, "stage": "opencl", "opencl_unavailable_reason": "probe_error",
              "reason": "compile failed", "probe_diagnostic": BUILD_LOG}
    child_for(monkeypatch, f"""
import sys
from pathlib import Path
from server.solver import bempp_opencl as probe
print(probe._READY_MARKER, flush=True)
probe._child_result = lambda *args: {result!r}
probe._child_main('smoke', None, Path(sys.argv[1]))
""")
    verdict = probe._run_probe("smoke", CPU, 5)
    assert verdict["ok"] is False
    assert verdict["probe_diagnostic"] == BUILD_LOG
    assert "unknown type name 'unit_type'" in caplog.text


def test_teardown_abort_after_failure_keeps_stderr_but_never_trusts_report(monkeypatch):
    result = {"ok": False, "opencl_unavailable_reason": "probe_error", "reason": "compile failed",
              "probe_diagnostic": BUILD_LOG}
    children = child_for(monkeypatch, f"""
import atexit, os, sys
from pathlib import Path
from server.solver import bempp_opencl as probe
atexit.register(os.abort)
print(probe._READY_MARKER, flush=True)
probe._child_result = lambda *args: {result!r}
probe._child_main('smoke', None, Path(sys.argv[1]))
""")
    verdict = probe._run_probe("smoke", CPU, 5)
    assert children[0].returncode != 0
    assert verdict["ok"] is False
    assert "Child exited with status" in verdict["reason"]
    assert "unknown type name 'unit_type'" in verdict["probe_stderr"]


def test_retry_device_aggregation_and_pipeline_assertion_keep_both_build_logs(monkeypatch):
    calls = 0

    def run(mode, _device, _timeout):
        nonlocal calls
        if mode == "inventory":
            return {"ok": True, "devices": [CPU]}
        calls += 1
        return {"ok": False, "stage": "opencl", "opencl_unavailable_reason": "probe_error",
                "reason": "compile failed", "probe_diagnostic": f"attempt {calls}\n{BUILD_LOG}", "_active_seconds": 0.1}

    monkeypatch.setattr(probe, "_run_probe", run)
    verdict = probe.qualified_opencl()
    assert calls == 2
    assert verdict["ok"] is False
    assert verdict["opencl_unavailable_reason"] == "probe_error"
    assert "attempt 1" in verdict["probe_diagnostic"] and "attempt 2" in verdict["probe_diagnostic"]
    assert "CPU (opencl)" in verdict["probe_diagnostic"]
    assert BUILD_LOG in _opencl_diagnosis()
    assert "unknown type name" not in verdict["reason"]


def test_oversized_diagnostic_is_bounded_and_explicit_and_preserves_head_and_tail():
    source = "COMPILER HEADER\n" + "x" * (2 * probe.PROBE_DIAGNOSTIC_CHARS) + "\nCOMPILER LAST ERROR"
    bounded = probe._bounded_diagnostic(source)
    assert len(bounded) == probe.PROBE_DIAGNOSTIC_CHARS
    assert bounded.startswith("COMPILER HEADER") and bounded.endswith("COMPILER LAST ERROR")
    assert "diagnostic truncated" in bounded
    small = "exact compiler message\nsecond line"
    assert probe._bounded_diagnostic(small) == small


@pytest.mark.parametrize("diagnostic", (None, {}, 3, "x" * (probe.PROBE_DIAGNOSTIC_CHARS + 1)))
def test_malformed_or_oversized_child_diagnostic_is_a_protocol_failure(diagnostic):
    result = {"ok": False, "opencl_unavailable_reason": "probe_error", "reason": "error",
              "probe_diagnostic": diagnostic}
    with pytest.raises(ValueError, match="Invalid probe diagnostic"):
        probe._validate_probe_result(result, "smoke")


def test_schema_rejection_cannot_be_hidden_by_good_compiler_text(monkeypatch):
    result = {"ok": True, "probe_diagnostic": BUILD_LOG}
    child_for(monkeypatch, f"import sys; open(sys.argv[1], 'w').write({json.dumps(result)!r})")
    verdict = probe._run_probe("smoke", CPU, 5)
    assert not verdict["ok"]
    assert verdict["opencl_unavailable_reason"] == "probe_error"
