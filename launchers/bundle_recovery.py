"""Reach recovery when the ``app`` layer is the thing that is missing.

Automatic recovery lives inside the application, and every platform launcher
reaches the application through the ``app`` layer. So the swap's last window --
killed between ``app`` -> ``app.previous`` and ``staged`` -> ``app`` -- left no
launcher able to run it: the Linux launcher refused outright, the macOS
launcher ``chdir``-ed into a directory that was not there, and the Windows
bootstrap it needed lived in the layer that was gone.

This module is the part that does not live there. The builder stages it, and a
byte copy of ``launchers/apply_update.py``, in a ``recovery`` directory beside
``app`` and ``runtime`` -- inside the bundle, so it is covered by the same seal
and the same download, and outside the two directories an update renames, so it
survives the window it exists for. Nothing here imports from the app layer.

**What it will and will not run.** The helper is named by this module, not by
the journal, and it is verified against a digest recorded at build time before
it is executed. The interpreter is the one already running this file, which is
the bundle's own. Nothing is taken from ``PATH``, from the data directory, or
from the record of the interrupted transaction: the journal decides *whether*
there is something to recover, inside the helper, and never *what to run*.

**The live-updater window.** An update in progress looks exactly like an
interrupted one from outside: ``app`` is genuinely absent for the moment
between two renames. A launcher that recovered immediately would race the
updater that is mid-swap. So the app layer is given a bounded time to appear
before anything is decided, which is also the better behaviour for the user --
the application starts a few seconds late instead of fighting the process that
is upgrading it. A residual race remains for an update slower than the dwell;
closing it needs an interlock the journal does not currently carry, which is
recorded in the plan rather than invented here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Mapping, Sequence


APP_LAYER = "app"
RUNTIME_LAYER = "runtime"
PREVIOUS_SUFFIX = ".previous"
HELPER_NAME = "apply_update.py"
MANIFEST_NAME = "RECOVERY-MANIFEST.json"
RECOVERY_DIRECTORY = "recovery"
DATA_DIR_ENV = "WG2_DATA_DIR"
APP_DIRECTORY = "WaveguideGenerator"

#: How long to let a live updater finish before treating a missing app layer as
#: an interrupted one. Ten polls of half a second: long enough for a swap that
#: is four renames and their directory syncs, short enough that a genuinely
#: broken installation is not left staring at nothing.
DWELL_ATTEMPTS = 10
DWELL_SECONDS = 0.5

#: Distinct from anything the helper returns, so "recovery could not be
#: attempted" is never read as "recovery ran and failed".
EXIT_OK = 0
EXIT_UNRECOVERED = 1
EXIT_NO_HELPER = 3


class RecoveryUnavailable(RuntimeError):
    """The recovery route itself is not usable, before anything was tried."""


def recovery_root(module_file: str | os.PathLike[str] | None = None) -> Path:
    return Path(module_file or __file__).resolve().parent


def resources_from_recovery(recovery: Path) -> Path:
    return recovery.parent


def bundle_from_resources(resources: Path, platform_name: str) -> Path:
    """Invert ``apply_update.resources_directory`` for the two bundle shapes."""

    resolved = Path(resources).resolve()
    if platform_name == "darwin":
        if resolved.name != "Resources" or resolved.parent.name != "Contents":
            raise RecoveryUnavailable(
                f"{resolved} is not a macOS bundle Resources directory."
            )
        return resolved.parent.parent
    return resolved


def data_dir_override(arguments: Sequence[str]) -> str | None:
    """Read ``--data-dir`` out of the arguments the launcher was given.

    The same two spellings the application accepts, read here because the
    module that normally does it lives in the app layer.
    """

    values = list(arguments)
    for index, value in enumerate(values):
        if value == "--data-dir" and index + 1 < len(values):
            return values[index + 1]
        if value.startswith("--data-dir="):
            return value.split("=", 1)[1]
    return None


def resolve_data_dir(
    override: str | os.PathLike[str] | None = None,
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """The application data directory, by the same rule the application uses.

    A deliberate second implementation of ``server.platform.paths.resolve_data_dir``:
    that one is in the app layer, and this runs when the app layer is missing.
    ``test_bundle_recovery.py`` asserts the two agree, so the copy cannot drift
    without a failing test.
    """

    env = os.environ if environ is None else environ
    configured = override if override is not None else env.get(DATA_DIR_ENV)
    if configured:
        return Path(configured).expanduser().absolute()

    os_name = platform.system() if system is None else system
    home_dir = Path.home() if home is None else Path(home)

    if os_name == "Darwin":
        root = home_dir / "Library" / "Application Support"
    elif os_name == "Windows":
        appdata = env.get("APPDATA")
        if not appdata:
            raise RecoveryUnavailable(
                "APPDATA is not set, so the Windows data directory cannot be "
                "determined. Set APPDATA or WG2_DATA_DIR and start again."
            )
        root = Path(appdata)
    else:
        xdg_data_home = env.get("XDG_DATA_HOME")
        root = Path(xdg_data_home) if xdg_data_home else home_dir / ".local" / "share"

    return (root / APP_DIRECTORY).expanduser().absolute()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_helper(recovery: Path) -> Path:
    """The staged helper, or a refusal naming what is wrong with it.

    The digest is recorded beside the helper at build time and is inside the
    same seal, so this catches a truncated copy and a substituted one alike.
    Refusing is the only safe answer: the alternative is running whatever is
    at that path.
    """

    recovery = Path(recovery)
    manifest_path = recovery / MANIFEST_NAME
    helper = recovery / HELPER_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryUnavailable(
            f"The recovery manifest at {manifest_path} could not be read: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise RecoveryUnavailable(f"The recovery manifest at {manifest_path} is not an object.")
    recorded = manifest.get("helperSha256")
    if not isinstance(recorded, str) or not recorded:
        raise RecoveryUnavailable(
            f"The recovery manifest at {manifest_path} records no helper digest."
        )
    if not helper.is_file():
        raise RecoveryUnavailable(f"The recovery helper is missing: {helper}")
    observed = file_sha256(helper)
    if observed != recorded:
        raise RecoveryUnavailable(
            f"The recovery helper at {helper} does not match the digest recorded "
            f"for it ({observed} rather than {recorded}), so it was not run."
        )
    return helper


def app_layer_present(resources: Path) -> bool:
    return (Path(resources) / APP_LAYER).is_dir()


def wait_for_app_layer(
    resources: Path,
    *,
    attempts: int = DWELL_ATTEMPTS,
    delay: float = DWELL_SECONDS,
    sleep=time.sleep,
) -> bool:
    """Give an update in progress the time to put the app layer back."""

    for remaining in range(max(0, attempts), 0, -1):
        if app_layer_present(resources):
            return True
        if remaining > 1:
            sleep(delay)
    return app_layer_present(resources)


def recover(
    *,
    resources: Path,
    arguments: Sequence[str] = (),
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    runner=subprocess.run,
    interpreter: str | None = None,
    attempts: int = DWELL_ATTEMPTS,
    delay: float = DWELL_SECONDS,
    sleep=time.sleep,
    report=None,
) -> int:
    """Recover an installation whose app layer is missing, or say why not.

    Returns ``EXIT_OK`` when the app layer is there afterwards -- including
    when it was never really gone, because the updater finished during the
    dwell -- and a non-zero code otherwise.
    """

    resources = Path(resources)
    selected = sys.platform if platform_name is None else platform_name
    env = os.environ if environ is None else environ
    say = report if report is not None else (lambda message: print(message, file=sys.stderr))

    if wait_for_app_layer(resources, attempts=attempts, delay=delay, sleep=sleep):
        return EXIT_OK

    recovery = resources / RECOVERY_DIRECTORY
    try:
        helper = verified_helper(recovery)
        bundle = bundle_from_resources(resources, selected)
        data_dir = resolve_data_dir(data_dir_override(arguments), environ=env)
    except RecoveryUnavailable as exc:
        say(f"Waveguide Generator recovery: {exc}")
        return EXIT_NO_HELPER

    say(
        "Waveguide Generator: the application layer is missing, which is what an "
        "interrupted update leaves behind. Recovering it..."
    )
    command = [
        interpreter or sys.executable,
        str(helper),
        "--recover",
        "--bundle",
        str(bundle),
        "--data-dir",
        str(data_dir),
    ]
    try:
        completed = runner(command, check=False)
    except OSError as exc:
        say(f"Waveguide Generator recovery: the helper could not be started: {exc}")
        return EXIT_NO_HELPER
    code = int(getattr(completed, "returncode", 1) or 0)
    if app_layer_present(resources):
        say("Waveguide Generator: the interrupted update was recovered.")
        return EXIT_OK
    say(
        "Waveguide Generator: the interrupted update could not be recovered "
        f"automatically (recovery exited {code}). Reinstall this version over "
        "the top; your designs and settings are in the data directory and are "
        "not touched by a reinstall."
    )
    return code or EXIT_UNRECOVERED


def windows_boot(
    *,
    environ: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
    executable: str | None = None,
) -> None:
    """The Windows ``sitecustomize`` entry, which owns the whole bootstrap.

    On Windows the launcher is a renamed ``pythonw.exe`` at the bundle root and
    the only bridge into Python code is the ``import site`` line in its
    ``._pth``. That bridge used to land in the app layer, so it disappeared
    with it. It lands here instead, in the directory an update never renames,
    and delegates to the app layer's own bootstrap whenever the app layer is
    where it should be -- so the volatile half still ships with the app and
    still updates with it, and only this shim is frozen at install time.
    """

    env = os.environ if environ is None else environ
    arguments = list(sys.argv if argv is None else argv)
    program = sys.executable if executable is None else executable
    if not _is_direct_windows_launch(arguments, program):
        return
    resources = Path(program).resolve().parent
    if app_layer_present(resources):
        import wg_desktop_bootstrap  # noqa: F401  (the app layer's own bootstrap)

        return
    raise SystemExit(
        recover(resources=resources, arguments=arguments[1:], environ=env)
    )


def _is_direct_windows_launch(arguments: Sequence[str], executable: str) -> bool:
    """The double-click, told apart from every other use of this interpreter.

    The same executable is deliberately usable as ``sys.executable`` by server
    workers. CPython leaves ``sys.argv[0]`` empty when it is given no script,
    no ``-c`` and no ``-m``, which no worker invocation ever is.
    """

    return bool(arguments) and arguments[0] == "" and (
        Path(executable).name.casefold() == "waveguide generator.exe"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wg_bundle_recovery",
        description=(
            "Recover a Waveguide Generator installation whose application layer "
            "is missing after an interrupted update."
        ),
    )
    parser.add_argument(
        "--resources",
        type=Path,
        required=True,
        help="the directory holding the app and runtime layers",
    )
    parser.add_argument(
        "app_arguments",
        nargs=argparse.REMAINDER,
        help="the arguments the launcher was given, read only for --data-dir",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    return recover(resources=args.resources, arguments=args.app_arguments)


if __name__ == "__main__":
    raise SystemExit(main())
