"""Validated handoff from the running status application to the installer."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from server.platform.paths import resolve_data_dir
from launchers import apply_update as apply_update_module
from launchers.apply_update import (
    BUNDLE_LAYERS,
    FAILED_SUFFIX,
    PREVIOUS_SUFFIX,
    WINDOWS_LAUNCHER_NAME,
    bundle_from_app_layer,
    resources_directory,
)


TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS_DETACHED_PROCESS = 0x00000008
WINDOWS_CREATE_NO_WINDOW = 0x08000000
ROLLBACK_HELPER_DIRECTORY = "rollback"
ROLLBACK_HELPER_SCRIPT = "apply_update.py"


class UpdateHandoffError(RuntimeError):
    """An update request or installer handoff could not be trusted or started."""


@dataclass(frozen=True, slots=True)
class BundleUpdateRequest:
    """Validated paths for one staged standalone-app update."""

    version: str
    staged_app_dir: Path
    staged_runtime_dir: Path | None


UpdateRequest = str | BundleUpdateRequest


def consume_update_request(
    path: Path,
    *,
    now: float | None = None,
    data_dir: Path | None = None,
) -> UpdateRequest | None:
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

    if isinstance(payload, dict) and payload.get("kind") == "apply_bundle":
        version = payload.get("version")
        raw_app = payload.get("stagedAppDir")
        raw_runtime = payload.get("stagedRuntimeDir")
        root = data_dir.resolve() if data_dir is not None else None
        try:
            staged_app = Path(raw_app).resolve() if isinstance(raw_app, str) else None
            staged_runtime = Path(raw_runtime).resolve() if isinstance(raw_runtime, str) else None
            inside = (
                root is not None
                and staged_app is not None
                and staged_app.is_relative_to(root)
                and (staged_runtime is None or staged_runtime.is_relative_to(root))
            )
        except OSError:
            inside = False
            staged_app = None
            staged_runtime = None
        invalid = (
            set(payload)
            != {
                "schemaVersion",
                "kind",
                "version",
                "stagedAppDir",
                "stagedRuntimeDir",
            }
            or payload.get("schemaVersion") != 1
            or not isinstance(version, str)
            or VERSION_RE.fullmatch(version) is None
            or raw_runtime is not None
            and not isinstance(raw_runtime, str)
            or not inside
            or staged_app is None
            or not staged_app.is_dir()
            or staged_runtime is not None
            and not staged_runtime.is_dir()
        )
        if not invalid:
            try:
                path.unlink()
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise UpdateHandoffError(f"Could not consume the update request: {exc}") from exc
            return BundleUpdateRequest(version, staged_app, staged_runtime)
        tag = ""
        ready_at = 0.0
    elif isinstance(payload, dict):
        tag = payload.get("tag")
        ready_at = payload.get("readyAtEpoch", 0.0)
        invalid = (
            payload.get("schemaVersion") != 1
            or payload.get("kind") != "install_release"
            or not isinstance(tag, str)
            or TAG_RE.fullmatch(tag) is None
            or not isinstance(ready_at, int | float)
        )
    else:
        invalid = True
        tag = ""
        ready_at = 0.0

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


# The helper interpreters run from the staged runtime under the data
# directory, where there is no pyvenv.cfg beside them, so the switch the
# bundle relies on is out of reach. Measured: a planted user-site .pth ran
# in the updater process during a real update. This is the process that
# renames the application layers, so it is the last one that should be
# executing whatever a user pip-installed years ago.
NO_USER_SITE_ENVIRONMENT = {"PYTHONNOUSERSITE": "1"}


def _data_dir_override(server_args: Sequence[str]) -> str | None:
    for index, argument in enumerate(server_args):
        if argument == "--data-dir" and index + 1 < len(server_args):
            return server_args[index + 1]
        if argument.startswith("--data-dir="):
            return argument.partition("=")[2]
    return None


def _posix_helper(installer: Path, tag: str, parent_pid: int, platform_name: str) -> Path:
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
    environment.update(NO_USER_SITE_ENVIRONMENT)
    data_dir_override = _data_dir_override(server_args)
    if data_dir_override is not None:
        # The installer relaunches through the normal platform launcher, which
        # does not receive the status window's original CLI arguments. Carry a
        # custom data directory through the equivalent supported environment
        # variable so the restarted app opens the same designs and job store.
        environment["WG2_DATA_DIR"] = str(resolve_data_dir(data_dir_override, environ=environment))
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


def launch_bundle_update_handoff(
    app_layer: Path,
    request: BundleUpdateRequest,
    parent_pid: int,
    *,
    environ: Mapping[str, str],
    server_args: Sequence[str] = (),
    platform_name: str | None = None,
) -> None:
    """Run the updater from the staged app, using its runtime when supplied."""

    selected_platform = sys.platform if platform_name is None else platform_name
    environment = dict(environ)
    environment.update(NO_USER_SITE_ENVIRONMENT)
    data_dir = resolve_data_dir(
        _data_dir_override(server_args),
        environ=environment,
    ).resolve()
    if not request.staged_app_dir.is_relative_to(data_dir) or (
        request.staged_runtime_dir is not None
        and not request.staged_runtime_dir.is_relative_to(data_dir)
    ):
        raise UpdateHandoffError("Refusing staged bundle paths outside the data directory.")
    inherited_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(request.staged_app_dir), inherited_pythonpath) if part
    )
    script = request.staged_app_dir / "launchers" / "apply_update.py"
    if not script.is_file():
        raise UpdateHandoffError(f"The staged bundle updater is missing: {script}")
    if request.staged_runtime_dir is not None:
        if selected_platform == "win32":
            python = request.staged_runtime_dir / "python.exe"
        else:
            python = request.staged_runtime_dir / "bin" / "python3.13"
    else:
        python = Path(sys.executable).resolve()
    if not python.is_file():
        raise UpdateHandoffError(f"The bundle updater Python is missing: {python}")
    try:
        bundle = bundle_from_app_layer(app_layer, selected_platform)
    except Exception as exc:  # noqa: BLE001 - translate into the handoff contract
        raise UpdateHandoffError(str(exc)) from exc
    command = [
        str(python),
        str(script),
        "--bundle",
        str(bundle),
        "--data-dir",
        str(data_dir),
        "--staged-app-dir",
        str(request.staged_app_dir),
        "--parent-pid",
        str(parent_pid),
    ]
    if request.staged_runtime_dir is not None:
        command.extend(("--staged-runtime-dir", str(request.staged_runtime_dir)))
    for argument in server_args:
        # The relaunch goes through LaunchServices/the exe, which receives no
        # environment; carry --port/--data-dir as explicit CLI arguments.
        command.append(f"--relaunch-arg={argument}")
    # Not the staged app directory.  Windows keeps an open handle on a
    # process's current directory, so an updater started inside
    # ``<data>/updates/<version>/staged/app`` cannot rename that very directory
    # into place: the swap fails with WinError 32 and rolls itself back. The
    # data directory is outside both the staged tree and the bundle, and is
    # never renamed.
    options: dict[str, object] = {
        # The updater renames both staged layers and both live layers. Its CWD
        # must be a stable absolute directory or Windows will lock that layer
        # against the very rename the updater is meant to perform.
        "cwd": str(data_dir),
        "env": environment,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    # An updater that dies before it opens its own log leaves no trace at all,
    # which is the difference between a five-minute diagnosis and a blind one.
    # Send whatever it prints to the log directory it would have written to.
    handoff_log = None
    try:
        log_directory = data_dir / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        handoff_log = (log_directory / "update-handoff.log").open("a", encoding="utf-8")
    except OSError:
        handoff_log = None
    if handoff_log is not None:
        options["stdout"] = handoff_log
        options["stderr"] = subprocess.STDOUT
    if selected_platform == "win32":
        options["creationflags"] = getattr(
            subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            WINDOWS_CREATE_NEW_PROCESS_GROUP,
        ) | getattr(subprocess, "DETACHED_PROCESS", WINDOWS_DETACHED_PROCESS)
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen(command, **options)
    except OSError as exc:
        raise UpdateHandoffError(f"Could not start the bundle updater: {exc}") from exc
    finally:
        if handoff_log is not None:
            handoff_log.close()


def rollback_renamed_directories(bundle: Path, platform_name: str) -> tuple[Path, ...]:
    """Name every directory a rollback of ``bundle`` renames."""

    resources = resources_directory(Path(bundle).resolve(), platform_name)
    return tuple(
        resources / f"{layer}{suffix}"
        for layer in BUNDLE_LAYERS
        for suffix in ("", PREVIOUS_SUFFIX, FAILED_SUFFIX)
    )


def rollback_interpreter(bundle: Path, platform_name: str) -> Path:
    """Return a Python that no rollback rename can pull out from under itself.

    On Windows the bundle root holds the renamed ``pythonw.exe`` and the
    interpreter DLLs beside it, deliberately *outside* the swappable ``runtime``
    directory so a launcher and its interpreter can be refreshed together. That
    makes it the only interpreter in the installation that does not live in a
    directory this rollback is about to rename -- the staged runtime the
    updater itself ran from no longer exists, because the swap moved it into
    place as ``runtime``.

    The bundle root is never renamed. The launcher *file* is (its ``.previous``
    has to go back), and Windows allows that while the image is mapped: an
    executable can be renamed out from under a running process, which is how
    every Windows updater replaces one. Only deletion is refused, and a
    rollback never deletes anything it is standing on.
    """

    candidate = (
        Path(bundle).resolve() / WINDOWS_LAUNCHER_NAME
        if platform_name == "win32"
        else Path(sys.executable)
    )
    if not candidate.is_file():
        raise UpdateHandoffError(f"The rollback helper interpreter is missing: {candidate}")
    resolved = candidate.resolve()
    for directory in rollback_renamed_directories(bundle, platform_name):
        if resolved == directory or resolved.is_relative_to(directory):
            raise UpdateHandoffError(
                "Refusing a rollback helper whose interpreter lives in a "
                f"directory the rollback renames: {resolved}"
            )
    return resolved


def launch_rollback_handoff(
    bundle: Path,
    data_dir: Path,
    parent_pid: int,
    *,
    environ: Mapping[str, str],
    server_args: Sequence[str] = (),
    platform_name: str | None = None,
    source_script: Path | None = None,
    process_factory: Callable[..., object] = subprocess.Popen,
) -> list[str]:
    """Start the detached helper that rolls back after ``parent_pid`` exits.

    The application that could not start cannot roll itself back on Windows:
    it holds mapped DLLs from the very ``runtime`` and ``app`` directories the
    restore has to move, and it is its own exit that releases them. So it hands
    the intent to an independent process -- exactly as an update hands off to
    the staged updater -- and leaves.

    Both halves of "does not stand on what it moves" are checked here rather
    than assumed: :func:`rollback_interpreter` refuses an interpreter inside a
    renamed directory, the script is copied out of the swappable ``app`` layer
    into the data directory first, and the working directory is the data
    directory, which is outside the bundle altogether.
    """

    selected_platform = sys.platform if platform_name is None else platform_name
    bundle = Path(bundle).resolve()
    data_dir = Path(data_dir).resolve()
    if data_dir == bundle or data_dir.is_relative_to(bundle):
        raise UpdateHandoffError(
            f"Refusing a rollback helper whose data directory is inside the bundle: {data_dir}"
        )

    origin = Path(
        apply_update_module.__file__ if source_script is None else source_script
    ).resolve()
    if not origin.is_file():
        raise UpdateHandoffError(f"The rollback helper source is missing: {origin}")
    python = rollback_interpreter(bundle, selected_platform)

    helper_root = data_dir / ROLLBACK_HELPER_DIRECTORY
    script = helper_root / ROLLBACK_HELPER_SCRIPT
    try:
        helper_root.mkdir(parents=True, exist_ok=True)
        # A copy, because the original is inside the ``app`` layer this helper
        # renames; the module is deliberately standard-library only, so the
        # copy runs anywhere.
        shutil.copyfile(origin, script)
    except OSError as exc:
        raise UpdateHandoffError(f"Could not stage the rollback helper: {exc}") from exc

    command = [
        str(python),
        str(script),
        "--rollback",
        "--bundle",
        str(bundle),
        "--data-dir",
        str(data_dir),
        "--parent-pid",
        str(parent_pid),
    ]
    for argument in server_args:
        command.append(f"--relaunch-arg={argument}")

    options: dict[str, object] = {
        "cwd": str(data_dir),
        "env": dict(environ) | NO_USER_SITE_ENVIRONMENT,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    handoff_log = None
    try:
        log_directory = data_dir / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        handoff_log = (log_directory / "rollback-handoff.log").open("a", encoding="utf-8")
    except OSError:
        handoff_log = None
    if handoff_log is not None:
        options["stdout"] = handoff_log
        options["stderr"] = subprocess.STDOUT
    if selected_platform == "win32":
        options["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", WINDOWS_CREATE_NEW_PROCESS_GROUP)
            | getattr(subprocess, "DETACHED_PROCESS", WINDOWS_DETACHED_PROCESS)
            | getattr(subprocess, "CREATE_NO_WINDOW", WINDOWS_CREATE_NO_WINDOW)
        )
    else:
        options["start_new_session"] = True
    try:
        process_factory(command, **options)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateHandoffError(f"Could not start the rollback helper: {exc}") from exc
    finally:
        if handoff_log is not None:
            handoff_log.close()
    return command
