#!/usr/bin/env python3
"""Apply a staged standalone-app update after the owning desktop process exits."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any


PARENT_WAIT_SECONDS = 60.0
PARENT_POLL_SECONDS = 0.2
# A rollback is announced to the user with a modal dialog *before* the failed
# application exits, so the helper may have to outwait a coffee break rather
# than the milliseconds an update handoff waits for.
ROLLBACK_PARENT_WAIT_SECONDS = 900.0
# ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION: the three
# ways Windows reports "something still has this open" for a directory move.
WINDOWS_TRANSIENT_RENAME_ERRORS = frozenset({5, 32, 33})
WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WINDOWS_SYNCHRONIZE = 0x00100000
WINDOWS_ERROR_ACCESS_DENIED = 5
WINDOWS_WAIT_OBJECT_0 = 0x00000000
RENAME_RETRY_SECONDS = 20.0
RENAME_RETRY_INTERVAL = 0.25
RELAUNCH_CONFIRM_SECONDS = 6.0
RELAUNCH_CONFIRM_INTERVAL = 0.25
WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS_DETACHED_PROCESS = 0x00000008
WINDOWS_LAUNCHER_NAME = "Waveguide Generator.exe"
BUNDLE_LAYERS = ("app", "runtime")
PREVIOUS_SUFFIX = ".previous"
FAILED_SUFFIX = ".failed"
# The renamed ``pythonw.exe`` parses everything on its command line as an
# interpreter option, so ``Waveguide Generator.exe --port 3110`` dies with
# "unknown option --port" before a single line of application code runs. The
# supported arguments travel in the inherited environment instead, which keeps
# the relaunch byte-identical to the double-click the bootstrap is written for.
WINDOWS_RELAUNCH_ENVIRONMENT = {
    "--port": "WG2_PORT",
    "--data-dir": "WG2_DATA_DIR",
}

RenameCallable = Callable[[Path, Path], None]
CommandRunner = Callable[..., subprocess.CompletedProcess[Any]]
RelaunchCallable = Callable[..., Any]
LogCallable = Callable[[str], None]


class ApplyUpdateError(RuntimeError):
    """The live bundle could not be swapped or restored safely."""


def append_update_log(data_dir: Path, message: str) -> None:
    """Append one updater/rollback event to the persistent update log."""

    logs = data_dir.resolve() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with (logs / "update.log").open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def _windows_process_exists(pid: int) -> bool:
    """Ask Win32 directly, because ``os.kill`` cannot answer this on Windows.

    Two failure modes rule ``os.kill(pid, 0)`` out here, and neither is the
    one this repository used to cite -- signal 0 does not terminate anything,
    measured on 3.13.3 and 3.13.12.  The real problems: a pid that no longer
    exists raises a bare ``OSError`` rather than ``ProcessLookupError``, and,
    worse, Win32 keeps a process object resolvable for as long as *anyone*
    holds a handle to it, so a dead process reads as running whenever some
    other process still has it open.  A probe that says "alive" forever turns
    a bounded wait into a hang.

    ``GetExitCodeProcess`` would fix the first and trip over ``STILL_ACTIVE``
    being 259: a process that exited with code 259 looks like a running one.
    Waiting on the handle with a zero timeout avoids both -- a process handle
    is signalled exactly when the process has exited, whatever it exited with.
    ``WaitForSingleObject`` needs ``SYNCHRONIZE`` on the handle; without it the
    call fails and the failure is indistinguishable from "still running".
    """

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION | WINDOWS_SYNCHRONIZE, False, pid
    )
    if not handle:
        # A live process we are not allowed to open still counts as running.
        return ctypes.get_last_error() == WINDOWS_ERROR_ACCESS_DENIED
    try:
        return kernel32.WaitForSingleObject(handle, 0) != WINDOWS_WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_process_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_parent(
    parent_pid: int,
    *,
    timeout: float = PARENT_WAIT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    exists: Callable[[int], bool] = process_exists,
) -> bool:
    """Return once the desktop owner exits, bounded like the legacy handoff."""

    deadline = clock() + timeout
    while exists(parent_pid):
        if clock() >= deadline:
            return False
        sleeper(PARENT_POLL_SECONDS)
    return True


def resources_directory(bundle: Path, platform_name: str) -> Path:
    if platform_name == "darwin":
        return bundle / "Contents" / "Resources"
    return bundle


def bundle_from_app_layer(app_layer: Path, platform_name: str) -> Path:
    """Resolve the application container around the current ``app`` layer."""

    resolved = app_layer.resolve()
    if platform_name == "darwin":
        resources = resolved.parent
        if resources.name != "Resources" or resources.parent.name != "Contents":
            raise ApplyUpdateError("The bundled app layer is outside a macOS Resources directory.")
        return resources.parent.parent
    return resolved.parent


def _rename(
    source: Path,
    destination: Path,
    *,
    timeout: float = RENAME_RETRY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Move a layer into place, waiting out a Windows directory lock.

    ``os.replace`` maps to the replace-existing Win32 move operation while
    retaining atomic rename semantics on POSIX. The staged updater is run by
    the staged runtime whenever that layer changes, so no loaded DLL remains
    inside the old runtime directory when NTFS moves it to ``.previous`` --
    but the *departing* application is a different matter. The updater only
    waits for its parent pid, and the server subprocess the parent owned can
    still be releasing its mapped extension modules when the swap starts:
    Windows then answers a directory rename with ERROR_ACCESS_DENIED rather
    than a sharing violation, and a single attempt loses the race. Virus
    scanners, the search indexer and Explorer preview handlers open the same
    directories on their own schedule, so retry briefly instead of failing an
    update that would have succeeded a moment later.
    """

    deadline = clock() + timeout
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in WINDOWS_TRANSIENT_RENAME_ERRORS:
                raise
            if clock() >= deadline:
                raise
            sleeper(RENAME_RETRY_INTERVAL)


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _failed_path(target: Path, *, log: LogCallable | None = None) -> Path:
    """Return a free ``<name>.failed`` path to move ``target`` aside into.

    Windows permits renaming a file whose image is mapped into a live process
    -- that is how an installer replaces a running executable -- but it refuses
    to *delete* one. A rollback therefore never deletes anything it must move
    out of the way; it renames. The earlier ``.failed`` copy is removed when it
    can be, and simply stepped over when it cannot, because an undeletable
    leftover from a previous failure must never be the reason a restore stops.
    """

    for index in range(0, 100):
        suffix = FAILED_SUFFIX if index == 0 else f"{FAILED_SUFFIX}.{index}"
        candidate = target.with_name(target.name + suffix)
        if candidate.exists() or candidate.is_symlink():
            try:
                _remove(candidate)
            except OSError as exc:
                if log is not None:
                    log(f"Could not remove the earlier failed copy {candidate}: {exc}")
                continue
        return candidate
    raise ApplyUpdateError(f"Too many undeleted failed copies remain beside {target}.")


