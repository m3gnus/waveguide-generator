"""Keep Fusion's WGLink in step with the Waveguide Generator that pins it.

WG pins an exact add-in commit in ``integrations/wglink/source.json``, but
nothing ever reconciled that pin with what Fusion actually had. The platform
installer installs the add-in; the in-app updater swaps the app and runtime
layers and never touches it. So a WG that updated itself kept talking to
whatever add-in was last installed by hand, with no version check anywhere and
no way for either side to notice -- and the add-in's own ``adapterVersion``
does not move commit to commit, so it could not have noticed either.

This closes that at startup, where the answer is cheap: compare the commit
recorded in the installed add-in's marker with the pin, and install when they
differ. A release ships the pinned package inside the app layer, so the update
is a local file copy and needs no network.

Three installs are deliberately left alone, each for its own reason. One synced
by ``dev_sync_wglink.py`` belongs to whoever is editing it. One with no WG
marker was installed by something else. One whose marker names a different
Waveguide Generator root is managed by that installation, and two WG copies
fighting over a single add-in is the failure the marker exists to prevent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import importlib.util
import json
import logging
from pathlib import Path
import sys
from types import ModuleType

from server.platform.paths import app_root
from server.platform.warmup import BackgroundWarmup


log = logging.getLogger("wg.cadlink.addin")

#: Written beside the add-in by ``scripts/dev_sync_wglink.py`` in the add-in
#: repository. Its presence means a person is editing this install.
DEV_MARKER = "wglink_dev.json"
INSTALL_MARKER = "wglink_install.json"


def _installer(root: Path) -> ModuleType:
    path = root / "scripts" / "install_wglink.py"
    spec = importlib.util.spec_from_file_location("wg_install_wglink_runtime", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pinned_commit(root: Path) -> str | None:
    try:
        payload = json.loads(
            (root / "integrations" / "wglink" / "source.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    commit = payload.get("commit")
    return str(commit) if isinstance(commit, str) and commit else None


def installed_commit(target: Path) -> str | None:
    try:
        payload = json.loads((target / INSTALL_MARKER).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    commit = payload.get("sourceCommit")
    return str(commit) if isinstance(commit, str) and commit else None


def refresh_wglink(
    *,
    root: Path | None = None,
    addins_dir: Path | None = None,
    data_dir: Path | None = None,
) -> tuple[str, str]:
    """Bring the installed add-in up to this WG's pin. Never raises.

    Returns ``(verdict, detail)``. The verdict is for tests and for the log
    line; the detail is what a person reading that line needs.
    """

    root = (root or app_root()).resolve()
    try:
        installer = _installer(root)
    except Exception as exc:  # noqa: BLE001 - a WG that cannot read its own installer
        return "unavailable", f"the WGLink installer is not readable: {exc}"

    pin = pinned_commit(root)
    if pin is None:
        return "unavailable", "this build declares no WGLink pin"

    directory = addins_dir or installer.default_addins_dir(installer._platform_name())
    if directory is None:
        return "unsupported", "Fusion is not supported on this platform"
    target = Path(directory).expanduser() / "WGLink"
    if not (target / "WGLink.py").is_file():
        return "absent", "no WGLink is installed for Fusion"
    if (target / DEV_MARKER).is_file():
        return "developer", f"{target} is a developer sync; left alone"
    if not installer.is_managed_target(target, root):
        return "external", f"{target} is not managed by this Waveguide Generator"

    current = installed_commit(target)
    if current == pin:
        return "current", f"WGLink is at the pinned {pin[:12]}"

    try:
        status, _installed = installer.install(
            root=root, addins_dir=Path(directory), data_dir=data_dir,
            python=Path(sys.executable),
        )
    except Exception as exc:  # noqa: BLE001 - never let this stop the app starting
        return "failed", f"could not update WGLink to {pin[:12]}: {exc}"
    if status != "installed":
        return "failed", f"the WGLink installer returned {status!r}"
    was = current[:12] if current else "an unrecorded commit"
    return "updated", f"updated WGLink from {was} to {pin[:12]}; restart Fusion"


def refresh_and_log() -> tuple[str, str]:
    verdict, detail = refresh_wglink()
    # An update is news; everything else is the ordinary state of a machine and
    # belongs at debug so a normal start stays quiet.
    (log.info if verdict in {"updated", "failed"} else log.debug)(
        "WGLink refresh: %s -- %s", verdict, detail
    )
    return verdict, detail


async def _refresh_off_thread() -> None:
    await asyncio.to_thread(refresh_and_log)


#: The process-wide reconciliation. One installed add-in per machine, so one
#: task per process, started at boot and drained at shutdown like every other.
addin_refresh = BackgroundWarmup("wglink-refresh", _refresh_off_thread)


async def start_addin_refresh() -> None:
    """Start the reconciliation. This must never delay startup."""

    await addin_refresh.start()


async def shutdown_addin_refresh() -> None:
    """Finish a reconciliation still running when the server stops.

    Draining rather than cancelling matters more here than for a warmup: this
    one can be mid-install, and abandoning it would leave a staging directory
    beside the user's add-in.
    """

    await addin_refresh.stop()


__all__ = [
    "addin_refresh",
    "installed_commit",
    "pinned_commit",
    "refresh_and_log",
    "refresh_wglink",
    "shutdown_addin_refresh",
    "start_addin_refresh",
]
