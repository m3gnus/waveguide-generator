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
the journal, and nothing is taken from ``PATH``, from the data directory, or
from the record of the interrupted transaction: the journal decides *whether*
there is something to recover, inside the helper, and never *what to run*.

The digest recorded beside the helper is an **integrity** check and not
publisher authentication. It catches a truncated, partially written or
accidentally replaced copy -- the states an interrupted install actually
produces -- and it does not catch an attacker who can write the installation
directory, because such an attacker rewrites the manifest as well. Nothing here
claims otherwise, and it must not: on macOS the bundle seal is *invalid* during
an interrupted rename, which is precisely why recovery has to re-seal, so the
signature cannot be leaned on at this moment either. Authenticating the helper
would need a publisher key this project does not have.

**The live-updater window.** An update in progress looks exactly like an
interrupted one from outside: a layer is genuinely absent between two renames.
Waiting is not an interlock -- an updater slower than any wait is still
mid-swap -- so the shared claim in :mod:`update_lock` is what guards it, and
every path that decides a transaction takes it. This one does not hold it
across the helper it runs: the helper takes it, because a parent holding a lock
while it waits for a child that must acquire the same lock is a deadlock. The
answer comes back as an exit code, and this **fails closed** on it: an
installation whose update is owned by a live process is left alone and the user
is told to start it again in a moment. The dwell is kept in front of all of it,
because the common case is an update that finishes in well under a second and
should cost nobody a refusal.

**What counts as recovered.** The helper's exit code is the verdict. A restored
``app`` directory is not proof on its own: the helper reports failure when it
could not re-seal the bundle, and it leaves the transaction open on purpose so
the next start tries again. So both are required -- a zero exit *and* an
installation whose layers are actually there.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Mapping, Sequence

# The shared exit code, so "somebody else owns this update" survives the process
# boundary between this and the helper it runs. Imported the two ways this module
# is run -- from the app layer as a package, and from the staged copy beside its
# own dependency -- and never defaulted, because a missing claim module means a
# broken staging rather than an installation that may skip the guard.
try:  # inside the app layer, where this module is maintained
    from launchers.update_lock import EXIT_UPDATE_IN_PROGRESS
except ImportError:  # the staged copy, running as a script beside its dependency
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from update_lock import EXIT_UPDATE_IN_PROGRESS  # type: ignore[no-redef]  # noqa: E402


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
#: ``EXIT_UPDATE_IN_PROGRESS`` is imported from :mod:`update_lock` above, so the
#: helper and this module cannot disagree about which number means "somebody
#: else owns this update".


class RecoveryUnavailable(RuntimeError):
    """The recovery route itself is not usable, before anything was tried."""


def installation_is_complete(resources: Path) -> bool:
    """Both layers present, each with the manifest that says it is a layer.

    The positive check the exit code is paired with. A directory called ``app``
    proves nothing -- a partially copied or half-renamed layer is a directory
    too -- so the manifests the builder writes into each layer are what is
    looked for, and a start is only allowed when both are there.
    """

    resources = Path(resources)
    return (
        (resources / APP_LAYER / "APP-MANIFEST.json").is_file()
        and (resources / RUNTIME_LAYER / "RUNTIME-MANIFEST.json").is_file()
    )


def recovery_root(module_file: str | os.PathLike[str] | None = None) -> Path:
    return Path(module_file or __file__).resolve().parent


def resources_from_recovery(recovery: Path) -> Path:
    return recovery.parent


def resources_for_platform(bundle: Path, platform_name: str) -> Path:
    """The layer directory inside a bundle, matching ``resources_directory``."""

    bundle = Path(bundle)
    if platform_name == "darwin":
        return bundle / "Contents" / "Resources"
    return bundle


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


def wait_for_installation(
    resources: Path,
    *,
    attempts: int = DWELL_ATTEMPTS,
    delay: float = DWELL_SECONDS,
    sleep=time.sleep,
) -> bool:
    """Give an update in progress the time to put the layers back.

    Waits on the whole installation rather than on ``app`` alone. The updater
    takes one layer at a time -- rename aside, rename in -- so a kill inside the
    *runtime's* turn leaves ``app`` already replaced and no interpreter to run
    it with. A launcher that asked only about ``app`` would call that healthy
    and exec a file that is not there.
    """

    for remaining in range(max(0, attempts), 0, -1):
        if installation_is_complete(resources):
            return True
        if remaining > 1:
            sleep(delay)
    return installation_is_complete(resources)


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

    # The dwell is not the interlock, it is the courtesy in front of it: most
    # updates finish in well under a second and should cost nobody a refusal.
    if wait_for_installation(resources, attempts=attempts, delay=delay, sleep=sleep):
        return EXIT_OK

    recovery = resources / RECOVERY_DIRECTORY
    try:
        helper = verified_helper(recovery)
        bundle = bundle_from_resources(resources, selected)
        data_dir = resolve_data_dir(data_dir_override(arguments), environ=env)
    except RecoveryUnavailable as exc:
        say(f"Waveguide Generator recovery: {exc}")
        return EXIT_NO_HELPER

    command = [
        interpreter or sys.executable,
        str(helper),
        "--recover",
        "--bundle",
        str(bundle),
        "--data-dir",
        str(data_dir),
    ]
    say(
        "Waveguide Generator: this installation is missing a layer, which is "
        "what an interrupted update leaves behind. Recovering it..."
    )
    try:
        # Deliberately NOT holding the claim across this child. The helper takes
        # it -- like every other path that decides a transaction -- and a parent
        # that held it while waiting for a child which must acquire it would be
        # a deadlock. The answer comes back as an exit code instead.
        completed = runner(command, check=False)
    except OSError as exc:
        say(f"Waveguide Generator recovery: the helper could not be started: {exc}")
        return EXIT_NO_HELPER
    code = int(getattr(completed, "returncode", 1) or 0)
    if code == EXIT_UPDATE_IN_PROGRESS:
        say(
            "Waveguide Generator: an update to this installation is still "
            "running, so nothing was changed. It will finish on its own -- "
            "start the application again in a moment."
        )
        return EXIT_UPDATE_IN_PROGRESS
    # Both, not either. A restored directory is not a completed recovery: the
    # helper reports failure when it could not re-seal the bundle, and it leaves
    # the transaction open on purpose so the next start tries again.
    if code == 0 and installation_is_complete(resources):
        say("Waveguide Generator: the interrupted update was recovered.")
        return EXIT_OK
    if code == 0:
        say(
            "Waveguide Generator: recovery reported success but the installation "
            "is still incomplete, so it was not started."
        )
    else:
        say(
            f"Waveguide Generator: recovery did not finish (it exited {code}), so "
            "the application was not started. The update log in the data "
            "directory says what it was doing."
        )
    say(
        "Reinstall this version over the top; your designs and settings are in "
        "the data directory and are not touched by a reinstall."
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
    if installation_is_complete(resources):
        _start_application()
        return
    code = recover(resources=resources, arguments=arguments[1:], environ=env)
    if code != EXIT_OK:
        raise SystemExit(code)
    # Recovery put the layer back, so this start continues into it rather than
    # asking the user to open the application a second time. The import system
    # cached the failure to find the app layer while it was genuinely absent,
    # so the caches have to be dropped before the same path is tried again.
    importlib.invalidate_caches()
    _start_application()


def _start_application() -> None:
    """Hand over to the app layer's own bootstrap, which owns the start.

    Separated so the recovered path and the ordinary path are the same line of
    code; everything about *how* the application starts still lives in the
    layer that ships with it.
    """

    import wg_desktop_bootstrap  # noqa: F401  (the app layer's own bootstrap)


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
