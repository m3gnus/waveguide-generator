"""Keep Fusion's WGLink in step with the Waveguide Generator that pins it.

WG pins an exact add-in commit in ``integrations/wglink/source.json``, but
nothing ever reconciled that pin with what Fusion actually had. The platform
installer installs the add-in; the in-app updater swaps the app and runtime
layers and never touches it. So a WG that updated itself kept talking to
whatever add-in was last installed by hand, with no version check anywhere and
no way for either side to notice -- and the add-in's own ``adapterVersion``
does not move commit to commit, so it could not have noticed either.

This closes that at startup, where the answer is cheap: compare the commit
recorded in the installed add-in's marker with the pin, and update when they
differ. A release ships the pinned package inside the app layer, so the update
is a local file copy and needs no network.

WG always uses the add-in it ships: WG and WGLink speak one delivery version
and WG refuses an older add-in (docs/architecture/CAD-OPERATIONS.md, "Delivery
version"). So startup also installs the shipped package where Fusion has no
WGLink yet, and replaces a WGLink that no Waveguide Generator manages -- one
copied in by hand, from before WG managed it. Both happen only where Fusion is
installed for this user, and only from the verified package this build ships.

Two installs are deliberately left alone, each for its own reason. One synced
by ``dev_sync_wglink.py`` belongs to whoever is editing it. One whose marker
names a different Waveguide Generator root is managed by that installation, and
two WG copies fighting over a single add-in is the failure the marker exists to
prevent. WG still refuses either while it is too old, and says why.

``WG2_WGLINK_REFRESH=0`` turns the startup refresh off; the test suite sets it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
from types import ModuleType

from server.cadlink.fusion_status import fusion_process_running
from server.platform.paths import app_root
from server.platform.warmup import BackgroundWarmup


log = logging.getLogger("wg.cadlink.addin")

#: Written beside the add-in by ``scripts/dev_sync_wglink.py`` in the add-in
#: repository. Its presence means a person is editing this install.
DEV_MARKER = "wglink_dev.json"
INSTALL_MARKER = "wglink_install.json"
#: ``0`` turns the startup refresh off.
REFRESH_ENV = "WG2_WGLINK_REFRESH"
# The verdict of this process's startup refresh, for the status the UI shows
# when Fusion is still running an older add-in.
_last_refresh: tuple[str, str] | None = None


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


def _other_manager(target: Path) -> str | None:
    """The Waveguide Generator root another installation's marker names, if any."""

    try:
        payload = json.loads((target / INSTALL_MARKER).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, Mapping) or payload.get("managedBy") != "waveguide-generator":
        return None
    other = payload.get("waveguideGeneratorRoot")
    if not isinstance(other, str) or not other:
        return None
    # A marker naming a WG that is no longer there -- the app was moved, or
    # removed -- manages nothing: the add-in is replaced like an unmanaged one.
    if not (Path(other) / "scripts" / "install_wglink.py").is_file():
        return None
    return other


def _fusion_installed(addins_dir: Path) -> bool:
    """Whether Fusion is installed for this user: its API folder, or a process."""

    return Path(addins_dir).expanduser().parent.is_dir() or fusion_process_running()


def _verified_shipped_package(
    root: Path,
    installer: ModuleType,
    pin: str,
) -> tuple[Path | None, str | None]:
    """Return this build's verified, offline WGLink package.

    First-install is deliberately not allowed to fall back to the source fetch:
    startup must never turn an absent optional add-in into a network operation.
    The platform installer is the consent boundary, and release bundles carry
    the package that it is allowed to install.
    """

    try:
        version_payload = json.loads(
            (root / "shared" / "version.json").read_text(encoding="utf-8")
        )
        if not isinstance(version_payload, Mapping):
            return None, "this build has no valid Waveguide Generator version"
        version = version_payload.get("version")
        if not isinstance(version, str) or not version:
            return None, "this build has no valid Waveguide Generator version"
        archive = Path(installer.shipped_package(root, version, pin))
    except (AttributeError, OSError, UnicodeError, ValueError, TypeError) as exc:
        return None, f"the bundled WGLink package could not be located: {exc}"
    if not archive.is_file():
        return None, f"the bundled WGLink package is missing: {archive.name}"
    try:
        installer.verify_package(archive, root=root)
    except Exception as exc:  # noqa: BLE001 - package verification is a user verdict
        return None, f"the bundled WGLink package failed verification: {exc}"
    return archive, None