def swap_staged_layers(
    resources: Path,
    staged_app: Path,
    staged_runtime: Path | None,
    *,
    renamer: RenameCallable = _rename,
) -> None:
    """Swap complete layers into place and restore all old layers on failure."""

    layers = [(resources / "app", staged_app.resolve())]
    if staged_runtime is not None:
        layers.append((resources / "runtime", staged_runtime.resolve()))
    for target, staged in layers:
        previous = target.with_name(target.name + ".previous")
        if not target.is_dir():
            raise ApplyUpdateError(f"The installed layer is missing: {target}")
        if not staged.is_dir():
            raise ApplyUpdateError(f"The staged layer is missing: {staged}")
        if previous.exists():
            raise ApplyUpdateError(
                f"A previous update has not completed its healthy-start check: {previous}"
            )

    moved_old: list[tuple[Path, Path]] = []
    moved_new: list[tuple[Path, Path]] = []
    try:
        for target, staged in layers:
            previous = target.with_name(target.name + ".previous")
            renamer(target, previous)
            moved_old.append((target, previous))
            renamer(staged, target)
            moved_new.append((target, staged))
    except OSError as exc:
        rollback_errors: list[str] = []
        for target, staged in reversed(moved_new):
            try:
                if target.exists() and not staged.exists():
                    renamer(target, staged)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        for target, previous in reversed(moved_old):
            try:
                if previous.exists() and not target.exists():
                    renamer(previous, target)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        suffix = (
            " Rollback also failed: " + "; ".join(rollback_errors)
            if rollback_errors
            else " The installed layers were restored."
        )
        raise ApplyUpdateError(f"Could not swap the staged update: {exc}.{suffix}") from exc


