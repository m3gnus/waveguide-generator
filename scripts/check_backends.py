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


def main() -> int:
    from server.solver.bempp import _missing_windows_runtime_dlls, bempp_status
    from server.solver.beat import beat_status
    from server.solver.circsym import circsym_status
    from server.solver.metal import metal_status

    print("Solve backends:")
    axisym = _report("Axisymmetric (portable CPU)", circsym_status())
    metal = _report("Metal (Apple Silicon)", metal_status())
    beat = _report("BEAT (CUDA/ROCm)", beat_status())
    bempp = _report("bempp (cross-platform)", bempp_status())

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