def refresh_wglink(
    *,
    root: Path | None = None,
    addins_dir: Path | None = None,
    data_dir: Path | None = None,
    install_absent: bool = True,
) -> tuple[str, str]:
    """Bring Fusion's WGLink up to the add-in this build ships. Never raises.

    Returns ``(verdict, detail)``. The verdict is for tests, the log line and
    the status the UI shows; the detail is what a person reading it needs. An
    absent add-in is installed from the verified shipped package where Fusion
    is installed for this user; ``install_absent=False`` leaves it absent. An
    explicit ``addins_dir`` is treated as a test/setup override of that check.
    """

    root = (root or app_root()).resolve()
    explicit_addins_dir = addins_dir is not None
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
    try:
        # The platform installer journals the two renames that replace WGLink.
        # Recover before looking at the target: after a process dies just after
        # moving the old target to ``previous``, the target is absent but a
        # perfectly valid managed install is still recoverable.
        installer.managed_target(
            root=root,
            platform=installer._platform_name(),
            addins_dir=Path(directory),
        )
    except Exception as exc:  # noqa: BLE001 - startup must never raise
        return "failed", f"could not recover the WGLink installation: {exc}"
    target = Path(directory).expanduser().resolve() / "WGLink"
    target_present = target.exists() or target.is_symlink()
    if not target_present:
        if not install_absent:
            return "absent", "no WGLink is installed for Fusion; installation was not requested"
        if not explicit_addins_dir and not _fusion_installed(Path(directory)):
            return "not-detected", "Fusion 360 is not installed for this user; WGLink was not installed"
        archive, package_error = _verified_shipped_package(root, installer, pin)
        if package_error is not None or archive is None:
            return "unavailable", package_error or "the bundled WGLink package is unavailable"
        try:
            status, installed = installer.install(
                root=root,
                addins_dir=Path(directory),
                data_dir=data_dir,
                python=Path(sys.executable),
                archive_path=archive,
            )
        except Exception as exc:  # noqa: BLE001 - never let this stop the app starting
            return "failed", f"could not install WGLink to {target}: {exc}"
        if status != "installed":
            return "failed", f"the WGLink installer returned {status!r} for an absent add-in"
        installed_target = Path(installed) if installed is not None else target
        return "installed", f"installed WGLink at {installed_target}; restart Fusion"
    if target.is_symlink() or not target.is_dir():
        return "external", f"{target} is an external WGLink target; left alone"
    if (target / DEV_MARKER).is_file():
        return "developer", f"{target} is a developer sync; left alone"
    replace_external = not installer.is_managed_target(target, root)
    if replace_external:
        other = _other_manager(target)
        if other is not None:
            return (
                "external",
                f"{target} is managed by another Waveguide Generator at {other}; left alone",
            )
        current = None
        # Replacing an add-in nobody manages takes only the verified package
        # this build ships, as a first install does; never a fetch.
        archive, package_error = _verified_shipped_package(root, installer, pin)
        if package_error is not None or archive is None:
            return "unavailable", package_error or "the bundled WGLink package is unavailable"
        package = {"archive_path": archive}
    else:
        current = installed_commit(target)
        if current == pin:
            return "current", f"WGLink is at the pinned {pin[:12]}"
        # A packaged app must never turn startup reconciliation into a network
        # fetch. Source checkouts retain the existing fetch path.
        package = {"offline_only": bool(getattr(installer, "_bundled", lambda: False)())}

    try:
        status, _installed = installer.install(
            root=root, addins_dir=Path(directory), data_dir=data_dir,
            python=Path(sys.executable),
            # A WGLink no WG manages is replaced by the one this build ships.
            replace_external=replace_external,
            **package,
        )
    except Exception as exc:  # noqa: BLE001 - never let this stop the app starting
        return "failed", f"could not update WGLink to {pin[:12]}: {exc}"
    if status != "installed":
        return "failed", f"the WGLink installer returned {status!r}"
    if replace_external:
        return (
            "replaced",
            f"replaced a WGLink no Waveguide Generator managed with {pin[:12]}; restart Fusion",
        )
    was = current[:12] if current else "an unrecorded commit"
    return "updated", f"updated WGLink from {was} to {pin[:12]}; restart Fusion"


def refresh_and_log() -> tuple[str, str]:
    global _last_refresh
    if os.environ.get(REFRESH_ENV, "").strip() == "0":
        verdict, detail = "disabled", f"{REFRESH_ENV}=0"
    else:
        verdict, detail = refresh_wglink()
    _last_refresh = (verdict, detail)
    # A change is news; everything else is the ordinary state of a machine and
    # belongs at debug so a normal start stays quiet.
    (log.info if verdict in {"updated", "installed", "replaced", "failed"} else log.debug)(
        "WGLink refresh: %s -- %s", verdict, detail
    )
    return verdict, detail


def last_refresh() -> dict[str, str] | None:
    """What this process's startup refresh did, or None before it finished."""

    if _last_refresh is None:
        return None
    verdict, detail = _last_refresh
    return {"verdict": verdict, "detail": detail}


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
    "last_refresh",
    "pinned_commit",
    "refresh_and_log",
    "refresh_wglink",
    "shutdown_addin_refresh",
    "start_addin_refresh",
]
