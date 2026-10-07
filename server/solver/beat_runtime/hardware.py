"""Hardware eligibility, separate from compiled-solve readiness."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import platform
import shutil
import subprocess
import threading
import time

from server.platform.process import background_process_kwargs

GPU_BACKENDS = ("cuda", "rocm", "metal")
_ROCM_ENV_VARS = ("BLAB_ROCM_PATH", "ROCM_PATH", "HIP_PATH", "ROCM_HOME")
_NEGATIVE_TTL = 30.0
_cache_lock = threading.Lock()
_detection_cache: dict[tuple, tuple[bool, str, float | None]] = {}
_probe_locks: dict[tuple, threading.Lock] = {}
_cache_generation = 0


def clear_hardware_cache() -> None:
    """Forget process-local hardware facts for explicit reprobe/refresh."""
    global _cache_generation
    with _cache_lock:
        _detection_cache.clear()
        _probe_locks.clear()
        _cache_generation += 1


def _nvidia_gpu_present(*, environ: Mapping[str, str], probe: bool = True) -> bool:
    executable = shutil.which("nvidia-smi", path=environ.get("PATH", os.defpath))
    if executable is None:
        return False
    if not probe:
        return True  # Launch-time hint only; the background worker verifies it.
    try:
        return subprocess.run(
            [executable, "-L"], capture_output=True, timeout=15.0, check=False,
            stdin=subprocess.DEVNULL, env=dict(environ), **background_process_kwargs(),
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _rocm_present(*, environ: Mapping[str, str]) -> bool:
    for name in _ROCM_ENV_VARS:
        value = environ.get(name, "").strip()
        if value and Path(value).is_dir():
            return True
    return any(shutil.which(name, path=environ.get("PATH", os.defpath))
               for name in ("rocminfo", "hipinfo", "hipInfo"))


def _metal_hardware(system: str, machine: str, version: str) -> tuple[bool, str]:
    reason = "Metal requires Apple Silicon and macOS 13.3+"
    eligible = False
    if system == "Darwin" and machine.lower() in {"arm64", "aarch64"}:
        if version == "10.16":
            try:
                version = subprocess.check_output(
                    ["/usr/bin/sw_vers", "-productVersion"], text=True,
                    stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
                ).strip()
            except (OSError, subprocess.SubprocessError):
                version = ""
        try:
            parts = tuple(int(part) for part in version.split("."))
            eligible = all(part >= 0 for part in parts) and (parts + (0,))[:2] >= (13, 3)
        except ValueError:
            pass
        reason = "Apple Silicon with macOS 13.3+" if eligible else "Metal requires macOS 13.3+"
    return eligible, reason


def gpu_hardware(
    *, system: str | None = None, machine: str | None = None,
    macos_version: str | None = None, environ: Mapping[str, str] | None = None,
    probe_nvidia: bool = True, only: str | None = None,
) -> dict[str, dict[str, bool | str]]:
    """Report only the requested family, or the full inventory, without Julia.

    Positive detection lasts until explicit invalidation or process exit;
    negative/timeout detection lasts 30 s. PATH and all ROCm roots select the
    cache, along with platform inputs. NVIDIA PATH hints use a separate key
    so a launcher hint can never replace the verified subprocess result.
    """
    if only is not None and only not in GPU_BACKENDS:
        raise ValueError(f"Unknown GPU backend: {only!r}")
    env = dict(os.environ if environ is None else environ)
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    version = (platform.mac_ver()[0] if macos_version is None else macos_version) if system == "Darwin" else ""
    supported = system in {"Linux", "Windows"}
    environment_key = (env.get("PATH", os.defpath), *(env.get(name, "") for name in _ROCM_ENV_VARS))
    rows = {}
    for backend in (GPU_BACKENDS if only is None else (only,)):
        key = (backend, system, machine, version, environment_key,
               probe_nvidia if backend == "cuda" else None)
        # Share concurrent identical probes without blocking other families/hints.
        with _cache_lock:
            probe_lock = _probe_locks.setdefault(key, threading.Lock())
        with probe_lock:
            with _cache_lock:
                cached = _detection_cache.get(key)
                generation = _cache_generation
            if cached is None or (cached[2] is not None and time.monotonic() >= cached[2]):
                if backend == "metal":
                    available, reason = _metal_hardware(system, machine, version)
                elif backend == "cuda":
                    available = supported and _nvidia_gpu_present(environ=env, probe=probe_nvidia)
                    reason = "NVIDIA GPU detected" if available else (
                        "No NVIDIA GPU detected" if supported else "CUDA requires Linux or Windows")
                else:
                    available = supported and _rocm_present(environ=env)
                    reason = "ROCm runtime detected" if available else (
                        "No ROCm runtime detected" if supported else "ROCm requires Linux or Windows")
                cached = (available, reason, None if available else time.monotonic() + _NEGATIVE_TTL)
                with _cache_lock:
                    # An explicit refresh during this probe must remain effective.
                    if generation == _cache_generation:
                        _detection_cache[key] = cached
            rows[backend] = {"available": cached[0], "reason": cached[1]}
    return rows


def detect_gpu_backend(
    *, environ: Mapping[str, str] | None = None,
    inventory: Mapping[str, Mapping[str, bool | str]] | None = None,
) -> str | None:
    """Suggest CUDA, then ROCm, then Metal; hardware is never readiness."""
    facts = gpu_hardware(environ=environ) if inventory is None else inventory
    return next((backend for backend in GPU_BACKENDS if facts[backend]["available"]), None)