def staged_launcher_files(runtime: Path) -> list[tuple[str, str]]:
    """Read the launcher files a runtime layer declares for its application folder.

    Only Windows runtimes carry the key: macOS keeps every executable inside the
    bundle, so there is nothing beside it to refresh.
    """

    manifest = runtime / "RUNTIME-MANIFEST.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplyUpdateError(f"Could not read the runtime manifest {manifest}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ApplyUpdateError(f"The runtime manifest {manifest} is not an object.")
    entries = payload.get("launcherFiles")
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ApplyUpdateError(f"The runtime manifest {manifest} has invalid launcherFiles.")
    files: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ApplyUpdateError(f"The runtime manifest {manifest} has invalid launcherFiles.")
        source = entry.get("source")
        destination = entry.get("destination")
        for name in (source, destination):
            # A manifest travels with a downloaded archive, so treat these as
            # untrusted input: only plain file names may be copied, and never
            # into a parent or absolute location.
            if (
                not isinstance(name, str)
                or not name
                or name in {".", ".."}
                or Path(name).name != name
            ):
                raise ApplyUpdateError(
                    f"The runtime manifest {manifest} names an unsafe launcher file: {name!r}"
                )
        assert isinstance(source, str) and isinstance(destination, str)
        files.append((source, destination))
    return files


def refresh_launcher_files(
    resources: Path,
    *,
    copier: Callable[[Path, Path], object] = shutil.copy2,
    renamer: RenameCallable = _rename,
    log: LogCallable | None = None,
) -> list[Path]:
    """Replace the launcher files beside a swapped-in runtime, reversibly.

    The old files move aside as ``<name>.previous`` so a failed start restores a
    matched launcher and runtime together; a healthy start removes them with the
    layer directories.
    """

    runtime = resources / "runtime"
    files = staged_launcher_files(runtime)
    if not files:
        return []

    replaced: list[tuple[Path, Path]] = []
    written: list[Path] = []
    try:
        for source, destination in files:
            origin = runtime / source
            if not origin.is_file():
                raise ApplyUpdateError(f"The installed runtime is missing {origin}.")
            target = resources / destination
            previous = target.with_name(target.name + PREVIOUS_SUFFIX)
            if target.exists() or target.is_symlink():
                if previous.exists() or previous.is_symlink():
                    # A leftover from an earlier cycle whose deletion Windows
                    # refused. Step over it rather than fail an update on it.
                    try:
                        _remove(previous)
                    except OSError:
                        renamer(previous, _failed_path(previous, log=log))
                renamer(target, previous)
                replaced.append((target, previous))
            copier(origin, target)
            written.append(target)
    except (ApplyUpdateError, OSError) as exc:
        for target in reversed(written):
            try:
                _remove(target)
            except OSError:
                pass
        for target, previous in reversed(replaced):
            try:
                if previous.exists() and not target.exists():
                    renamer(previous, target)
            except OSError:
                pass
        raise ApplyUpdateError(f"Could not refresh the launcher files: {exc}") from exc

    if log is not None:
        log(f"Refreshed {len(written)} launcher files from the installed runtime.")
    return written


def cleanup_previous_layers(resources: Path, *, log: LogCallable | None = None) -> list[Path]:
    """Remove rollback and failed-update leftovers once a start reports healthy.

    This is where the deferred deletions land. A rollback renames the version
    that would not start to ``.failed`` and leaves the removal to whoever can
    actually perform it: by the time a restored version reports a healthy
    frontend, nothing anywhere maps a DLL out of those directories any more.
    Every removal is best effort -- a leftover directory is clutter, never a
    reason to fail a start that has already succeeded.
    """

    removed: list[Path] = []

    def discard(path: Path, description: str) -> None:
        try:
            _remove(path)
        except OSError as exc:
            if log is not None:
                log(f"Could not remove the {description} {path}: {exc}")
            return
        removed.append(path)
        if log is not None:
            log(f"Removed {description}: {path}")

    for name in BUNDLE_LAYERS:
        path = resources / f"{name}{PREVIOUS_SUFFIX}"
        if path.exists() or path.is_symlink():
            discard(path, "healthy-start rollback layer")
    for path in sorted(resources.glob(f"*{PREVIOUS_SUFFIX}")):
        # Whatever remains at this point is a launcher file saved by
        # refresh_launcher_files; the two layer directories are gone above.
        if path.is_file() or path.is_symlink():
            discard(path, "healthy-start rollback launcher file")
    for path in sorted(resources.glob(f"*{FAILED_SUFFIX}*")):
        discard(path, "rolled-back failed update copy")
    return removed


