"""One exclusive claim on an installation's update, taken by everyone who moves it.

A dwell is not an interlock. An update in progress and an interrupted one look
identical from outside -- ``app`` really is absent between two renames -- so a
bootstrap recovery that only *waits* still races an updater slower than the
wait, and the two then rename the same directories from two processes.

This is the claim both sides take instead. It is an OS-level exclusive lock on
one file per installation, which matters more than the file: the kernel drops
it when the holder's descriptor closes, so a killed updater releases it by
dying rather than leaving a stale claim that blocks recovery forever -- which
is the failure a lock file with a pid inside it would introduce into exactly
the case this exists to fix.

Who takes it:

* the updater CLI, for the whole of an ``apply`` or ``rollback`` transaction,
  and for ``--recover``, which decides one;
* the in-application startup recovery in ``launchers/statusapp/updater.py``,
  which decides a transaction on every start;
* the detached rollback helper, which is the same CLI running from a copy.

Every one of them **fails closed**: an installation whose update is owned by a
live process is left exactly as that process left it.

**Nothing blocks.** Acquisition is non-blocking everywhere, which is what makes
the updater's own relaunch protocol safe: the updater installs while holding the
claim and then starts the application, and the application's startup recovery
finds the claim held, concludes it has nothing to decide, and starts. Waiting
there instead would be a deadlock the moment the updater waited for the child it
had just started. The bootstrap recovery does not hold the claim across the
helper it runs either, for the same reason -- the helper takes it, and the
bootstrap reads the answer out of the helper's exit code.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
import platform
from pathlib import Path
from typing import Iterator, Mapping


#: The claim lives beside the caches the launchers already redirect here, not
#: in the data directory and not inside the bundle. See :func:`lock_path`.
LOCK_DIRECTORY = "locks"
LOCK_PREFIX = "update-"
LOCK_SUFFIX = ".lock"
CACHE_DIRECTORY_MACOS = "WaveguideGenerator"
CACHE_DIRECTORY_WINDOWS = "WaveguideGenerator"
CACHE_DIRECTORY_XDG = "waveguide-generator"

#: The exit code every entry point uses for "somebody else owns this update".
#: Shared so the bootstrap can tell that answer apart from a recovery that ran
#: and failed, across a process boundary.
EXIT_UPDATE_IN_PROGRESS = 4

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - taken only on Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - taken only on POSIX
    msvcrt = None  # type: ignore[assignment]


class UpdateInProgress(RuntimeError):
    """Another process owns this installation's update right now."""


class LockLocationUnavailable(OSError):
    """The claim has nowhere to live, so ownership cannot be established.

    An ``OSError`` on purpose: every caller already has to treat a claim it
    could not take as a refusal, and this is that case rather than a separate
    one.
    """


def installation_key(resources: str | os.PathLike[str]) -> str:
    """The journal's own scoping key, computed without importing the updater.

    A deliberate second implementation of ``apply_update.installation_key``:
    this module is staged beside the updater and must not import it, and the
    updater imports this one. ``test_bundle_recovery`` asserts the two agree, so
    the copy cannot drift into a lock that scopes differently from the record it
    protects.
    """

    normalized = os.path.normcase(os.path.normpath(str(Path(resources))))
    return hashlib.sha256(normalized.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def cache_root(
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """The per-user directory the launchers already redirect caches into.

    Reused rather than invented: ``launcher.c``, the generated Linux launcher
    and the Windows bootstrap all point ``PYTHONPYCACHEPREFIX`` at exactly these
    roots, so this is a location the application already owns on every platform
    and a user already has.
    """

    env = os.environ if environ is None else environ
    os_name = platform.system() if system is None else system
    if os_name == "Windows":
        local = env.get("LOCALAPPDATA")
        if not local:
            raise LockLocationUnavailable(
                "LOCALAPPDATA is not set, so the update claim has nowhere to live."
            )
        return Path(local) / CACHE_DIRECTORY_WINDOWS
    home_dir = Path.home() if home is None else Path(home)
    if os_name == "Darwin":
        return home_dir / "Library" / "Caches" / CACHE_DIRECTORY_MACOS
    xdg = env.get("XDG_CACHE_HOME")
    root = Path(xdg) if xdg else home_dir / ".cache"
    return root / CACHE_DIRECTORY_XDG


def lock_path(
    resources: str | os.PathLike[str],
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """Where one *installation's* claim lives, whatever data directory is asked for.

    Deliberately **not** under the data directory. ``--data-dir`` is a caller's
    choice, so a lock rooted there is a lock one process can step around by
    naming a different directory -- and it is the same installation's layers
    both processes would then rename. Keying it on the resolved installation
    path instead means two data directories pointing at one bundle share one
    claim, and two bundles sharing one data directory keep two, which is the
    behaviour the directories themselves imply.

    Nor inside the bundle: on macOS the bundle is sealed, and writing a lock
    file into it would break the signature that recovery exists to restore.

    The limit, stated rather than papered over: the root is per user, so two
    *different* users updating one shared installation do not exclude each
    other. Closing that needs a writable system-wide location this application
    does not claim, and it is a narrower gap than the one it replaces.
    """

    key = installation_key(resources)
    root = cache_root(system=system, environ=environ, home=home)
    return root / LOCK_DIRECTORY / f"{LOCK_PREFIX}{key}{LOCK_SUFFIX}"


def _acquire(handle: int) -> bool:
    """Take the exclusive lock without blocking, or report that it is held."""

    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True
    if msvcrt is not None:  # pragma: no cover - Windows only
        try:
            msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    # No locking primitive at all. Refusing is the only safe answer: the caller
    # asked for exclusion and cannot be told it has it.
    return False  # pragma: no cover - no supported platform lacks both


def _release(handle: int) -> None:
    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass
        return
    if msvcrt is not None:  # pragma: no cover - Windows only
        try:
            msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


@contextmanager
def claim_update(
    resources: str | os.PathLike[str],
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Iterator[Path]:
    """Own this installation's update for the duration, or refuse to start.

    Raises :class:`UpdateInProgress` when another process holds it, and
    ``OSError`` when the lock file itself cannot be created -- neither of which
    may be answered by proceeding, because the caller is about to move
    directories another process may be moving.
    """

    path = lock_path(resources, system=system, environ=environ, home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if not _acquire(descriptor):
            raise UpdateInProgress(
                f"another process is updating this installation ({path})"
            )
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        except OSError:
            # The claim is the lock, not the note in the file. A pid that could
            # not be written costs a diagnostic and nothing else.
            pass
        try:
            yield path
        finally:
            _release(descriptor)
    finally:
        os.close(descriptor)


def locking_is_available() -> bool:
    """Whether this interpreter has a locking primitive at all."""

    return fcntl is not None or msvcrt is not None


__all__ = [
    "EXIT_UPDATE_IN_PROGRESS",
    "LockLocationUnavailable",
    "UpdateInProgress",
    "cache_root",
    "claim_update",
    "installation_key",
    "lock_path",
    "locking_is_available",
]
