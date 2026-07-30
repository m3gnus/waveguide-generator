"""Verify that a BEM solve can actually run, not just that packages import.

`hornlab_bempp_bem` is a thin wrapper: importing it succeeds even when the
underlying bempp-cl engine cannot load. On a clean Windows machine that gap is
not theoretical. bempp-cl assembles through numba, numba ships compiled
extensions, and those extensions need the Microsoft Visual C++ Redistributable,
which Windows does not install by default. The result is an install that
reports "Bempp ready" and a strict preflight that reports READY, while every
solve fails with `ImportError: Numba could not be imported`.

This script imports the engine the solve path actually uses and reports which
assembly backends are genuinely usable. Run it from the repo root:

    node scripts/run-backend-python.js server/scripts/check_solver_engine.py

Exit codes:
    0  at least one assembly backend can run a solve
    1  no assembly backend is usable
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from typing import Any

# Compiled-extension runtime DLLs that Windows does not ship by default. numba,
# llvmlite and many other binary wheels link against these.
_WINDOWS_RUNTIME_DLLS = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")

_VCREDIST_GUIDANCE = (
    "Install the Microsoft Visual C++ Redistributable (x64), then re-run the "
    "installer:\n"
    "    winget install --id Microsoft.VCRedist.2015+.x64 --scope user\n"
    "  or download it from "
    "https://aka.ms/vs/17/release/vc_redist.x64.exe"
)


def _missing_windows_runtime_dlls() -> list[str]:
    """Return VC++ runtime DLLs that are not loadable, on Windows only."""
    if platform.system() != "Windows":
        return []
    import ctypes

    missing = []
    for name in _WINDOWS_RUNTIME_DLLS:
        try:
            ctypes.CDLL(name)
        except OSError:
            missing.append(name)
    return missing


def _probe(module: str) -> dict[str, Any]:
    """Import a module and capture the failure instead of propagating it."""
    try:
        __import__(module)
    except BaseException as exc:  # noqa: BLE001 - numba raises bare ImportError chains
        return {
            "importable": False,
            "error": f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}",
        }
    return {"importable": True, "error": None}


def collect_status() -> dict[str, Any]:
    """Report what can actually assemble and solve on this host."""
    wrapper = _probe("hornlab_bempp_bem")
    # The real engine. bempp-cl >=0.4 exposes `bempp_cl.api`; older builds used
    # `bempp.api`. Accept either so this check does not become the thing that
    # breaks on an upgrade.
    engine = _probe("bempp_cl.api")
    if not engine["importable"]:
        legacy = _probe("bempp.api")
        if legacy["importable"]:
            engine = legacy

    numba = _probe("numba")
    pyopencl = _probe("pyopencl")

    missing_dlls = _missing_windows_runtime_dlls()

    backends = {
        "numba": numba["importable"],
        "opencl": pyopencl["importable"],
    }
    usable = engine["importable"] and any(backends.values())

    guidance: list[str] = []
    if not usable:
        if missing_dlls:
            guidance.append(
                "The Microsoft Visual C++ Redistributable is missing. Compiled "
                "Python extensions (numba, llvmlite) cannot load without it. "
                f"Not loadable: {', '.join(missing_dlls)}."
            )
            guidance.append(_VCREDIST_GUIDANCE)
        elif not numba["importable"] and not pyopencl["importable"]:
            guidance.append(
                "Neither numba nor pyopencl can be imported, so bempp-cl has no "
                "assembly backend. Reinstall backend requirements:\n"
                "    .venv\\Scripts\\python.exe -m pip install -r "
                "server/requirements-bempp.txt"
            )
        if not engine["importable"] and not missing_dlls:
            guidance.append(
                f"bempp-cl engine import failed: {engine['error']}"
            )

    return {
        "usable": usable,
        "wrapper": wrapper,
        "engine": engine,
        "assemblyBackends": backends,
        "missingWindowsRuntimeDlls": missing_dlls,
        "guidance": guidance,
    }


def _print_human(status: dict[str, Any]) -> None:
    print("Solver engine check")
    print(
        f"  hornlab_bempp_bem wrapper : "
        f"{'OK' if status['wrapper']['importable'] else 'FAILED'}"
    )
    engine = status["engine"]
    print(
        f"  bempp-cl engine           : "
        f"{'OK' if engine['importable'] else 'FAILED'}"
        + ("" if engine["importable"] else f"  ({engine['error']})")
    )
    for name, ok in status["assemblyBackends"].items():
        print(f"  assembly backend {name:<8} : {'available' if ok else 'unavailable'}")

    if status["usable"]:
        print("  Result: a solve can run on this host.")
        return

    print("  Result: NO solve can run on this host.")
    for line in status["guidance"]:
        print()
        for part in line.splitlines():
            print(f"  {part}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    status = collect_status()
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        _print_human(status)
    return 0 if status["usable"] else 1


if __name__ == "__main__":
    sys.exit(main())