def rollback_previous_layers(
    resources: Path,
    *,
    renamer: RenameCallable = _rename,
    log: LogCallable | None = None,
) -> bool:
    """Restore every available ``.previous`` layer and launcher file.

    Deleting is deliberately not part of the critical path. Windows refuses to
    delete a file whose image is still mapped into a live process -- the
    observed failure was ``[WinError 5] Access is denied:
    '...\\runtime.failed\\DLLs\\libcrypto-3-x64.dll'`` raised by ``rmtree`` --
    and because that raised, this function never reached the launcher files
    below. The installation was left with a 0.2.5 app and runtime under a 0.2.6
    ``vcruntime140.dll``, which is exactly the mismatch the launcher-refresh
    mechanism exists to prevent. Renaming such a file *is* permitted, so every
    displaced item is renamed to ``.failed`` and only afterwards removed, best
    effort, with any failure logged instead of raised.
    """

    def report(message: str) -> None:
        if log is not None:
            log(message)

    layers = [
        (resources / name, resources / f"{name}{PREVIOUS_SUFFIX}")
        for name in BUNDLE_LAYERS
        if (resources / f"{name}{PREVIOUS_SUFFIX}").is_dir()
    ]
    if not layers:
        report("No previous bundle layers were available for rollback.")
        return False

    moved_current: list[tuple[Path, Path]] = []
    restored: list[tuple[Path, Path]] = []
    displaced: list[Path] = []
    try:
        for current, previous in layers:
            failed = _failed_path(current, log=log)
            renamer(current, failed)
            moved_current.append((current, failed))
            displaced.append(failed)
            report(f"Moved the failed layer aside: {current} -> {failed}")
            renamer(previous, current)
            restored.append((current, previous))
            report(f"Restored the previous layer: {previous} -> {current}")
    except (OSError, ApplyUpdateError) as exc:
        report(f"Rollback could not restore the bundle layers: {exc}")
        for current, previous in reversed(restored):
            if current.exists() and not previous.exists():
                try:
                    renamer(current, previous)
                except OSError as undo_exc:
                    report(f"Could not undo the partial restore of {current}: {undo_exc}")
        for current, failed in reversed(moved_current):
            if failed.exists() and not current.exists():
                try:
                    renamer(failed, current)
                except OSError as undo_exc:
                    report(f"Could not put {failed} back as {current}: {undo_exc}")
        report(
            "The bundle layers were left as the rollback found them; "
            "review the entries above before changing the installation."
        )
        return False

    # Launcher files saved beside the layers must go back with them, or a
    # restored runtime would keep the newer launcher that failed to start. One
    # file that cannot be restored must not stop the other five: a partially
    # refreshed launcher set is the very failure this loop repairs.
    restored_files = 0
    for previous in sorted(resources.glob(f"*{PREVIOUS_SUFFIX}")):
        if not (previous.is_file() or previous.is_symlink()):
            continue
        current = previous.with_name(previous.name[: -len(PREVIOUS_SUFFIX)])
        moved_aside: Path | None = None
        try:
            if current.exists() or current.is_symlink():
                moved_aside = _failed_path(current, log=log)
                renamer(current, moved_aside)
                displaced.append(moved_aside)
            renamer(previous, current)
        except (OSError, ApplyUpdateError) as exc:
            report(f"Could not restore the previous launcher file {current}: {exc}")
            # Leaving no file at all is worse than leaving the new one, so put
            # back whatever this file's restore had already moved.
            if moved_aside is not None and moved_aside.exists() and not current.exists():
                displaced.remove(moved_aside)
                try:
                    renamer(moved_aside, current)
                except OSError as undo_exc:
                    report(f"Could not put {moved_aside} back as {current}: {undo_exc}")
            continue
        restored_files += 1
        report(f"Restored the previous launcher file: {current}")

    suffix = f" and {restored_files} launcher files" if restored_files else ""
    report(f"Restored the previous bundle layers{suffix} after startup failed.")

    for path in displaced:
        try:
            _remove(path)
        except OSError as exc:
            # Expected on Windows whenever a DLL from the failed version is
            # still mapped. cleanup_previous_layers sweeps it up on the next
            # healthy start, when nothing holds it any more.
            report(f"Deferred removal of {path} to the next healthy start: {exc}")
    return True


