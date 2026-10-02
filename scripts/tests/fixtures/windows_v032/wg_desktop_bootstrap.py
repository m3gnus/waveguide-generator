"""Bootstrap the double-clickable Windows executable."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import sys
import traceback


def _is_direct_launch() -> bool:
    return (
        sys.argv[0] == ""
        and PureWindowsPath(sys.executable).name.casefold() == "waveguide generator.exe"
    )


# Per-user site packages are switched off by the pyvenv.cfg beside the
# launcher, which takes effect before the interpreter runs any of this. There
# is deliberately nothing to do here: removing sys.path entries at this point
# would be theatre, because site.main() has already executed every .pth file
# in the user site directory by the time sitecustomize is imported.


if _is_direct_launch():
    bundle_root = Path(sys.executable).resolve().parent
    app_root = bundle_root / "app"
    os.environ["WG2_BUNDLE"] = "1"
    os.environ["WG2_APP_ROOT"] = str(app_root)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        cache_root = Path(local_app_data) / "WaveguideGenerator" / "cache"
        os.environ.setdefault("PYTHONPYCACHEPREFIX", str(cache_root / "pycache"))
        os.environ.setdefault("NUMBA_CACHE_DIR", str(cache_root / "numba"))
        # PYTHONPYCACHEPREFIX is read at interpreter start-up, long before a
        # sitecustomize import, so setting it here only ever reaches child
        # processes.  Assigning sys.pycache_prefix is what stops *this* process
        # from writing __pycache__ into the swappable app and runtime layers.
        sys.pycache_prefix = os.environ["PYTHONPYCACHEPREFIX"]
    try:
        # Deliberately not app_root.  Windows keeps an open handle on a
        # process's current directory, and a failed update has to rename
        # ``app`` out of the way while this very process asks for the rollback.
        # The bundle root is never renamed.
        os.chdir(bundle_root)
        from launchers.desktop import main

        result = main(sys.argv[1:])
    except Exception as exc:
        from launchers.statusapp.__main__ import _report_startup_failure

        _report_startup_failure(
            "Waveguide Generator could not start: "
            f"{type(exc).__name__}: {exc}",
            detail=traceback.format_exc(),
        )
        result = 1
    raise SystemExit(result)
