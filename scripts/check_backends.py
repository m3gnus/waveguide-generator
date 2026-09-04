#!/usr/bin/env python3
"""Report whether a solve can actually run on this host, and exit non-zero if not.

v1 shipped an installer that reported "Bempp ready" on machines where every
solve then died: ``hornlab_bempp_bem`` is a thin pure-Python wrapper and imports
happily on a clean Windows box whose compiled extensions cannot load at all.
Counting OpenCL devices was no better -- a device that initialises can still
fail to compile the assembly kernel.

So this asks v2's own capability probes, the ones the solve path consults, and
prints the same three facts they carry: which backend was resolved, why, and the
remedy when it was not.  It must be a file rather than an inline ``python -c``
because cmd.exe mis-parses a quoted argument containing parentheses inside a
parenthesised block -- v1 lost a healthy ``.venv`` on every Windows run to
exactly that.

It also compares ``pins.json`` against what is installed, because a probe can
say "ready" about the wrong commit.  A drifted venv is what dropped coupled
infinite baffle and axisymmetric cancellation off a Windows box while every
module still reported version ``0.1.0``
(``docs/validation/2026-08/PINNED-VS-INSTALLED.md``).  Drift is printed loudly
but does not set the exit status: the host can still solve, and failing an
install over it would be a different, harsher claim than this script makes.
"""

from __future__ import annotations

import os
from pathlib import Path
import platform
import sys


_IMPORT_ROOT = Path(
    os.environ.get("WG2_APP_ROOT") or Path(__file__).resolve().parents[1]
).expanduser().resolve()
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from server.platform.paths import app_root  # noqa: E402


REPO_ROOT = app_root()


def _report(label: str, status: dict[str, object]) -> bool:
    available = bool(status.get("available"))
    reason = str(status.get("reason") or "").strip()
    print(f"  {label}: {'ready' if available else 'not available'}")
    if reason:
        print(f"    {reason}")
    return available


def report_dependency_drift() -> list[str]:
    """Print declared-vs-installed for every pinned module; return the drift.

    Kept ASCII-only and free of box drawing so a Windows console in any code
    page renders it, for the same reason this is a file and not a ``-c``.
    """

    from server.integration.installed import measure_installed_stack
    from server.integration.provenance import pinned_dependency_shas

    pinned = pinned_dependency_shas()
    if not pinned:
        return []
    installed, drift = measure_installed_stack(pinned)

    print()
    print("Pinned dependencies:")
    width = max(len(name) for name in pinned)
    for name in sorted(pinned):
        measured = installed.get(name)
        if measured is None:
            state = "NOT MEASURED (not installed from Git, or unreadable)"
        elif measured == pinned[name]:
            state = f"{measured[:9]} matches"
        else:
            state = f"{measured[:9]} INSTALLED, {pinned[name][:9]} PINNED -- DRIFTED"
        print(f"  {name.ljust(width)}  {state}")

    if drift:
        print()
        print(f"WARNING: {len(drift)} of {len(pinned)} pinned modules do not match what")
        print("         this environment has installed, so solve results from this")
        print("         host describe a stack it is not running. Capability probes")
        print("         and package version strings cannot see this.")
        print(f"         Drifted: {', '.join(drift)}")
        print("         Reinstall the pinned set:")
        print("           python -m pip install -r server/requirements-pins.txt")
    return drift


def main() -> int:
    from server.solver.bempp import _missing_windows_runtime_dlls, bempp_status
    from server.solver.beat import (
        BEAT_BACKENDS,
        BEAT_BACKEND_LABELS,
        beat_backend_statuses,
    )
    from server.solver.circsym import circsym_status
    from server.solver.metal import metal_status

    print("Solve backends:")
    axisym = _report("Axisymmetric (portable CPU)", circsym_status())
    metal = _report("Metal (Apple Silicon)", metal_status())
    # One line per BEAT execution backend, the same four the app offers. A
    # single "BEAT" line here could only report whichever one the probe named,
    # which is exactly the question a person running this script is asking.
    beat_statuses = beat_backend_statuses()
    beat = False
    for backend in BEAT_BACKENDS:
        label = BEAT_BACKEND_LABELS.get(backend, f"BEAT ({backend})")
        beat = _report(label, beat_statuses[backend]) or beat
    bempp = _report("bempp (cross-platform)", bempp_status())

    report_dependency_drift()

    if axisym or metal or beat or bempp:
        return 0

    print()
    print("ERROR: no solve backend can run on this host, so simulations would all fail.")
    if platform.system() == "Windows":
        missing = _missing_windows_runtime_dlls()
        if missing:
            print(f"       Missing runtime DLLs: {', '.join(missing)}")
            print("       Install the Microsoft Visual C++ Redistributable (x64):")
            print("         winget install --id Microsoft.VCRedist.2015+.x64 --scope user")
            print("       or https://aka.ms/vs/17/release/vc_redist.x64.exe")
    print("       Fix the reason printed above, then run the installer again.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
