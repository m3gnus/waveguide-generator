"""CPU package setup through injectable Julia subprocesses."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
import os
from pathlib import Path
import subprocess
import sys

from server.platform.process import background_process_kwargs

from . import paths

StatusCallback = Callable[[str], None]


def guarded_status(status_cb: StatusCallback) -> StatusCallback:
    """Pass progress through; report callback errors once without failing setup."""
    if getattr(status_cb, "_wg_beat_guarded", False):
        return status_cb
    reported = False

    def guarded(message: str) -> None:
        nonlocal reported
        try:
            status_cb(message)
        except Exception as exc:
            if not reported:
                reported = True
                with suppress(Exception):
                    sys.stderr.write(
                        f"BEAT provisioning status callback failed ({type(exc).__name__}); "
                        "continuing without that status line.\n"
                    )

    guarded._wg_beat_guarded = True  # type: ignore[attr-defined]
    return guarded


def julia_environment(
    project: Path, environment: Mapping[str, str], *, cwd: Path | None = None,
) -> tuple[Path, dict[str, str]]:
    """Resolve and isolate every project/depot destination before Julia writes."""
    cwd = Path.cwd() if cwd is None else cwd
    env = dict(environment)

    def destination(value: Path) -> Path:
        value = value.expanduser()
        resolved = (cwd / value).resolve()
        paths.checked_root(resolved, environ=env)
        paths.checked_root(resolved)
        return resolved

    project = destination(project)
    depot = env.get("JULIA_DEPOT_PATH") or str(paths.runtime_dir(environ=env) / "depot")
    entries = depot.split(os.pathsep)
    if any(not entry for entry in entries):
        # Empty entries expand to Julia-selected depots we cannot isolate here.
        raise ValueError("Empty JULIA_DEPOT_PATH entries refused; supply explicit depot paths")
    env["JULIA_DEPOT_PATH"] = os.pathsep.join(str(destination(Path(entry))) for entry in entries)
    return project, env


def run_julia_step(
    julia: str, code: str, *, project: Path, environment: Mapping[str, str],
    label: str, status_cb: StatusCallback, popen: Callable | None = None,
) -> None:
    """Stream UTF-8 progress and keep a bounded failure tail; own only this child."""
    cwd = Path.cwd()
    project, env = julia_environment(project, environment, cwd=cwd)
    report = guarded_status(status_cb)
    report(label)
    process = (popen or subprocess.Popen)(
        [julia, f"--project={project}", "--startup-file=no", "-e", code],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", env=env, cwd=cwd, **background_process_kwargs(),
    )
    tail: list[str] = []
    try:
        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip()
            if text:
                tail.append(text)
                del tail[:-15]
                report(text)
        returncode = process.wait()
        if returncode != 0:
            raise RuntimeError(f"{label} failed (exit {returncode}).\n" + "\n".join(tail[-10:]))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.stdout is not None:
            process.stdout.close()
