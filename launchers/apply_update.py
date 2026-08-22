#!/usr/bin/env python3
"""Apply a staged standalone-app update after the owning desktop process exits."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

from shared.safe_names import UnsafeName, collision_key, validate_relative_name


PARENT_WAIT_SECONDS = 60.0
PARENT_POLL_SECONDS = 0.2
WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS_DETACHED_PROCESS = 0x00000008

RenameCallable = Callable[[Path, Path], None]
CommandRunner = Callable[..., subprocess.CompletedProcess[Any]]
RelaunchCallable = Callable[[Sequence[str], str], None]
LogCallable = Callable[[str], None]
ProcessProbe = Callable[[int], bool]


class ApplyUpdateError(RuntimeError):
    """The live bundle could not be swapped or restored safely."""


def append_update_log(data_dir: Path, message: str) -> None:
    """Append one updater/rollback event without affecting recovery control flow."""

    try:
        logs = data_dir.resolve() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with (logs / "update.log").open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except Exception:  # noqa: BLE001 - logging must never suppress recovery
        pass


def _emit_log(log: LogCallable | None, message: str) -> None:
    if log is None:
        return
    try:
        log(message)
    except Exception:  # noqa: BLE001 - injected/logging failures are non-fatal
        pass


def process_exists(
    pid: int,
    *,
    platform_name: str | None = None,
    windows_probe: ProcessProbe | None = None,
) -> bool:
    """Return whether ``pid`` exists without using destructive Windows ``os.kill``."""

    if pid <= 0:
        return False
    selected_platform = sys.platform if platform_name is None else platform_name
    if selected_platform == "win32":
        if windows_probe is None:
            # Keep the Win32 handle implementation in its existing single owner.
            # This import is safe in the staged app layer and remains stdlib-only.
            from server.platform.instance import pid_is_running

            windows_probe = pid_is_running
        return windows_probe(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
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


def _rename(source: Path, destination: Path) -> None:
    # ``os.replace`` maps to the replace-existing Win32 move operation while
    # retaining atomic rename semantics on POSIX. The staged updater is run by
    # the staged runtime whenever that layer changes, so no loaded DLL remains
    # inside the old runtime directory when NTFS moves it to ``.previous``.
    os.replace(source, destination)


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def previous_generation_paths(resources: Path) -> list[Path]:
    """Return layer and launcher backups that belong to one pending update."""

    paths = [
        path
        for name in ("app.previous", "runtime.previous")
        if ((path := resources / name).exists() or path.is_symlink())
    ]
    paths.extend(
        path
        for path in sorted(resources.glob("*.previous"))
        if path not in paths and (path.is_file() or path.is_symlink())
    )
    return paths


def swap_staged_layers(
    resources: Path,
    staged_app: Path,
    staged_runtime: Path | None,
    *,
    renamer: RenameCallable = _rename,
) -> None:
    """Swap complete layers into place and restore all old layers on failure."""

    layers: list[tuple[Path, Path]] = []
    if staged_runtime is not None:
        layers.append((resources / "runtime", staged_runtime.resolve()))
    # Install the app last. If the process is interrupted between complete
    # layer swaps, an old app with a newer runtime is likelier to remain usable
    # than a new app with the older runtime it explicitly replaced.
    layers.append((resources / "app", staged_app.resolve()))
    pending = previous_generation_paths(resources)
    if pending:
        raise ApplyUpdateError(
            "A previous update has not completed its healthy-start check: "
            + ", ".join(str(path) for path in pending)
        )
    for target, staged in layers:
        previous = target.with_name(target.name + ".previous")
        if not target.is_dir():
            raise ApplyUpdateError(f"The installed layer is missing: {target}")
        if not staged.is_dir():
            raise ApplyUpdateError(f"The staged layer is missing: {staged}")

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


def _validate_launcher_files(
    files: Sequence[tuple[object, object]],
    *,
    description: str,
) -> list[tuple[str, str]]:
    validated: list[tuple[str, str]] = []
    source_keys: set[str] = set()
    destination_keys: set[str] = set()
    for source_value, destination_value in files:
        try:
            source = validate_relative_name(source_value, what="launcher source")
            destination = validate_relative_name(
                destination_value,
                what="launcher destination",
            )
        except UnsafeName as exc:
            raise ApplyUpdateError(f"{description} names an unsafe launcher file: {exc}") from exc
        source_key = collision_key(source)
        destination_key = collision_key(destination)
        if source_key in source_keys:
            raise ApplyUpdateError(f"{description} repeats launcher source {source!r}.")
        if destination_key in destination_keys:
            raise ApplyUpdateError(f"{description} repeats launcher destination {destination!r}.")
        if destination_key in {"app", "runtime"} or destination_key.endswith(
            (".previous", ".failed")
        ):
            raise ApplyUpdateError(
                f"{description} names a protected launcher destination: {destination!r}."
            )
        source_keys.add(source_key)
        destination_keys.add(destination_key)
        validated.append((source, destination))
    return validated


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
    files: list[tuple[object, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ApplyUpdateError(f"The runtime manifest {manifest} has invalid launcherFiles.")
        files.append((entry.get("source"), entry.get("destination")))
    return _validate_launcher_files(files, description=f"The runtime manifest {manifest}")


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
    files = _validate_launcher_files(
        staged_launcher_files(runtime),
        description="The installed runtime manifest",
    )
    if not files:
        return []

    incoming_directory = resources / ".launcher-update"
    if incoming_directory.exists() or incoming_directory.is_symlink():
        raise ApplyUpdateError(
            f"A previous launcher refresh did not finish cleanly: {incoming_directory}"
        )
    for source, destination in files:
        origin = runtime / source
        if not origin.is_file():
            raise ApplyUpdateError(f"The installed runtime is missing {origin}.")
        previous = (resources / destination).with_name(destination + ".previous")
        if previous.exists() or previous.is_symlink():
            raise ApplyUpdateError(
                f"A previous launcher refresh has not completed its healthy-start check: {previous}"
            )

    replaced: list[tuple[Path, Path]] = []
    written: list[Path] = []
    try:
        incoming_directory.mkdir()
        for source, destination in files:
            copier(runtime / source, incoming_directory / destination)
        for _source, destination in files:
            target = resources / destination
            previous = target.with_name(target.name + ".previous")
            if target.exists() or target.is_symlink():
                renamer(target, previous)
                replaced.append((target, previous))
            renamer(incoming_directory / destination, target)
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
        try:
            _remove(incoming_directory)
        except OSError:
            pass
        raise ApplyUpdateError(f"Could not refresh the launcher files: {exc}") from exc

    try:
        _remove(incoming_directory)
    except OSError as exc:
        _emit_log(log, f"Could not remove the empty launcher staging directory: {exc}")
    _emit_log(log, f"Refreshed {len(written)} launcher files from the installed runtime.")
    return written


def cleanup_previous_layers(resources: Path, *, log: LogCallable | None = None) -> list[Path]:
    """Remove rollback layers only after the new app has reported healthy."""

    removed: list[Path] = []
    for name in ("app.previous", "runtime.previous"):
        path = resources / name
        if path.exists() or path.is_symlink():
            _remove(path)
            removed.append(path)
            _emit_log(log, f"Removed healthy-start rollback layer: {path}")
    for path in sorted(resources.glob("*.previous")):
        # Whatever remains at this point is a launcher file saved by
        # refresh_launcher_files; the two layer directories are gone above.
        if path.is_file() or path.is_symlink():
            _remove(path)
            removed.append(path)
            _emit_log(log, f"Removed healthy-start rollback launcher file: {path}")
    return removed


def rollback_previous_layers(
    resources: Path,
    *,
    renamer: RenameCallable = _rename,
    log: LogCallable | None = None,
) -> bool:
    """Restore every available ``.previous`` layer transactionally."""

    items: list[tuple[Path, Path, bool]] = [
        (resources / name, resources / f"{name}.previous", True)
        for name in ("runtime", "app")
        if (resources / f"{name}.previous").is_dir()
    ]
    items.extend(
        (
            previous.with_name(previous.name[: -len(".previous")]),
            previous,
            False,
        )
        for previous in sorted(resources.glob("*.previous"))
        if previous.is_file() or previous.is_symlink()
    )
    if not items:
        _emit_log(log, "No previous bundle layers or launcher files were available for rollback.")
        return False

    failed_paths = [
        current.with_name(current.name + ".failed") for current, _previous, _is_layer in items
    ]
    if any(path.exists() or path.is_symlink() for path in failed_paths):
        _emit_log(log, "Rollback failed because prior .failed recovery material still exists.")
        return False

    moved_current: list[tuple[Path, Path]] = []
    restored: list[tuple[Path, Path]] = []

    def reverse_partial_rollback() -> None:
        for current, previous in reversed(restored):
            try:
                if (current.exists() or current.is_symlink()) and not previous.exists():
                    renamer(current, previous)
            except OSError:
                pass
        for current, failed in reversed(moved_current):
            try:
                if (failed.exists() or failed.is_symlink()) and not current.exists():
                    renamer(failed, current)
            except OSError:
                pass

    try:
        # Preserve the entire new generation before restoring any old item.
        for current, previous, _is_layer in items:
            failed = current.with_name(current.name + ".failed")
            if current.exists() or current.is_symlink():
                renamer(current, failed)
                moved_current.append((current, failed))
        for current, previous, _is_layer in items:
            renamer(previous, current)
            restored.append((current, previous))
    except OSError as exc:
        reverse_partial_rollback()
        _emit_log(log, f"Rollback failed: {exc}")
        return False

    complete = all((resources / name).is_dir() for name in ("app", "runtime")) and all(
        current.is_dir() if is_layer else current.is_file() or current.is_symlink()
        for current, _previous, is_layer in items
    )
    if not complete:
        reverse_partial_rollback()
        _emit_log(log, "Rollback failed final-state validation.")
        return False

    cleanup_errors: list[str] = []
    # Only now is the restored installation proven runnable enough to discard
    # the failed/new generation. A failed cleanup leaves extra recovery data,
    # never a missing live executable or layer.
    for _current, failed in moved_current:
        try:
            _remove(failed)
        except OSError as exc:
            cleanup_errors.append(f"{failed}: {exc}")
    launcher_count = sum(not is_layer for _current, _previous, is_layer in items)
    suffix = f" and {launcher_count} launcher files" if launcher_count else ""
    detail = (
        " Cleanup of failed generation was incomplete: " + "; ".join(cleanup_errors)
        if cleanup_errors
        else ""
    )
    _emit_log(log, f"Restored the previous bundle layers{suffix} after startup failed.{detail}")
    return True


def _repair_macos_bundle(
    bundle: Path,
    *,
    platform_name: str,
    runner: CommandRunner,
    log: LogCallable,
) -> None:
    if platform_name != "darwin":
        return
    quarantine = ["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(bundle)]
    try:
        result = runner(quarantine, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            _emit_log(log, f"Completed: {' '.join(quarantine[:-1])}")
        else:
            detail = str(result.stderr or result.stdout or "").strip()
            _emit_log(
                log,
                f"Best-effort quarantine removal failed ({result.returncode}): {detail}",
            )
    except (OSError, subprocess.SubprocessError) as exc:
        _emit_log(log, f"Best-effort quarantine removal could not run: {exc}")

    commands = (
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(bundle)],
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
    )
    for command in commands:
        try:
            result = runner(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                detail = str(result.stderr or result.stdout or "").strip()
                raise ApplyUpdateError(
                    f"Required bundle command failed ({result.returncode}): "
                    f"{' '.join(command[:-1])}: {detail}"
                )
            _emit_log(log, f"Completed: {' '.join(command[:-1])}")
        except (OSError, subprocess.SubprocessError) as exc:
            raise ApplyUpdateError(
                f"Required bundle command could not run: {' '.join(command[:-1])}: {exc}"
            ) from exc


def repair_bundle(
    bundle: Path,
    *,
    platform_name: str = sys.platform,
    runner: CommandRunner = subprocess.run,
    log: LogCallable | None = None,
) -> None:
    """Remove quarantine and restore the ad-hoc seal after a swap or rollback."""

    _repair_macos_bundle(
        bundle.resolve(),
        platform_name=platform_name,
        runner=runner,
        log=log or (lambda _message: None),
    )


def relaunch_application(
    command: Sequence[str],
    platform_name: str,
    *,
    process_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> None:
    options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if platform_name == "win32":
        options["creationflags"] = getattr(
            subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            WINDOWS_CREATE_NEW_PROCESS_GROUP,
        ) | getattr(subprocess, "DETACHED_PROCESS", WINDOWS_DETACHED_PROCESS)
    else:
        options["start_new_session"] = True
    process_factory(list(command), **options)


def relaunch_command(bundle: Path, platform_name: str, arguments: Sequence[str] = ()) -> list[str]:
    """Relaunch the bundle with the CLI arguments the updated process was started with.

    ``open`` starts the app through LaunchServices, which passes neither the
    caller's environment nor its argv, so ``--port``/``--data-dir`` would
    otherwise be lost across an update.
    """

    if platform_name == "darwin":
        # Absolute, because the updater inherits whatever PATH the desktop had.
        command = ["/usr/bin/open", "-n", str(bundle)]
        if arguments:
            command.extend(("--args", *arguments))
        return command
    if platform_name == "win32":
        # The Windows launcher is a renamed pythonw.exe, so a bare argument
        # would be parsed as an interpreter option ("unknown option --port")
        # and the restart would fail with no console to say so. Naming the
        # module explicitly hands the arguments to the application's parser,
        # and the bundle's own bootstrap starts the desktop only for the
        # no-argument double-click, so this cannot start it twice.
        return [
            str(bundle / "Waveguide Generator.exe"),
            "-m",
            "launchers.desktop",
            *arguments,
        ]
    raise ApplyUpdateError(f"Bundle updates are unsupported on {platform_name}.")


def _show_update_failure_dialog(message: str, platform_name: str) -> None:
    """Best-effort visible failure channel for the detached updater."""

    try:
        if platform_name == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                message,
                "Waveguide Generator update",
                0x10 | 0x10000,
            )
        elif platform_name == "darwin":
            subprocess.run(
                [
                    "/usr/bin/osascript",
                    "-e",
                    "on run argv",
                    "-e",
                    'display dialog (item 1 of argv) with title "Waveguide Generator update" '
                    'buttons {"OK"} default button "OK" with icon stop',
                    "-e",
                    "end run",
                    "--",
                    message,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    except Exception:  # noqa: BLE001 - update.log remains the fallback channel
        pass


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
    logger: LogCallable | None = None,
    failure_reporter: LogCallable | None = None,
) -> int:
    """Wait, swap, repair the seal, and relaunch; return a process exit code."""

    selected_logger = logger or (lambda message: append_update_log(data_dir, message))
    selected_reporter = failure_reporter or (
        lambda message: _show_update_failure_dialog(message, platform_name)
    )

    def log(message: str) -> None:
        _emit_log(selected_logger, message)

    def report(message: str) -> None:
        _emit_log(selected_reporter, message)

    resolved_bundle = bundle.resolve()
    resources = resources_directory(resolved_bundle, platform_name)
    command = relaunch_command(resolved_bundle, platform_name, relaunch_arguments)

    def live_installation_is_complete() -> bool:
        if not all((resources / name).is_dir() for name in ("app", "runtime")):
            return False
        return platform_name != "win32" or (resources / "Waveguide Generator.exe").is_file()

    def relaunch_current() -> str | None:
        if not live_installation_is_complete():
            return "the on-disk installation is incomplete and was not relaunched"
        try:
            relauncher(command, platform_name)
        except Exception as exc:  # noqa: BLE001 - recovery must describe any launch failure
            return f"the application could not be relaunched: {type(exc).__name__}: {exc}"
        return None

    def finish_failure_after_mutation(reason: str, exit_code: int) -> int:
        # Do not emit the failure diagnostic until rollback, required signing,
        # and the attempt to reopen the restored version have all run.
        rolled_back = rollback_previous_layers(resources, renamer=renamer)
        repair_error: str | None = None
        if rolled_back:
            try:
                repair_bundle(
                    resolved_bundle,
                    platform_name=platform_name,
                    runner=runner,
                )
            except ApplyUpdateError as exc:
                repair_error = str(exc)
        relaunch_error = None if not rolled_back or repair_error else relaunch_current()
        if not rolled_back:
            outcome = (
                "Automatic rollback could not restore every required layer and launcher file. "
                "The installation was not relaunched."
            )
        elif repair_error is not None:
            outcome = (
                "The previous files were restored, but the restored macOS bundle could not be "
                f"signed and verified: {repair_error}. The installation was not relaunched."
            )
        elif relaunch_error is not None:
            outcome = f"The previous version was restored, but {relaunch_error}."
        else:
            outcome = "The previous version was restored and reopened."
        message = f"{reason}\n\n{outcome} Review update.log in the application data log directory."
        log(message)
        report(message)
        return exit_code

    if not waiter(parent_pid):
        message = (
            f"Parent process {parent_pid} did not exit; the update was cancelled before any "
            "files changed. The current process remains open."
        )
        log(message)
        report(message)
        return 1

    try:
        swap_staged_layers(
            resources,
            staged_app,
            staged_runtime,
            renamer=renamer,
        )
    except ApplyUpdateError as exc:
        relaunch_error = relaunch_current()
        outcome = (
            "The current version was reopened."
            if relaunch_error is None
            else f"The update was cancelled, but {relaunch_error}."
        )
        message = f"{exc}\n\n{outcome} Review update.log in the application data log directory."
        log(message)
        report(message)
        return 2

    if staged_runtime is not None:
        try:
            refresh_launcher_files(resources, log=log)
        except ApplyUpdateError as exc:
            return finish_failure_after_mutation(str(exc), 4)

    try:
        repair_bundle(resolved_bundle, platform_name=platform_name, runner=runner, log=log)
    except ApplyUpdateError as exc:
        return finish_failure_after_mutation(str(exc), 5)
    log("Installed and verified the staged bundle layers.")
    try:
        relauncher(command, platform_name)
    except Exception as exc:  # noqa: BLE001 - a failed launch must restore the old version
        return finish_failure_after_mutation(
            f"Could not relaunch the updated Waveguide Generator: {type(exc).__name__}: {exc}",
            3,
        )
    log(f"Relaunched Waveguide Generator: {' '.join(command)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--staged-app-dir", type=Path, required=True)
    parser.add_argument("--staged-runtime-dir", type=Path)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument(
        "--relaunch-arg",
        action="append",
        default=[],
        dest="relaunch_args",
        help="CLI argument for the relaunched application; use --relaunch-arg=--flag (repeatable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