def _best_effort_bundle_repairs(
    bundle: Path,
    *,
    platform_name: str,
    runner: CommandRunner,
    log: LogCallable,
) -> None:
    if platform_name != "darwin":
        return
    commands = (
        ["xattr", "-dr", "com.apple.quarantine", str(bundle)],
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(bundle)],
    )
    for command in commands:
        try:
            result = runner(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                log(f"Completed: {' '.join(command[:-1])}")
            else:
                detail = str(result.stderr or result.stdout or "").strip()
                log(
                    f"Best-effort command failed ({result.returncode}): "
                    f"{' '.join(command[:-1])}: {detail}"
                )
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"Best-effort command could not run: {' '.join(command[:-1])}: {exc}")


def repair_bundle(
    bundle: Path,
    *,
    platform_name: str = sys.platform,
    runner: CommandRunner = subprocess.run,
    log: LogCallable | None = None,
) -> None:
    """Remove quarantine and restore the ad-hoc seal after a swap or rollback."""

    _best_effort_bundle_repairs(
        bundle.resolve(),
        platform_name=platform_name,
        runner=runner,
        log=log or (lambda _message: None),
    )


def relaunch_application(
    command: Sequence[str],
    platform_name: str,
    *,
    environment: Mapping[str, str] | None = None,
    process_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> Any:
    """Start the relaunch detached and return whatever the factory produced."""

    options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if environment is not None:
        options["env"] = dict(environment)
    if platform_name == "win32":
        # DETACHED_PROCESS already denies the child a console, and the launcher
        # is a subsystem-2 executable, so nothing flashes on screen either way.
        options["creationflags"] = getattr(
            subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            WINDOWS_CREATE_NEW_PROCESS_GROUP,
        ) | getattr(subprocess, "DETACHED_PROCESS", WINDOWS_DETACHED_PROCESS)
    else:
        options["start_new_session"] = True
    return process_factory(list(command), **options)


def confirm_relaunch(
    process: Any,
    *,
    timeout: float = RELAUNCH_CONFIRM_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> str | None:
    """Return a description of an immediate relaunch failure, or ``None``.

    ``Popen`` returning is not evidence that the application started: a command
    line the interpreter rejects -- ``unknown option --port`` -- produces a
    healthy-looking ``Popen`` and a process that is gone milliseconds later.
    Watch the child for a moment so the updater cannot log success over that.
    """

    poll = getattr(process, "poll", None)
    if not callable(poll):
        return None
    deadline = clock() + timeout
    while True:
        code = poll()
        if code is not None:
            return f"exited with code {code} within {timeout:.0f} seconds of starting"
        if clock() >= deadline:
            return None
        sleeper(RELAUNCH_CONFIRM_INTERVAL)


def _relaunch_environment_value(flag: str, value: str, data_dir: Path | None) -> str | None:
    if flag == "--port":
        try:
            return str(int(value))
        except ValueError:
            return None
    if flag == "--data-dir":
        # The updater was handed the already-resolved data directory the
        # departing process was using, which is exactly what the override
        # meant; re-deriving it here would need the server package this
        # deliberately stdlib-only script cannot import.
        if data_dir is not None:
            return str(data_dir)
        return str(Path(value).expanduser())
    return None


def relaunch_environment(
    arguments: Sequence[str],
    platform_name: str,
    *,
    data_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[dict[str, str] | None, list[str]]:
    """Translate relaunch arguments Windows cannot pass on into the environment.

    Returns the environment to start the child with (``None`` means "inherit
    unchanged") and the arguments that could not be carried, so the caller can
    say so in the log rather than losing them silently.
    """

    if platform_name != "win32":
        return None, []

    overrides: dict[str, str] = {}
    dropped: list[str] = []
    pending: str | None = None
    for argument in arguments:
        if pending is not None:
            value = _relaunch_environment_value(pending, argument, data_dir)
            if value is None:
                dropped.extend((pending, argument))
            else:
                overrides[WINDOWS_RELAUNCH_ENVIRONMENT[pending]] = value
            pending = None
            continue
        flag, separator, inline = argument.partition("=")
        if flag not in WINDOWS_RELAUNCH_ENVIRONMENT:
            dropped.append(argument)
            continue
        if not separator:
            pending = flag
            continue
        value = _relaunch_environment_value(flag, inline, data_dir)
        if value is None:
            dropped.append(argument)
        else:
            overrides[WINDOWS_RELAUNCH_ENVIRONMENT[flag]] = value
    if pending is not None:
        dropped.append(pending)

    if not overrides:
        return None, dropped
    environment = dict(os.environ if environ is None else environ)
    environment.update(overrides)
    return environment, dropped


def relaunch_command(bundle: Path, platform_name: str, arguments: Sequence[str] = ()) -> list[str]:
    """Name the command that reopens the application after a swap or rollback.

    ``open`` starts the app through LaunchServices, which passes neither the
    caller's environment nor its argv, so on macOS ``--port``/``--data-dir``
    travel as explicit ``--args``.

    Windows is the mirror image. ``Waveguide Generator.exe`` is a renamed
    ``pythonw.exe``: CPython parses its whole command line as interpreter
    options and exits with ``unknown option --port`` before ``sitecustomize``
    -- and therefore the bundle bootstrap -- ever runs. Worse, the bootstrap
    identifies a double-click by an empty ``sys.argv[0]``, so *any* argument
    would also turn the launch into "a server worker is using me as
    sys.executable" and skip ``WG2_BUNDLE``, ``WG2_APP_ROOT`` and the cache
    redirection. The launcher is therefore started exactly as Explorer starts
    it, with no argv at all, and :func:`relaunch_environment` carries the
    arguments the environment can express.
    """

    if platform_name == "darwin":
        command = ["open", "-n", str(bundle)]
        if arguments:
            command.extend(("--args", *arguments))
        return command
    if platform_name == "win32":
        return [str(bundle / WINDOWS_LAUNCHER_NAME)]
    raise ApplyUpdateError(f"Bundle updates are unsupported on {platform_name}.")


def _relaunch(
    *,
    bundle: Path,
    data_dir: Path,
    platform_name: str,
    arguments: Sequence[str],
    relauncher: RelaunchCallable,
    confirm: Callable[[Any], str | None],
    environ: Mapping[str, str] | None,
    log: LogCallable,
) -> int:
    """Reopen the application and prove it survived; return an exit code."""

    command = relaunch_command(bundle, platform_name, arguments)
    environment, dropped = relaunch_environment(
        arguments,
        platform_name,
        data_dir=data_dir,
        environ=environ,
    )
    if dropped:
        log(
            "The relaunch could not carry these arguments and started without "
            f"them: {' '.join(dropped)}"
        )
    try:
        process = relauncher(command, platform_name, environment=environment)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"Could not relaunch Waveguide Generator: {exc}")
        return 3
    log(f"Relaunched Waveguide Generator: {' '.join(command)}")
    failure = confirm(process)
    if failure is not None:
        log(
            f"The relaunched Waveguide Generator {failure}; it did not stay "
            "running. Reopen it from the Start menu and review the entries "
            "above."
        )
        return 5
    return 0


def apply_update(
    *,
    bundle: Path,
    data_dir: Path,
    staged_app: Path,
    staged_runtime: Path | None,
    parent_pid: int,
    relaunch_arguments: Sequence[str] = (),
    platform_name: str = sys.platform,
    renamer: RenameCallable = _rename,
    runner: CommandRunner = subprocess.run,
    relauncher: RelaunchCallable = relaunch_application,
    waiter: Callable[[int], bool] = wait_for_parent,
    confirm: Callable[[Any], str | None] = confirm_relaunch,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Wait, swap, repair the seal, and relaunch; return a process exit code."""

    def log(message: str) -> None:
        append_update_log(data_dir, message)

    if not waiter(parent_pid):
        log(f"Parent process {parent_pid} did not exit; update cancelled.")
        return 1

    resources = resources_directory(bundle.resolve(), platform_name)
    try:
        swap_staged_layers(
            resources,
            staged_app,
            staged_runtime,
            renamer=renamer,
        )
    except ApplyUpdateError as exc:
        log(str(exc))
        return 2

    if staged_runtime is not None:
        try:
            refresh_launcher_files(resources, log=log)
        except ApplyUpdateError as exc:
            log(str(exc))
            rollback_previous_layers(resources, renamer=renamer, log=log)
            return 4

    log("Installed the staged bundle layers.")
    repair_bundle(bundle.resolve(), platform_name=platform_name, runner=runner, log=log)
    return _relaunch(
        bundle=bundle.resolve(),
        data_dir=data_dir,
        platform_name=platform_name,
        arguments=relaunch_arguments,
        relauncher=relauncher,
        confirm=confirm,
        environ=environ,
        log=log,
    )


def _wait_for_failed_application(parent_pid: int) -> bool:
    return wait_for_parent(parent_pid, timeout=ROLLBACK_PARENT_WAIT_SECONDS)


def rollback_bundle(
    *,
    bundle: Path,
    data_dir: Path,
    parent_pid: int,
    relaunch_arguments: Sequence[str] = (),
    platform_name: str = sys.platform,
    renamer: RenameCallable = _rename,
    runner: CommandRunner = subprocess.run,
    relauncher: RelaunchCallable = relaunch_application,
    waiter: Callable[[int], bool] = _wait_for_failed_application,
    confirm: Callable[[Any], str | None] = confirm_relaunch,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Restore the previous version once the failed application has exited.

    This runs in the dedicated rollback helper, never in the application that
    could not start: that process has DLLs mapped out of both the ``runtime``
    it must rename and the ``app`` beside it, and it is the process whose exit
    releases them. Waiting for it here is what turns "Windows will not let me
    delete this" into "there is nothing left holding it".
    """

    def log(message: str) -> None:
        append_update_log(data_dir, message)

    log(
        f"Rollback helper {os.getpid()} started for {bundle}; waiting for the "
        f"application that failed to start (pid {parent_pid}) to exit."
    )
    if not waiter(parent_pid):
        log(
            f"The application that failed to start (pid {parent_pid}) is still "
            "running, so the rollback was cancelled and nothing was changed. "
            "Close Waveguide Generator and reopen it to try again."
        )
        return 1

    resources = resources_directory(bundle.resolve(), platform_name)
    if not rollback_previous_layers(resources, renamer=renamer, log=log):
        log(
            "The rollback did not restore the previous version, so the "
            "application was not reopened. Review the entries above before "
            "changing the installation."
        )
        return 2

    repair_bundle(bundle.resolve(), platform_name=platform_name, runner=runner, log=log)
    return _relaunch(
        bundle=bundle.resolve(),
        data_dir=data_dir,
        platform_name=platform_name,
        arguments=relaunch_arguments,
        relauncher=relauncher,
        confirm=confirm,
        environ=environ,
        log=log,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--staged-app-dir", type=Path)
    parser.add_argument("--staged-runtime-dir", type=Path)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="restore the .previous layers of a version that would not start",
    )
    parser.add_argument(
        "--relaunch-arg",
        action="append",
        default=[],
        dest="relaunch_args",
        help="CLI argument for the relaunched application; use --relaunch-arg=--flag (repeatable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.rollback:
        if args.staged_app_dir is not None or args.staged_runtime_dir is not None:
            parser.error("--rollback restores installed layers and takes no staged directories")
        return rollback_bundle(
            bundle=args.bundle,
            data_dir=args.data_dir,
            parent_pid=args.parent_pid,
            relaunch_arguments=args.relaunch_args,
        )
    if args.staged_app_dir is None:
        parser.error("--staged-app-dir is required unless --rollback is given")
    return apply_update(
        bundle=args.bundle,
        data_dir=args.data_dir,
        staged_app=args.staged_app_dir,
        staged_runtime=args.staged_runtime_dir,
        parent_pid=args.parent_pid,
        relaunch_arguments=args.relaunch_args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
