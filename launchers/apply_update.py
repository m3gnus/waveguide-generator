#!/usr/bin/env python3
"""Apply a staged standalone-app update after the owning desktop process exits."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
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
WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS_DETACHED_PROCESS = 0x00000008

RenameCallable = Callable[[Path, Path], None]
CommandRunner = Callable[..., subprocess.CompletedProcess[Any]]
RelaunchCallable = Callable[[Sequence[str], str], None]
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


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
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
            previous = target.with_name(target.name + ".previous")
            if target.exists() or target.is_symlink():
                _remove(previous)
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
    """Remove rollback layers only after the new app has reported healthy."""

    removed: list[Path] = []
    for name in ("app.previous", "runtime.previous"):
        path = resources / name
        if path.exists() or path.is_symlink():
            _remove(path)
            removed.append(path)
            if log is not None:
                log(f"Removed healthy-start rollback layer: {path}")
    for path in sorted(resources.glob("*.previous")):
        # Whatever remains at this point is a launcher file saved by
        # refresh_launcher_files; the two layer directories are gone above.
        if path.is_file() or path.is_symlink():
            _remove(path)
            removed.append(path)
            if log is not None:
                log(f"Removed healthy-start rollback launcher file: {path}")
    return removed


def rollback_previous_layers(
    resources: Path,
    *,
    renamer: RenameCallable = _rename,
    log: LogCallable | None = None,
) -> bool:
    """Restore every available ``.previous`` layer transactionally."""

    layers = [
        (resources / name, resources / f"{name}.previous")
        for name in ("app", "runtime")
        if (resources / f"{name}.previous").is_dir()
    ]
    if not layers:
        if log is not None:
            log("No previous bundle layers were available for rollback.")
        return False

    moved_current: list[tuple[Path, Path]] = []
    restored: list[tuple[Path, Path]] = []
    try:
        for current, previous in layers:
            failed = current.with_name(current.name + ".failed")
            _remove(failed)
            renamer(current, failed)
            moved_current.append((current, failed))
            renamer(previous, current)
            restored.append((current, previous))
    except OSError as exc:
        for current, previous in reversed(restored):
            if current.exists() and not previous.exists():
                try:
                    renamer(current, previous)
                except OSError:
                    pass
        for current, failed in reversed(moved_current):
            if failed.exists() and not current.exists():
                try:
                    renamer(failed, current)
                except OSError:
                    pass
        if log is not None:
            log(f"Rollback failed: {exc}")
        return False

    for _current, failed in moved_current:
        _remove(failed)
    restored_files = 0
    for previous in sorted(resources.glob("*.previous")):
        # Launcher files saved beside the layers must go back with them, or a
        # restored runtime would keep the newer launcher that failed to start.
        if not (previous.is_file() or previous.is_symlink()):
            continue
        current = previous.with_name(previous.name[: -len(".previous")])
        try:
            _remove(current)
            renamer(previous, current)
            restored_files += 1
        except OSError as exc:
            if log is not None:
                log(f"Could not restore the previous launcher file {current}: {exc}")
    if log is not None:
        suffix = f" and {restored_files} launcher files" if restored_files else ""
        log(f"Restored the previous bundle layers{suffix} after startup failed.")
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
        command = ["open", "-n", str(bundle)]
        if arguments:
            command.extend(("--args", *arguments))
        return command
    if platform_name == "win32":
        return [str(bundle / "Waveguide Generator.exe"), *arguments]
    raise ApplyUpdateError(f"Bundle updates are unsupported on {platform_name}.")


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
    command = relaunch_command(bundle.resolve(), platform_name, relaunch_arguments)
    try:
        relauncher(command, platform_name)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"Could not relaunch Waveguide Generator: {exc}")
        return 3
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
