"""Supported backend argv/environment shared by desktop, CLI and installed clients."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import sys

from launch.serve_options import UPDATE_STAGING_ROOT_ENV
from server.platform.paths import app_root


def backend_argv(
    *,
    repo_root: Path | None = None,
    python_executable: str | Path | None = None,
    server_args: Sequence[str] = (),
    server_command: Sequence[str] | None = None,
    port: int | None,
    data_dir: Path | None = None,
    control_path: Path | None = None,
    headless: bool = False,
) -> list[str]:
    """Build a supported start; data_dir is an explicit override, never discovery.

    CLI clients can supply their remaining serve options in server_args and
    an override in data_dir, using backend_environment with the same override.
    A port of None leaves port selection to serve.py.
    """
    # Do not resolve the interpreter symlink: that loses venv recognition.
    command = (
        list(server_command)
        if server_command is not None
        else [
            str(Path(python_executable or sys.executable).absolute()),
            str((repo_root or app_root()) / "launch" / "serve.py"),
        ]
    )
    command.extend(server_args)
    if data_dir is not None:
        command.extend(("--data-dir", str(data_dir)))
    command.append("--no-browser")
    if port is not None:
        command.extend(("--port", str(port)))
    if control_path is not None:
        command.extend(("--status-control", str(control_path), "--parent-pid", str(os.getpid())))
    if headless:
        command.append("--lifetime=headless")
    return command


def backend_environment(
    environ: Mapping[str, str], *, staging_root: Path | None = None,
    data_dir: Path | None = None,
) -> dict[str, str]:
    """Preserve inherited overrides, adding a data override only when supplied."""
    environment = dict(environ)
    environment.pop(UPDATE_STAGING_ROOT_ENV, None)
    environment["WG2_NO_BROWSER"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    if data_dir is not None:
        environment["WG2_DATA_DIR"] = str(data_dir)
    if staging_root is not None:
        environment[UPDATE_STAGING_ROOT_ENV] = str(staging_root)
    return environment


def installed_launcher_argv(
    *, environ: Mapping[str, str] | None = None, platform_name: str | None = None
) -> list[str] | None:
    """Name this bundle's stable OS entry, outside its swappable app/runtime.

    Checkouts do not advertise an installed entry. Resolve the app layer rather
    than trusting an inherited launcher path that might name another bundle.
    """
    env = os.environ if environ is None else environ
    if env.get("WG2_BUNDLE") != "1":
        return None
    root = app_root(environ=env)
    if root.name != "app":
        return None
    platform = sys.platform if platform_name is None else platform_name
    if platform == "darwin":
        if root.parent.name != "Resources" or root.parent.parent.name != "Contents":
            return None
        entry = root.parent.parent / "MacOS" / "Waveguide Generator"
    elif platform == "win32":
        entry = root.parent / "Waveguide Generator.exe"
        # Only full installers replace the native entry. App-layer updates
        # cannot infer support from an exe left by an older installer. Bind
        # the installer-owned marker to a signature in the current image, so
        # a later downgrade cannot leave a stale positive advertisement.
        signature = b"WG_NATIVE_ENSURE_BACKEND_V1"
        try:
            if ((root.parent / "Waveguide Generator.ensure-backend-v1").read_bytes() != signature
                    or signature not in entry.read_bytes()):
                return None
        except OSError:
            return None
    else:
        entry = root.parent / "waveguide-generator"
        if not entry.is_file():
            entry = root.parent / "launch-wg.sh"
    return [str(entry.absolute())] if entry.is_file() else None
