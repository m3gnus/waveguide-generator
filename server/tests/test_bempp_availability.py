"""bempp availability must mean "a solve can run", not "the wrapper imported".

v1 shipped this bug and then wrote the diagnosis down in
``server/scripts/check_solver_engine.py``: ``hornlab_bempp_bem`` is a thin
pure-Python wrapper that imports fine on a clean Windows box where bempp-cl
cannot assemble at all, because numba's compiled extensions need a Visual C++
redistributable Windows does not install by default. The installer reported
"Bempp ready", the preflight reported READY, and every solve died on
``ImportError: Numba could not be imported``.

v2 is about to be validated on Windows for the first time, so these tests pin
the behaviour that keeps it from repeating.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

from server.solver import bempp


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """Successful probes are cached process-wide, so isolate every case.

    ``bempp_status`` memoizes a successful probe -- it imports bempp_cl and
    enumerates OpenCL devices, which was being paid again at the start of every
    solve. On a host where the real probe succeeds, that cached success would
    otherwise answer the monkeypatched failure cases below.
    """

    bempp.bempp_status.cache_clear()
    yield
    bempp.bempp_status.cache_clear()


def _refusing(target, message):
    """Import everything normally except ``target``, which fails as on Windows."""

    real_import = importlib.import_module

    def refuse(name, *args, **kwargs):
        if name == target:
            raise ImportError(message)
        return real_import(name, *args, **kwargs)

    return refuse


def _refusing_numba():
    return _refusing("numba", "Numba could not be imported")


def test_opencl_is_preferred_when_a_device_exists(monkeypatch):
    """OpenCL is hornlab-bempp-bem's production backend, so it wins by default."""

    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    monkeypatch.setattr(bempp, "_opencl_status", lambda: (True, "assembles on OpenCL device Fake CPU"))

    status = bempp.bempp_status()

    assert status["available"] is True
    assert status["assembly_backend"] == "opencl"
    assert status["warning"] is None


def test_numba_fallback_is_never_silent(monkeypatch):
    """Falling back must say so, say why, and say what to fix.

    Assembling on numba quietly is how someone spends a week wondering why
    solves are slow and why Stop does nothing for the first minute.
    """

    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    monkeypatch.setattr(
        bempp, "_opencl_status",
        lambda: (False, "an OpenCL runtime is present but exposes no device. Install an OpenCL runtime"),
    )

    status = bempp.bempp_status()

    assert status["available"] is True
    assert status["assembly_backend"] == "numba"
    warning = status["warning"]
    assert warning and warning == status["reason"]
    assert "numba" in warning
    assert "OpenCL" in warning
    assert "Install an OpenCL runtime" in warning


def test_available_requires_a_working_assembly_backend(monkeypatch):
    """A wrapper that imports is not enough to call the engine available."""

    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    monkeypatch.setattr(bempp, "_opencl_status", lambda: (False, "no OpenCL here."))
    monkeypatch.setattr(bempp.importlib, "import_module", _refusing_numba())

    status = bempp.bempp_status()

    assert status["available"] is False
    assert status["assembly_backend"] is None
    assert "numba" in status["reason"]
    assert "OpenCL" in status["reason"]


def test_windows_missing_runtime_names_the_dlls_and_the_fix(monkeypatch):
    """The remedy has to be in the message; the failure is otherwise a mystery."""

    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    monkeypatch.setattr(bempp, "_opencl_status", lambda: (False, "no OpenCL here."))
    monkeypatch.setattr(bempp.importlib, "import_module", _refusing_numba())
    monkeypatch.setattr(
        bempp, "_missing_windows_runtime_dlls", lambda: ["vcruntime140.dll", "msvcp140.dll"]
    )

    reason = bempp.bempp_status()["reason"]

    assert "vcruntime140.dll" in reason and "msvcp140.dll" in reason
    assert "VCRedist" in reason or "vc_redist" in reason


