import contextlib
import unittest
from unittest.mock import patch

from scripts import check_solver_engine
from solver.device_inventory import (
    acceleration_summary,
    device_mode_readiness,
    opencl_device_inventory,
)

MODULE = "solver.device_inventory"


class _FakeDeviceType:
    CPU = 2
    GPU = 4
    ACCELERATOR = 8


class _FakeDevice:
    def __init__(
        self,
        *,
        name,
        device_type,
        vendor="Fake Vendor",
        driver_version="1.0.0",
        max_compute_units=12,
        global_mem_bytes=16379 * 1024 * 1024,
        double_fp_config=63,
    ):
        self.name = name
        self.type = device_type
        self.vendor = vendor
        self.driver_version = driver_version
        self.max_compute_units = max_compute_units
        self.global_mem_size = global_mem_bytes
        self.double_fp_config = double_fp_config
        self.extensions = "cl_khr_fp64" if double_fp_config else ""


class _FakePlatform:
    def __init__(
        self,
        *,
        name="Intel(R) OpenCL",
        vendor="Intel(R) Corporation",
        version="OpenCL 3.0 WINDOWS",
        devices=(),
        device_error=None,
    ):
        self.name = name
        self.vendor = vendor
        self.version = version
        self._devices = list(devices)
        self._device_error = device_error

    def get_devices(self):
        if self._device_error is not None:
            raise self._device_error
        return list(self._devices)


class _FakePyOpenCL:
    device_type = _FakeDeviceType

    def __init__(self, platforms=(), platform_error=None):
        self._platforms = list(platforms)
        self._platform_error = platform_error

    def get_platforms(self):
        if self._platform_error is not None:
            raise self._platform_error
        return list(self._platforms)


def _cpu_device(name="AMD Ryzen 7 5825U with Radeon Graphics"):
    return _FakeDevice(name=name, device_type=_FakeDeviceType.CPU)


def _gpu_device(name="Fake OpenCL GPU"):
    return _FakeDevice(
        name=name,
        device_type=_FakeDeviceType.GPU,
        max_compute_units=8,
        global_mem_bytes=4096 * 1024 * 1024,
    )


def _patch_runtime(
    *,
    binding=None,
    binding_reason="pyopencl is not importable (ModuleNotFoundError: No module named 'pyopencl').",
    configure=None,
    configure_reason="hornlab_bempp_bem.device.configure_opencl is unavailable.",
    numba=None,
    kernel_build=None,
):
    """Patch the guarded optional-import helpers and the kernel-build probe.

    ``kernel_build`` defaults to success so that tests about device enumeration
    stay about device enumeration. The probe has its own dedicated tests.
    """
    numba = numba or {"available": True, "reason": "numba 0.61.0 is available."}
    kernel_build = kernel_build or {"ok": True, "reason": "OpenCL kernel compiled successfully."}
    return (
        patch(
            f"{MODULE}._import_pyopencl",
            return_value=(binding, None if binding is not None else binding_reason),
        ),
        patch(
            f"{MODULE}._load_configure_opencl",
            return_value=(configure, None if configure is not None else configure_reason),
        ),
        patch(f"{MODULE}._numba_runtime", return_value=numba),
        patch(f"{MODULE}.opencl_kernel_build_probe", return_value=kernel_build),
    )


class _ConfigureOpenCLStub:
    """Stand-in for hornlab_bempp_bem.device.configure_opencl."""

    def __init__(self, *, names=None, errors=None):
        self.names = names or {}
        self.errors = errors or {}
        self.calls = []

    def __call__(self, device_type="cpu"):
        self.calls.append(device_type)
        if device_type in self.errors:
            raise self.errors[device_type]
        if device_type in self.names:
            return self.names[device_type]
        raise RuntimeError(f"OpenCL {device_type} device could not be initialized.")


