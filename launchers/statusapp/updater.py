"""Validated handoff from the running status application to the installer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time

from server.platform.paths import resolve_data_dir


TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")


class UpdateHandoffError(RuntimeError):
    """An update request or installer handoff could not be trusted or started."""


def consume_update_request(path: Path, *, now: float | None = None) -> str | None:
    """Consume a ready, schema-valid request; retain a request whose delay is active."""

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UpdateHandoffError(f"Could not read the update request: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        try:
            path.unlink()
        except OSError:
            pass
        raise UpdateHandoffError("Discarded a malformed update request.") from exc

    if not isinstance(payload, dict):
        invalid = True
        tag = ""
        ready_at = 0.0
    else:
        tag = payload.get("tag")
        ready_at = payload.get("readyAtEpoch", 0.0)
        invalid = (
            payload.get("schemaVersion") != 1
            or payload.get("kind") != "install_release"
            or not isinstance(tag, str)
            or TAG_RE.fullmatch(tag) is None
            or not isinstance(ready_at, int | float)
        )

    if invalid:
        try:
            path.unlink()
        except OSError:
            pass
        raise UpdateHandoffError("Discarded an invalid update request.")

    if float(ready_at) > (time.time() if now is None else now):
        return None

    try:
        path.unlink()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UpdateHandoffError(f"Could not consume the update request: {exc}") from exc
    return tag


def _data_dir_override(server_args: Sequence[str]) -> str | None:
    for index, argument in enumerate(server_args):
        if argument == "--data-dir" and index + 1 < len(server_args):
            return server_args[index + 1]
        if argument.startswith("--data-dir="):
            return argument.partition("=")[2]
    return None


def _posix_helper(
    installer: Path, tag: str, parent_pid: int, platform_name: str
) -> Path:
    suffix = ".command" if platform_name == "darwin" else ".sh"
    descriptor, raw_path = tempfile.mkstemp(prefix="wg-install-update-", suffix=suffix)
    path = Path(raw_path)
    script = (
        "#!/bin/bash\n"
        "set -u\n"
        f"parent_pid={parent_pid}\n"
        "remaining=300\n"
        'while kill -0 "$parent_pid" 2>/dev/null && (( remaining > 0 )); do\n'
        "  sleep 0.2\n"
        "  remaining=$((remaining - 1))\n"
        "done\n"
        'if kill -0 "$parent_pid" 2>/dev/null; then\n'
        '  echo "Waveguide Generator did not close, so the update was cancelled." >&2\n'
        "  exit 1\n"
        "fi\n"
        # Keep deletion and exec on one parsed line so removing the running
        # helper cannot make the shell lose a later command.
        f"rm -f -- {shlex.quote(str(path))}; "
        f"exec bash {shlex.quote(str(installer))} --tag {shlex.quote(tag)}\n"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(script)
        path.chmod(0o700)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path


def _windows_helper() -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="wg-install-update-", suffix=".bat")
    path = Path(raw_path)
    script = r"""@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "WG_UPDATE_WAIT=60"
:wait_for_status
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "if (Get-Process -Id ([int]$env:WG_UPDATE_PARENT_PID) -ErrorAction SilentlyContinue) { exit 0 }; exit 1"
if errorlevel 1 goto run_installer
set /a WG_UPDATE_WAIT-=1 >nul
if "%WG_UPDATE_WAIT%"=="0" goto status_stuck
>nul 2>&1 ping 127.0.0.1 -n 2
goto wait_for_status
:status_stuck
echo.
echo Waveguide Generator did not close, so the update was cancelled.
pause
del "%~f0" >nul 2>&1 & exit /b 1
:run_installer
call "%WG_UPDATE_INSTALLER%" --tag "%WG_UPDATE_TAG%"
set "WG_UPDATE_RESULT=%ERRORLEVEL%"
if "%WG_UPDATE_RESULT%"=="0" goto finish
echo.
echo The update failed. Review the installer error and log above.
pause
:finish
del "%~f0" >nul 2>&1 & exit /b %WG_UPDATE_RESULT%
"""
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write(script)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path


def launch_update_handoff(
    repo_root: Path,
    tag: str,
    parent_pid: int,
    *,
    environ: Mapping[str, str],
    server_args: Sequence[str] = (),
    platform_name: str | None = None,
) -> None:
    """Start an independent helper that waits for this status process to exit."""

    selected_platform = sys.platform if platform_name is None else platform_name
    if TAG_RE.fullmatch(tag) is None:
        raise UpdateHandoffError("Refusing an invalid update release tag.")

    if selected_platform == "win32":
        installer = repo_root / "installers" / "windows" / "install-and-update.bat"
    elif selected_platform == "darwin":
        installer = repo_root / "installers" / "macos" / "install-wg.command"
    else:
        installer = repo_root / "installers" / "linux" / "install.sh"
    if not installer.is_file():
        raise UpdateHandoffError(f"The update installer is missing: {installer}")

    environment = dict(environ)
    data_dir_override = _data_dir_override(server_args)
    if data_dir_override is not None:
        # The installer relaunches through the normal platform launcher, which
        # does not receive the status window's original CLI arguments. Carry a
        # custom data directory through the equivalent supported environment
        # variable so the restarted app opens the same designs and job store.
        environment["WG2_DATA_DIR"] = str(
            resolve_data_dir(data_dir_override, environ=environment)
        )
    try:
        if selected_platform == "win32":
            helper = _windows_helper()
            environment.update(
                WG_UPDATE_PARENT_PID=str(parent_pid),
                WG_UPDATE_INSTALLER=str(installer),
                WG_UPDATE_TAG=tag,
            )
            command = [
                environment.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                "call",
                str(helper),
            ]
            subprocess.Popen(
                command,
                cwd=repo_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                ),
                close_fds=True,
            )
            return

        helper = _posix_helper(installer, tag, parent_pid, selected_platform)
        if selected_platform == "darwin":
            opened = subprocess.run(
                ["open", str(helper)],
                cwd=repo_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10.0,
                check=False,
            )
            if opened.returncode != 0:
                try:
                    helper.unlink()
                except OSError:
                    pass
                raise UpdateHandoffError(
                    "macOS could not open the updater in Terminal: "
                    + (opened.stderr.strip() or f"exit {opened.returncode}")
                )
            return

        data_dir = resolve_data_dir(
            data_dir_override,
            environ=environment,
        )
        logs = data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_handle = (logs / "install.log").open("a", encoding="utf-8")
        try:
            subprocess.Popen(
                [str(helper)],
                cwd=repo_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log_handle.close()
    except (OSError, subprocess.SubprocessError) as exc:
        try:
            helper.unlink()
        except (OSError, UnboundLocalError):
            pass
        raise UpdateHandoffError(f"Could not start the update installer: {exc}") from exc
