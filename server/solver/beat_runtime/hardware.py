"""Hardware eligibility, separate from compiled-solve readiness."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import platform
import shutil
import subprocess

GPU_BACKENDS = ("cuda", "rocm", "metal")


def _nvidia_gpu_present(*, environ: Mapping[str, str], probe: bool = True) -> bool:
    executable = shutil.which("nvidia-smi", path=environ.get("PATH", os.defpath))
    if executable is None:
        return False
    if not probe:
        return True  # Launch-time hint only; the background worker verifies it.
    try:
        return subprocess.run(
            [executable, "-L"], capture_output=True, timeout=15.0, check=False,
            stdin=subprocess.DEVNULL, env=dict(environ),
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _rocm_present(*, environ: Mapping[str, str]) -> bool:
    for name in ("BLAB_ROCM_PATH", "ROCM_PATH", "HIP_PATH", "ROCM_HOME"):
        value = environ.get(name, "").strip()
        if value and Path(value).is_dir():
            return True
    return any(shutil.which(name, path=environ.get("PATH", os.defpath))
               for name in ("rocminfo", "hipinfo", "hipInfo"))


def gpu_hardware(
    *, system: str | None = None, machine: str | None = None,
    macos_version: str | None = None, environ: Mapping[str, str] | None = None,
    probe_nvidia: bool = True,
) -> dict[str, dict[str, bool | str]]:
    """Report GPU eligibility without importing either engine or starting Julia.

    Apple Silicon and macOS 13.3+ are necessary, not proof of a working device.
    Missing/unparseable OS versions fail closed. Old-SDK Python may report 10.16;
    use sw_vers for that compatibility value. x86_64 Python under Rosetta is
    refused, since its selected Julia would also be x86_64.
    """
    env = os.environ if environ is None else environ
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    reason = "Metal requires Apple Silicon and macOS 13.3+"
    eligible = False
    if system == "Darwin" and machine.lower() in {"arm64", "aarch64"}:
        version = platform.mac_ver()[0] if macos_version is None else macos_version
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
    supported = system in {"Linux", "Windows"}
    cuda = supported and _nvidia_gpu_present(environ=env, probe=probe_nvidia)
    rocm = supported and _rocm_present(environ=env)
    return {
        "metal": {"available": eligible, "reason": reason},
        "cuda": {"available": cuda, "reason": "NVIDIA GPU detected" if cuda else (
            "No NVIDIA GPU detected" if supported else "CUDA requires Linux or Windows")},
        "rocm": {"available": rocm, "reason": "ROCm runtime detected" if rocm else (
            "No ROCm runtime detected" if supported else "ROCm requires Linux or Windows")},
    }


def detect_gpu_backend(*, environ: Mapping[str, str] | None = None) -> str | None:
    """Suggest CUDA, then ROCm, then Metal; hardware is never readiness."""
    facts = gpu_hardware() if environ is None else gpu_hardware(environ=environ)
    return next((backend for backend in GPU_BACKENDS if facts[backend]["available"]), None)