class DeviceInventoryTest(unittest.TestCase):
    def _run(self, func, **kwargs):
        patches = _patch_runtime(**kwargs)
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            return func()

    def test_initialized_device_with_unbuildable_kernel_is_not_ready(self):
        """A working OpenCL context is not evidence that a solve can run.

        Regression test for a real failure: on an install path containing a
        space, configure_opencl("cpu") succeeds and every device query looks
        healthy, but bempp-cl's clBuildProgram call fails with
        INVALID_BUILD_OPTIONS because it passes its -I include path unquoted.
        Readiness must reflect that, and the effective backend must fall back
        to numba rather than reporting an OpenCL device it cannot use.
        """
        readiness = self._run(
            lambda: device_mode_readiness("opencl_cpu"),
            binding=_FakePyOpenCL(platforms=[_FakePlatform(devices=[_cpu_device()])]),
            configure=_ConfigureOpenCLStub(names={"cpu": "Fake CPU"}),
            kernel_build={
                "ok": False,
                "reason": (
                    "OpenCL kernel build failed with INVALID_BUILD_OPTIONS. The "
                    "bempp-cl include path contains a space and bempp-cl does not "
                    "quote its -I option, so the compiler splits it."
                ),
            },
        )

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["resolvedDeviceType"], "cpu")
        # The device name is still reported: it initialized, so a "device not
        # found" message would misdirect the user.
        self.assertEqual(readiness["deviceName"], "Fake CPU")
        self.assertIn("assembly kernel cannot be compiled", readiness["reason"])
        self.assertIn("INVALID_BUILD_OPTIONS", readiness["reason"])

        summary = self._run(
            acceleration_summary,
            binding=_FakePyOpenCL(platforms=[_FakePlatform(devices=[_cpu_device()])]),
            configure=_ConfigureOpenCLStub(names={"cpu": "Fake CPU"}),
            kernel_build={"ok": False, "reason": "INVALID_BUILD_OPTIONS"},
        )
        self.assertFalse(summary["openclCpu"]["available"])
        self.assertFalse(summary["acceleratedByGpu"])
        self.assertEqual(summary["effectiveBackend"], "numba")

    # 1. pyopencl missing entirely.
    def test_inventory_reports_binding_missing_without_pyopencl(self):
        inventory = self._run(opencl_device_inventory)

        self.assertFalse(inventory["bindingAvailable"])
        self.assertIn("pyopencl", inventory["bindingReason"])
        self.assertEqual(inventory["platforms"], [])
        self.assertEqual(inventory["cpuDeviceCount"], 0)
        self.assertEqual(inventory["gpuDeviceCount"], 0)

    def test_acceleration_summary_falls_back_to_numba_without_pyopencl(self):
        summary = self._run(acceleration_summary)

        self.assertFalse(summary["openclCpu"]["available"])
        self.assertFalse(summary["openclGpu"]["available"])
        self.assertIsNone(summary["openclCpu"]["deviceName"])
        self.assertIsNone(summary["openclGpu"]["deviceName"])
        self.assertTrue(summary["numba"]["available"])
        self.assertEqual(summary["effectiveBackend"], "numba")
        self.assertFalse(summary["acceleratedByGpu"])

    # 2. Regression test for the core bug.
    def test_cpu_only_opencl_host_is_not_reported_as_gpu_accelerated(self):
        binding = _FakePyOpenCL(platforms=[_FakePlatform(devices=[_cpu_device()])])
        configure = _ConfigureOpenCLStub(
            names={"cpu": "AMD Ryzen 7 5825U with Radeon Graphics"}
        )

        inventory = self._run(
            opencl_device_inventory, binding=binding, configure=configure
        )
        summary = self._run(acceleration_summary, binding=binding, configure=configure)

        self.assertTrue(inventory["bindingAvailable"])
        self.assertEqual(inventory["cpuDeviceCount"], 1)
        self.assertEqual(inventory["gpuDeviceCount"], 0)
        self.assertEqual(len(inventory["platforms"]), 1)
        self.assertEqual(inventory["platforms"][0]["devices"][0]["type"], "cpu")

        self.assertTrue(summary["openclCpu"]["available"])
        self.assertEqual(
            summary["openclCpu"]["deviceName"], "AMD Ryzen 7 5825U with Radeon Graphics"
        )
        self.assertFalse(summary["openclGpu"]["available"])
        self.assertEqual(summary["effectiveBackend"], "opencl_cpu")
        self.assertFalse(summary["acceleratedByGpu"])
        # configure_opencl("gpu") must never be attempted with no GPU device present.
        self.assertNotIn("gpu", configure.calls)

    def test_cpu_device_fields_are_populated(self):
        binding = _FakePyOpenCL(platforms=[_FakePlatform(devices=[_cpu_device()])])
        inventory = self._run(opencl_device_inventory, binding=binding)

        platform = inventory["platforms"][0]
        self.assertEqual(platform["name"], "Intel(R) OpenCL")
        self.assertEqual(platform["vendor"], "Intel(R) Corporation")
        self.assertEqual(platform["version"], "OpenCL 3.0 WINDOWS")

        device = platform["devices"][0]
        self.assertEqual(device["name"], "AMD Ryzen 7 5825U with Radeon Graphics")
        self.assertEqual(device["type"], "cpu")
        self.assertEqual(device["computeUnits"], 12)
        self.assertEqual(device["globalMemMB"], 16379)
        self.assertTrue(device["fp64"])
        self.assertEqual(device["driverVersion"], "1.0.0")

    # 3. A GPU device that initializes.
    def test_gpu_device_reports_gpu_acceleration(self):
        binding = _FakePyOpenCL(
            platforms=[_FakePlatform(devices=[_cpu_device(), _gpu_device()])]
        )
        configure = _ConfigureOpenCLStub(
            names={"cpu": "Fake OpenCL CPU", "gpu": "Fake OpenCL GPU"}
        )

        inventory = self._run(
            opencl_device_inventory, binding=binding, configure=configure
        )
        summary = self._run(acceleration_summary, binding=binding, configure=configure)

        self.assertEqual(inventory["cpuDeviceCount"], 1)
        self.assertEqual(inventory["gpuDeviceCount"], 1)
        self.assertTrue(summary["openclGpu"]["available"])
        self.assertEqual(summary["openclGpu"]["deviceName"], "Fake OpenCL GPU")
        self.assertEqual(summary["effectiveBackend"], "opencl_gpu")
        self.assertTrue(summary["acceleratedByGpu"])

    def test_gpu_present_but_uninitializable_is_not_gpu_accelerated(self):
        binding = _FakePyOpenCL(platforms=[_FakePlatform(devices=[_gpu_device()])])
        configure = _ConfigureOpenCLStub(
            errors={"gpu": RuntimeError("no usable GPU context")}
        )

        summary = self._run(acceleration_summary, binding=binding, configure=configure)

        self.assertFalse(summary["acceleratedByGpu"])
        self.assertEqual(summary["effectiveBackend"], "numba")

    # 4. opencl_gpu on a CPU-only host.
    def test_device_mode_readiness_rejects_opencl_gpu_on_cpu_only_host(self):
        binding = _FakePyOpenCL(platforms=[_FakePlatform(devices=[_cpu_device()])])
        configure = _ConfigureOpenCLStub(names={"cpu": "Fake OpenCL CPU"})

        readiness = self._run(
            lambda: device_mode_readiness("opencl_gpu"),
            binding=binding,
            configure=configure,
        )

        self.assertEqual(readiness["deviceMode"], "opencl_gpu")
        self.assertEqual(readiness["resolvedDeviceType"], "gpu")
        self.assertFalse(readiness["ready"])
        self.assertIsNone(readiness["deviceName"])
        self.assertIn("GPU", readiness["reason"])
        self.assertIn("opencl_cpu", readiness["reason"])
        # The working CPU device must not be credited to the GPU mode.
        self.assertNotIn("gpu", configure.calls)

        cpu_readiness = self._run(
            lambda: device_mode_readiness("opencl_cpu"),
            binding=binding,
            configure=configure,
        )
        self.assertTrue(cpu_readiness["ready"])
        self.assertEqual(cpu_readiness["resolvedDeviceType"], "cpu")

    def test_auto_mirrors_solve_path_and_resolves_to_cpu(self):
        # Mirrors bempp_solver._opencl_device_from_request: only an explicit
        # opencl_gpu selects the GPU, so auto resolves to cpu even with a GPU.
        binding = _FakePyOpenCL(
            platforms=[_FakePlatform(devices=[_cpu_device(), _gpu_device()])]
        )
        configure = _ConfigureOpenCLStub(
            names={"cpu": "Fake OpenCL CPU", "gpu": "Fake OpenCL GPU"}
        )

        readiness = self._run(
            lambda: device_mode_readiness("auto"), binding=binding, configure=configure
        )

        self.assertEqual(readiness["resolvedDeviceType"], "cpu")
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["deviceName"], "Fake OpenCL CPU")
        self.assertEqual(configure.calls, ["cpu"])

    def test_unknown_device_mode_is_reported_not_raised(self):
        binding = _FakePyOpenCL(platforms=[_FakePlatform(devices=[_cpu_device()])])
        readiness = self._run(
            lambda: device_mode_readiness("metal"), binding=binding
        )

        self.assertIsNone(readiness["resolvedDeviceType"])
        self.assertFalse(readiness["ready"])
        self.assertIn("Unknown device_mode", readiness["reason"])

    # 5. configure_opencl raising.
    def test_configure_opencl_failure_is_captured_without_raising(self):
        binding = _FakePyOpenCL(platforms=[_FakePlatform(devices=[_cpu_device()])])
        configure = _ConfigureOpenCLStub(
            errors={"cpu": RuntimeError("OpenCL cpu device could not be initialized.")}
        )

        readiness = self._run(
            lambda: device_mode_readiness("opencl_cpu"),
            binding=binding,
            configure=configure,
        )

        self.assertFalse(readiness["ready"])
        self.assertIsNone(readiness["deviceName"])
        self.assertIn("could not be initialized", readiness["reason"])
        self.assertIn("RuntimeError", readiness["reason"])

        summary = self._run(acceleration_summary, binding=binding, configure=configure)
        self.assertFalse(summary["openclCpu"]["available"])
        self.assertEqual(summary["effectiveBackend"], "numba")

    def test_missing_configure_opencl_entry_point_is_reported(self):
        binding = _FakePyOpenCL(platforms=[_FakePlatform(devices=[_cpu_device()])])
        readiness = self._run(
            lambda: device_mode_readiness("opencl_cpu"), binding=binding
        )

        self.assertFalse(readiness["ready"])
        self.assertIn("configure_opencl", readiness["reason"])

    # 6. Enumeration raising.
    def test_platform_enumeration_failure_returns_structured_reason(self):
        binding = _FakePyOpenCL(platform_error=RuntimeError("clGetPlatformIDs failed"))
        inventory = self._run(opencl_device_inventory, binding=binding)

        self.assertTrue(inventory["bindingAvailable"])
        self.assertEqual(inventory["platforms"], [])
        self.assertEqual(inventory["cpuDeviceCount"], 0)
        self.assertEqual(inventory["gpuDeviceCount"], 0)
        self.assertIn("platform enumeration failed", inventory["reason"])
        self.assertIn("clGetPlatformIDs failed", inventory["reason"])

    def test_partial_device_enumeration_failure_keeps_healthy_platform(self):
        binding = _FakePyOpenCL(
            platforms=[
                _FakePlatform(
                    name="Broken Platform",
                    device_error=RuntimeError("clGetDeviceIDs failed"),
                ),
                _FakePlatform(name="Good Platform", devices=[_cpu_device()]),
            ]
        )
        inventory = self._run(opencl_device_inventory, binding=binding)

        self.assertEqual(inventory["platformCount"], 2)
        self.assertEqual(inventory["cpuDeviceCount"], 1)
        self.assertEqual(inventory["gpuDeviceCount"], 0)
        self.assertEqual(inventory["platforms"][0]["devices"], [])
        self.assertIn("clGetDeviceIDs failed", inventory["platforms"][0]["reason"])
        self.assertIn("Partial enumeration failures", inventory["reason"])

    def test_device_attribute_failure_does_not_abort_enumeration(self):
        class _ExplodingDevice:
            @property
            def name(self):
                raise RuntimeError("device query failed")

        binding = _FakePyOpenCL(
            platforms=[_FakePlatform(devices=[_ExplodingDevice(), _cpu_device()])]
        )
        inventory = self._run(opencl_device_inventory, binding=binding)

        # _safe_attr swallows the failing query; the healthy device still lands.
        self.assertEqual(inventory["cpuDeviceCount"], 1)
        self.assertEqual(inventory["deviceCount"], 2)

    def test_reasons_are_sanitized_of_filesystem_paths(self):
        binding = _FakePyOpenCL(
            platform_error=RuntimeError(
                r"failed loading C:\Users\someone\AppData\icd\vendor.dll"
            )
        )
        inventory = self._run(opencl_device_inventory, binding=binding)

        self.assertNotIn("someone", inventory["reason"])
        self.assertNotIn("C:\\", inventory["reason"])
        self.assertIn("<path>", inventory["reason"])

    def test_public_functions_work_against_the_real_environment(self):
        # No patches: the module must import and run cleanly with neither
        # pyopencl nor a working OpenCL device installed.
        inventory = opencl_device_inventory()
        self.assertIsInstance(inventory["bindingAvailable"], bool)
        self.assertIsInstance(inventory["cpuDeviceCount"], int)
        self.assertIsInstance(inventory["gpuDeviceCount"], int)

        for mode in ("auto", "opencl_cpu", "opencl_gpu"):
            readiness = device_mode_readiness(mode)
            self.assertEqual(readiness["deviceMode"], mode)
            self.assertIsInstance(readiness["ready"], bool)
            self.assertTrue(readiness["reason"])

        summary = acceleration_summary()
        self.assertIn(
            summary["effectiveBackend"], {"opencl_gpu", "opencl_cpu", "numba"}
        )
        self.assertIsInstance(summary["acceleratedByGpu"], bool)


