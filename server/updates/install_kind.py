"""Identify the exact installed destination without inspecting or changing Git."""

from __future__ import annotations

import json
import ntpath
import os
from pathlib import Path
import platform
import plistlib
import re
import stat
from typing import Callable, Mapping

from shared import release_assets
from launchers.update_lock import ordinary_path, resolved_path

BUNDLE_ID = "is.hornlab.waveguide-generator-v2"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{D8F99D24-D991-4FB0-91FE-E86D79128D2B}_is1"


def windows_install_location() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
            return value if isinstance(value, str) else None
    except (ImportError, OSError):
        return None


def current_platform(system: str | None = None, machine: str | None = None) -> str | None:
    system = platform.system() if system is None else system
    machine = (platform.machine() if machine is None else machine).lower()
    return {
        ("Darwin", "arm64"): release_assets.MACOS_PLATFORM,
        ("Darwin", "aarch64"): release_assets.MACOS_PLATFORM,
        ("Windows", "amd64"): release_assets.WINDOWS_PLATFORM,
        ("Windows", "x86_64"): release_assets.WINDOWS_PLATFORM,
        ("Linux", "x86_64"): release_assets.LINUX_PLATFORM,
    }.get((system, machine))


def _json(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
        raise ValueError("The installed bundle manifest is not a bounded regular file.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError("The installed bundle manifest is unsupported.")
    return value


def probe_install(
    repo_root: Path,
    running_version: str,
    platform_name: str | None,
    *,
    environ: Mapping[str, str] | None = None,
    registry_reader: Callable[[], str | None] = windows_install_location,
    writable: Callable[[Path], bool] = lambda path: os.access(path, os.W_OK),
) -> dict:
    env = os.environ if environ is None else environ

    def verdict(kind: str, root: Path | None, reason: str | None = None) -> dict:
        return {"kind": kind, "installRoot": str(root) if root else None,
                "updateSupported": reason is None, "reason": reason}

    if env.get("WG2_BUNDLE") != "1":
        return verdict("source", None, "Download the release installer to update this source installation.")
    app = Path(ordinary_path(str(repo_root.absolute())))
    if app.name != "app" or app.is_symlink() or app != resolved_path(app):
        return verdict("unsupported", None, "This bundle uses an unsupported application path.")
    try:
        installed = _json(app / "APP-MANIFEST.json")
        runtime = _json(app.parent / "runtime" / "RUNTIME-MANIFEST.json")
        identity = installed.get("runtimeId")
        if (installed.get("version") != running_version
                or not isinstance(identity, str)
                or re.fullmatch(r"[0-9a-f]{12}", identity) is None
                or runtime.get("runtimeId") != identity or runtime.get("platform") != platform_name):
            raise ValueError("The installed app and runtime identities do not match this process.")
        if platform_name == release_assets.MACOS_PLATFORM:
            target = app.parent.parent.parent
            if app.parent.name != "Resources" or app.parent.parent.name != "Contents" or target.suffix != ".app":
                raise ValueError("The application is outside a supported macOS bundle.")
            if "/AppTranslocation/" in str(target):
                raise ValueError("Move the app out of App Translocation before updating.")
            info_path = target / "Contents" / "Info.plist"
            info_stat = info_path.lstat()
            if not stat.S_ISREG(info_stat.st_mode) or info_stat.st_size > 64 * 1024:
                raise ValueError("The macOS application identity is invalid.")
            with info_path.open("rb") as handle:
                info = plistlib.load(handle)
            if not isinstance(info, dict) or info.get("CFBundleIdentifier") != BUNDLE_ID:
                raise ValueError("The macOS application identity is not Waveguide Generator.")
            if not writable(target.parent):
                raise ValueError("The application folder is read-only; download the installer instead.")
            return verdict("macos", target)
        if platform_name == release_assets.WINDOWS_PLATFORM:
            target = app.parent
            registered = registry_reader()
            if not registered or ntpath.normcase(str(resolved_path(registered))) != ntpath.normcase(str(resolved_path(target))):
                return verdict("portable", target, "This portable or unregistered copy needs a manual installer download.")
            return verdict("windows", target)
        if platform_name == release_assets.LINUX_PLATFORM:
            target = app.parent
            if target.name != "waveguide-generator" or not writable(target.parent):
                raise ValueError("This Linux installation cannot be updated at its exact destination.")
            return verdict("linux", target)
        raise ValueError("No installer is available for this platform architecture.")
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException) as exc:
        return verdict("unsupported", None, str(exc))
