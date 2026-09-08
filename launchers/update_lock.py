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
live process is left exactly as that process left it, and a start that finds the
claim held does not start.

**The relaunch grant.** That rule would make the updater's own relaunch
impossible, because the updater must keep the claim across it: if the relaunched
application does not stay running, the updater rolls the installation back, so
it is still a writer while the child is starting. Releasing first would leave
that rollback unguarded, and waiting would deadlock the updater against the
child it just started -- acquisition is non-blocking everywhere for that reason.

So the updater *authorizes* each start it performs instead. Where the layers on
the disk are the ones to open -- after the swap and the reseal on the successful
path, and over an intact installation whose transaction is decided when an
update is cancelled or rolled back -- it mints a single-use grant with
:func:`grant_relaunch`, hands the nonce to the child it starts through that
child's environment, and the child spends it with
:func:`consume_relaunch_grant`. A start holding a valid grant is the updater's
own relaunch and proceeds; a start without one, while somebody holds the claim,
is an arbitrary launch into an installation that is being written and refuses.
Agreement between the two layer manifests is **not** used for this: it is true
before the first rename and again before the reseal finishes, so it authorizes
nothing and races the writer that is about to change it.

**A grant is per relaunch, not per transaction.** An update whose new version
will not start relaunches twice -- the new version, then the restored one -- and
the child that refused has already spent the first nonce, so the second start is
minted its own. Minting clears any earlier grant for the installation, so one is
outstanding at a time whatever the path.

The grant is a handshake between this application's own processes, not a
security boundary: anybody who can write the lock directory can write a grant,
exactly as they could take the claim.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import platform
import re
import secrets
import time
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
    """One key per *physical* installation, whatever it was spelled as.

    Resolved before it is hashed, which the journal's own key is not. The CLI
    takes ``--bundle`` as a raw path, so the same installation arrives as an
    absolute path, as a relative one, and through a symlink -- three spellings
    that normalising alone maps to three different hashes, and therefore to
    three "exclusive" claims on one set of directories. Resolving links and
    relative components first is what makes the claim an installation's rather
    than a string's.

    Deliberately *not* the same function as ``apply_update.installation_key``:
    that one names a journal file and changing it would rename the records of
    every installation in flight. This one names a lock, which nothing outlives.
    """

    physical = os.path.realpath(os.fspath(resources))
    normalized = os.path.normcase(os.path.normpath(physical))
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


RELAUNCH_ENVIRONMENT_VARIABLE = "WG2_UPDATE_RELAUNCH_GRANT"
GRANT_PREFIX = "relaunch-"
GRANT_SUFFIX = ".json"
#: A grant is spent by the start it was minted for, which follows immediately.
#: The window is generous for a slow machine and far short of a session.
GRANT_LIFETIME_SECONDS = 600.0