def test_missing_opencl_runtime_explains_how_to_get_one(monkeypatch):
    """pyopencl imports fine with no ICD; the device probe is what catches it."""

    class _NoPlatforms:
        @staticmethod
        def get_platforms():
            raise RuntimeError("clGetPlatformIDs failed: PLATFORM_NOT_FOUND_KHR")

    monkeypatch.setitem(sys.modules, "pyopencl", _NoPlatforms)

    usable, reason = bempp._opencl_status()

    assert usable is False
    assert "PLATFORM_NOT_FOUND_KHR" in reason
    assert "Khronos" in reason or "OpenCL runtime" in reason


class _FakeDevice:
    def __init__(self, name, kind):
        self.name = name
        self.kind = kind
        self.type = kind


class _FakePlatform:
    """One ICD, answering ``get_devices`` the way pyopencl does."""

    def __init__(self, name, devices):
        self.name = name
        self._devices = devices

    def get_devices(self, device_type=None):
        if device_type is None:
            return list(self._devices)
        found = [d for d in self._devices if d.kind == device_type]
        if not found:
            # pyopencl surfaces the driver's own DEVICE_NOT_FOUND as an error.
            raise RuntimeError("clGetDeviceIDs failed: DEVICE_NOT_FOUND")
        return found


def _fake_pyopencl(platforms):
    class _DeviceType:
        CPU = "cpu-devices"
        GPU = "gpu-devices"

    class _Module:
        device_type = _DeviceType

        @staticmethod
        def get_platforms():
            return platforms

    return _Module


def test_a_gpu_only_runtime_is_not_a_usable_opencl_backend(monkeypatch):
    """Apple Silicon: the ICD is real, the device the solve needs is not.

    ``solve_bempp_from_msh_text`` always asks bempp-cl for an OpenCL *cpu*
    device, and bempp-cl's dense assembly has no GPU-only path. A probe that
    accepted any device reported READY on an M1 Max and then failed inside
    every solve with "OpenCL cpu device could not be initialized" -- exactly
    the class of lie this module exists to prevent.
    """

    gpu_only = _FakePlatform("Apple", [_FakeDevice("Apple M1 Max", "gpu-devices")])
    monkeypatch.setitem(sys.modules, "pyopencl", _fake_pyopencl([gpu_only]))

    usable, reason = bempp._opencl_status()

    assert usable is False
    assert "cpu" in reason
    # Name what was found, or the report reads as "no OpenCL at all", which is
    # a different problem with a different remedy.
    assert "Apple M1 Max" in reason


def test_a_cpu_device_on_any_platform_is_accepted(monkeypatch):
    """The GPU-only case must not become a blanket refusal."""

    gpu_only = _FakePlatform("Apple", [_FakeDevice("Apple M1 Max", "gpu-devices")])
    with_cpu = _FakePlatform(
        "Intel(R) OpenCL", [_FakeDevice("Intel(R) Core(TM) i7", "cpu-devices")]
    )
    monkeypatch.setitem(sys.modules, "pyopencl", _fake_pyopencl([gpu_only, with_cpu]))
    monkeypatch.setattr(
        bempp,
        "_bempp_default_cpu_device",
        lambda: with_cpu.get_devices(device_type="cpu-devices")[0],
        raising=False,
    )

    usable, reason = bempp._opencl_status()

    assert usable is True
    assert "Intel(R) Core(TM) i7" in reason


def test_the_probe_asks_for_the_device_the_solve_asks_for(monkeypatch):
    """One constant, so the two can never drift apart again."""

    assert bempp.OPENCL_DEVICE_TYPE == "cpu"

    recorded = []

    class _Recording(_FakePlatform):
        def get_devices(self, device_type=None):
            recorded.append(device_type)
            return super().get_devices(device_type)

    cpu = _FakeDevice("Intel(R) Core(TM) i7", "cpu-devices")
    platform_entry = _Recording("Intel(R) OpenCL", [cpu])
    monkeypatch.setitem(sys.modules, "pyopencl", _fake_pyopencl([platform_entry]))
    monkeypatch.setattr(
        bempp,
        "_bempp_default_cpu_device",
        lambda: cpu,
        raising=False,
    )

    assert bempp._opencl_status()[0] is True
    assert recorded == ["cpu-devices"]