class SolverEngineReadinessTest(unittest.TestCase):
    def test_importable_opencl_without_device_and_unusable_numba_is_not_usable(self):
        def probe(module):
            importable = module in {"hornlab_bempp_bem", "bempp_cl.api", "pyopencl"}
            return {"importable": importable, "error": None if importable else "missing"}

        acceleration = {
            "openclCpu": {"available": False, "reason": "No OpenCL CPU device."},
            "openclGpu": {"available": False, "reason": "No OpenCL GPU device."},
            "numba": {"available": False, "reason": "Numba runtime failed."},
            "effectiveBackend": "numba",
            "acceleratedByGpu": False,
        }
        with patch.object(check_solver_engine, "_probe", side_effect=probe), patch.object(
            check_solver_engine,
            "_metal_status",
            return_value={"ready": False, "reason": "Metal unavailable."},
        ), patch.object(
            check_solver_engine, "_missing_windows_runtime_dlls", return_value=[]
        ), patch.object(
            check_solver_engine, "acceleration_summary", return_value=acceleration
        ):
            status = check_solver_engine.collect_status()

        self.assertFalse(status["bemppUsable"])
        self.assertFalse(status["usable"])
        self.assertTrue(
            any("no assembly backend is runtime-ready" in line for line in status["guidance"])
        )

    def test_runtime_ready_bempp_or_metal_is_usable(self):
        ready_numba = {
            "openclCpu": {"available": False},
            "openclGpu": {"available": False},
            "numba": {"available": True},
        }
        self.assertTrue(check_solver_engine._bempp_runtime_ready(True, ready_numba))
        self.assertFalse(check_solver_engine._bempp_runtime_ready(False, ready_numba))

        unavailable = {
            "openclCpu": {"available": False},
            "openclGpu": {"available": False},
            "numba": {"available": False},
        }
        with patch.object(
            check_solver_engine,
            "_probe",
            return_value={"importable": False, "error": "missing"},
        ), patch.object(
            check_solver_engine,
            "_metal_status",
            return_value={"ready": True, "reason": "Metal BEM backend is ready."},
        ), patch.object(
            check_solver_engine, "_missing_windows_runtime_dlls", return_value=[]
        ), patch.object(
            check_solver_engine, "acceleration_summary", return_value=unavailable
        ):
            status = check_solver_engine.collect_status()

        self.assertFalse(status["bemppUsable"])
        self.assertTrue(status["usable"])


if __name__ == "__main__":
    unittest.main()
