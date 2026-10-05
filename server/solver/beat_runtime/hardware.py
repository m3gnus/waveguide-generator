"""Hardware eligibility, separate from compiled-solve readiness."""

from __future__ import annotations

import platform

UNSUPPORTED = "not supported in this build"


def gpu_hardware(
    *, system: str | None = None, machine: str | None = None,
    macos_version: str | None = None,
) -> dict[str, dict[str, bool | str]]:
    """Report Metal eligibility; CUDA/ROCm are always explicitly unsupported.

    Apple Silicon and macOS 13.3+ are necessary, not proof of a working device.
    Missing/unparseable OS versions fail closed. No packages or tools run here.
    """
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    reason = "Metal requires Apple Silicon and macOS 13.3+"
    eligible = False
    if system == "Darwin" and machine.lower() in {"arm64", "aarch64"}:
        version = platform.mac_ver()[0] if macos_version is None else macos_version
        try:
            parts = tuple(int(part) for part in version.split("."))
            eligible = all(part >= 0 for part in parts) and (parts + (0,))[:2] >= (13, 3)
        except ValueError:
            pass
        reason = "Apple Silicon with macOS 13.3+" if eligible else "Metal requires macOS 13.3+"
    return {
        "metal": {"available": eligible, "reason": reason},
        "cuda": {"available": False, "reason": UNSUPPORTED},
        "rocm": {"available": False, "reason": UNSUPPORTED},
    }


def detect_gpu_backend() -> str | None:
    """Suggest Metal only on an eligible host; this is never readiness."""
    return "metal" if gpu_hardware()["metal"]["available"] else None