def test_probe_rejects_the_non_cpu_device_bempp_cl_would_really_select(monkeypatch):
    cpu = _FakeDevice("Validated CPU", "cpu-devices")
    gpu = _FakeDevice("Unvalidated GPU", "gpu-devices")
    mixed = _FakePlatform("Mixed CPU/GPU ICD", [cpu, gpu])
    monkeypatch.setitem(sys.modules, "pyopencl", _fake_pyopencl([mixed]))
    monkeypatch.setattr(
        bempp,
        "_bempp_default_cpu_device",
        lambda: gpu,
        raising=False,
    )

    usable, reason = bempp._opencl_status()

    assert usable is False
    assert "Unvalidated GPU" in reason
    assert "not a cpu device" in reason


def test_available_requires_the_engine_not_just_its_wrapper(monkeypatch):
    """numba loading proves nothing about bempp-cl, which is imported lazily.

    hornlab_bempp_bem imports bempp_cl inside the solve functions, so importing
    the wrapper leaves sys.modules with no bempp_cl entry at all. A probe that
    stopped at numba would call a broken engine available.
    """

    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    monkeypatch.setattr(
        bempp.importlib,
        "import_module",
        _refusing("bempp_cl.api", "DLL load failed while importing _bempp: ..."),
    )

    status = bempp.bempp_status()

    assert status["available"] is False
    assert status["assembly_backend"] is None
    assert "bempp_cl" in status["reason"]


def test_the_wrapper_really_does_defer_its_bempp_import():
    """Pins the premise of the test above; if it ever changes, this fails loudly."""

    pytest.importorskip("hornlab_bempp_bem")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import hornlab_bempp_bem; "
            "print(any(m == 'bempp_cl' or m.startswith('bempp_cl.') for m in sys.modules))",
        ],
        text=True, capture_output=True, check=True,
    )
    assert completed.stdout.strip() == "False"


def test_missing_runtime_dlls_are_reported_by_name(monkeypatch):
    """The Windows branch of the DLL probe, exercised without a clean box.

    ctypes.CDLL succeeds here because the redistributable is installed, so the
    failure this reports for a machine without it is otherwise never executed.
    """

    monkeypatch.setattr(bempp.platform, "system", lambda: "Windows")

    def refuse_dll(name):
        if name in ("vcruntime140_1.dll", "msvcp140.dll"):
            raise OSError(f"Could not find module {name}")
        return object()

    import ctypes

    monkeypatch.setattr(ctypes, "CDLL", refuse_dll)

    assert bempp._missing_windows_runtime_dlls() == ["vcruntime140_1.dll", "msvcp140.dll"]


def test_dll_probe_is_windows_only(monkeypatch):
    """ctypes.CDLL on those names would fail everywhere else and mislead."""

    monkeypatch.setattr(bempp.platform, "system", lambda: "Darwin")
    assert bempp._missing_windows_runtime_dlls() == []
    monkeypatch.setattr(bempp.platform, "system", lambda: "Linux")
    assert bempp._missing_windows_runtime_dlls() == []


def test_absent_package_is_still_a_plain_unavailable(monkeypatch):
    monkeypatch.setattr(bempp, "_load_api", lambda: False)

    status = bempp.bempp_status()

    assert status["available"] is False
    assert "not importable" in status["reason"]


def test_solving_refuses_when_the_backend_cannot_run(monkeypatch):
    """The guard must sit in front of the solve, not only in the report."""

    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    monkeypatch.setattr(bempp, "SolveConfig", object())
    monkeypatch.setattr(bempp, "bempp_solve", lambda *a, **k: None)
    monkeypatch.setattr(
        bempp,
        "bempp_status",
        lambda: {"available": False, "reason": "numba cannot load", "version": None,
                 "assembly_backend": None},
    )

    class _Context:
        solver_mode = "full_3d"
        frequencies_hz = None

        def validate(self):
            return None

    monkeypatch.setattr(bempp, "reject_bempp_infinite_baffle", lambda _context: None)

    with pytest.raises(bempp.BemppUnavailable, match="numba cannot load"):
        bempp.solve_bempp_from_msh_text("mesh", _Context())