NONCE_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def grant_path(
    resources: str | os.PathLike[str],
    nonce: str,
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """The file that *is* one grant, named by the nonce that spends it.

    The nonce is part of the name, not only of the contents, and that is what
    makes consumption safe. A start presenting no nonce or the wrong one
    addresses a path that does not exist, so it cannot read, claim or delete the
    grant somebody else was given -- which is what an earlier revision did, and
    it cost a healthy update its rollback whenever a user opened the application
    while the updater was relaunching it.

    A presented nonce is checked against :data:`NONCE_PATTERN` before it becomes
    part of a path, so a value out of the environment can only ever name a file
    in this directory.
    """

    if not NONCE_PATTERN.fullmatch(nonce):
        raise ValueError("a relaunch nonce is 32 lowercase hexadecimal digits")
    key = installation_key(resources)
    root = cache_root(system=system, environ=environ, home=home)
    return root / LOCK_DIRECTORY / f"{GRANT_PREFIX}{key}-{nonce}{GRANT_SUFFIX}"


def _grant_directory(
    resources: str | os.PathLike[str],
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> tuple[Path, str]:
    root = cache_root(system=system, environ=environ, home=home) / LOCK_DIRECTORY
    return root, f"{GRANT_PREFIX}{installation_key(resources)}-"


def grant_relaunch(
    resources: str | os.PathLike[str],
    *,
    now: float | None = None,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> str:
    """Authorize exactly one start of this installation, and return its nonce.

    Called by the updater only where the installation on the disk is the one to
    open -- after the swap and the reseal, or over an intact installation whose
    transaction has been decided -- so possession of the nonce is evidence of
    *what state* the granting process had reached, not merely that it exists.
    Returns the nonce for the caller to put in the child's environment.

    Any earlier grant for this installation is removed first, which is also what
    keeps a second relaunch in one transaction honest: the nonce the refused
    child was given stops existing when the restored version's is minted. The
    updater is the only minter and it holds the installation's claim while it
    mints, so at most one grant is outstanding and an abandoned one cannot
    accumulate.
    """

    directory, prefix = _grant_directory(
        resources, system=system, environ=environ, home=home
    )
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob(f"{prefix}*{GRANT_SUFFIX}"):
        try:
            stale.unlink()
        except OSError:
            pass
    nonce = secrets.token_hex(16)
    path = grant_path(resources, nonce, system=system, environ=environ, home=home)
    payload = {
        "schemaVersion": 1,
        "nonce": nonce,
        "installation": installation_key(resources),
        "issuedBy": os.getpid(),
        "expiresAt": (time.time() if now is None else now) + GRANT_LIFETIME_SECONDS,
    }
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)
    return nonce


def consume_relaunch_grant(
    resources: str | os.PathLike[str],
    nonce: str | None,
    *,
    now: float | None = None,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> bool:
    """Spend the grant this start was given, or report that it has none.

    **Consumption is a rename, and the rename is the exclusion.** Two starts
    holding the same valid nonce both try to move the one file out of the way;
    exactly one succeeds, because the loser's rename finds nothing there. A read
    followed by an unlink would let both read the same payload and both answer
    yes, which is not a one-shot grant.

    **A start that was given nothing consumes nothing.** No nonce, a malformed
    one, or one addressing another installation never reaches the grant file at
    all -- the path is derived from what was presented. That matters more than
    it looks: the ordinary case is a user opening the application while the
    updater is relaunching it, and an earlier revision let that launch delete
    the grant its child was about to spend, whereupon the updater rolled a
    perfectly healthy update back.

    Every failure is a refusal: absent, unreadable, expired, mismatched or
    belonging to another installation all mean the same thing here, which is
    that nothing authorized this start.
    """

    if not nonce or not NONCE_PATTERN.fullmatch(nonce):
        return False
    try:
        path = grant_path(resources, nonce, system=system, environ=environ, home=home)
    except (ValueError, OSError):
        return False
    # One atomic move decides who spent it. The name is unique to this attempt so
    # two winners cannot exist even if the loser retried.
    claimed = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.spent")
    try:
        os.rename(path, claimed)
    except OSError:
        return False
    try:
        payload = json.loads(claimed.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    finally:
        try:
            claimed.unlink()
        except OSError:
            pass
    if not isinstance(payload, dict):
        return False
    if payload.get("installation") != installation_key(resources):
        return False
    recorded = payload.get("nonce")
    if not isinstance(recorded, str) or not secrets.compare_digest(recorded, nonce):
        return False
    expires = payload.get("expiresAt")
    if not isinstance(expires, (int, float)):
        return False
    return (time.time() if now is None else now) <= float(expires)


__all__ = [
    "EXIT_UPDATE_IN_PROGRESS",
    "GRANT_LIFETIME_SECONDS",
    "LockLocationUnavailable",
    "RELAUNCH_ENVIRONMENT_VARIABLE",
    "UpdateInProgress",
    "cache_root",
    "claim_update",
    "consume_relaunch_grant",
    "grant_path",
    "grant_relaunch",
    "installation_key",
    "lock_path",
    "locking_is_available",
]
