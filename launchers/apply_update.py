#!/usr/bin/env python3
"""Apply a staged standalone-app update after the owning desktop process exits."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
import uuid

try:  # POSIX only; the Windows branch of _fsync_descriptor never needs it.
    import fcntl
except ImportError:  # pragma: no cover - taken only on Windows
    fcntl = None  # type: ignore[assignment]


# The shared claim. Imported the two ways this module is ever run -- from the
# app layer as a package, and from a staged copy as a script beside its own
# dependency -- and NOT defaulted to a no-op when neither works. A missing
# module in a staged copy is a staging bug, not evidence of an old
# installation, and answering it by silently dropping the exclusion would
# remove the guard exactly where the detached helper needs it most.
try:  # inside the app layer, where this module is maintained
    from launchers.update_lock import (
        EXIT_UPDATE_IN_PROGRESS,
        UpdateInProgress,
        claim_update as _claim_update,
    )
except ImportError:  # a staged copy, running as a script beside its dependency
    # The directory is added explicitly rather than relied upon. The documented
    # manual command and the detached handoff both run this file with a plain
    # interpreter, which would put it on the path anyway -- but ``-I`` does not,
    # and an isolated run is exactly how somebody repairs a broken installation.
    # The sibling is no less trusted than this file: it is this file's own
    # directory, and this file is the program that was chosen to run.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from update_lock import (  # type: ignore[no-redef]  # noqa: E402
        EXIT_UPDATE_IN_PROGRESS,
        UpdateInProgress,
        claim_update as _claim_update,
    )


class ApplyUpdateError(RuntimeError):
    """The live bundle could not be swapped or restored safely."""


# The Windows runtime interpreter is isolated by ``python._pth`` and does not
# add a directly executed script's parent app layer to ``sys.path``. Make the
# staged app importable before it is renamed so this updater can use the same
# strict name validator as the rest of the release pipeline. The copied
# rollback helper instead finds the installed app through the bundle launcher's
# own ``._pth``; it performs no lazy application imports after renaming begins.
_SCRIPT_APP_ROOT = Path(__file__).resolve().parents[1]
if (
    _SCRIPT_APP_ROOT.name == "app"
    and (_SCRIPT_APP_ROOT / "shared").is_dir()
    and str(_SCRIPT_APP_ROOT) not in sys.path
):
    sys.path.insert(0, str(_SCRIPT_APP_ROOT))

#: Why the strict shared-name validator is unavailable, or ``None``.
#:
#: Installing launcher files needs it and refuses without it. *Recovering* does
#: not: rollback and reconciliation move only directories and files the bundle
#: already owns, under names this module spells itself. Keeping the failure in a
#: variable rather than at the import statement is what lets the copied external
#: recovery helper run at all when the missing ``app`` layer is the very thing
#: it was started to restore -- that helper died on this import before, so the
#: one situation it exists for was the one situation it could not handle. The
#: import still happens here, eagerly, before any rename: nothing below it is a
#: lazy application import performed after the layers start moving.
NAME_VALIDATOR_ERROR: str | None = None
try:
    from shared.safe_names import (  # noqa: E402 - isolated staged runtime path bootstrap
        UnsafeName,
        collision_key,
        validate_relative_name,
    )
except Exception as _validator_error:  # noqa: BLE001 - any import failure is recoverable
    NAME_VALIDATOR_ERROR = f"{type(_validator_error).__name__}: {_validator_error}"

    class UnsafeName(ValueError):  # type: ignore[no-redef]
        """Stand-in so the except clauses below stay valid without the app layer."""

    def _validator_unavailable(*_args: object, **_kwargs: object) -> str:
        raise ApplyUpdateError(
            "The strict launcher-name validator could not be imported "
            f"({NAME_VALIDATOR_ERROR}); refusing to install launcher files."
        )

    collision_key = _validator_unavailable  # type: ignore[assignment]
    validate_relative_name = _validator_unavailable  # type: ignore[assignment]


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
WINDOWS_STILL_ACTIVE = 259
WINDOWS_WAIT_OBJECT_0 = 0x00000000
WINDOWS_WAIT_TIMEOUT = 0x00000102
RENAME_RETRY_SECONDS = 20.0
RENAME_RETRY_INTERVAL = 0.25
RELAUNCH_CONFIRM_SECONDS = 6.0
RELAUNCH_CONFIRM_INTERVAL = 0.25
WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS_DETACHED_PROCESS = 0x00000008
WINDOWS_LAUNCHER_NAME = "Waveguide Generator.exe"
#: The Linux bundle's entry point, at the root of the installation beside
#: ``app`` and ``runtime`` -- the same position the Windows launcher holds,
#: which is why ``bundle_from_app_layer`` resolves both with one rule.
LINUX_LAUNCHER_NAME = "waveguide-generator"
BUNDLE_LAYERS = ("app", "runtime")
#: Where the standalone recovery helper is kept, outside the bundle. The
#: rollback handoff already copies this module there when it hands off; a
#: transaction stages it up front as well, so the copy exists for the crash
#: that never reaches a handoff. ``launchers/statusapp/updater.py`` imports
#: both names rather than spelling them again, so the two cannot drift apart.
RECOVERY_HELPER_DIRECTORY = "rollback"
RECOVERY_HELPER_NAME = "apply_update.py"
#: Staged beside the helper because the helper imports it and refuses to run
#: without it. A copy of this module alone is not a runnable helper.
RECOVERY_LOCK_NAME = "update_lock.py"
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


# --------------------------------------------------------------------------
# Durability primitives
# --------------------------------------------------------------------------

#: Whether this platform lets Python flush a *directory*, which is what makes a
#: rename survive a power cut rather than merely a crash. POSIX can; Windows has
#: no directory handle the standard library will open, so there it is False and
#: the design below never depends on it. See ``sync_directory``.
DIRECTORY_SYNC_SUPPORTED = os.name == "posix"

#: ``fcntl.F_FULLFSYNC``. Named here because the constant is absent on Linux.
MACOS_FULL_FSYNC = 51


#: Errno values that mean "this file system does not implement F_FULLFSYNC",
#: as opposed to "this flush failed". Only the first list may be answered by
#: falling back to the weaker ``fsync``; a genuine write error must not be
#: turned into a quieter flush that then reports success.
UNSUPPORTED_FLUSH_ERRNOS = frozenset(
    number
    for number in (
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "EINVAL", None),
        getattr(errno, "ENOTTY", None),
        getattr(errno, "ENOSYS", None),
    )
    if number is not None
)


def _fsync_descriptor(fd: int, *, log: LogCallable | None = None) -> bool:
    """Flush one descriptor as hard as the platform can be asked to flush it.

    Returns whether the *strongest* available flush was used, so a caller can
    record a weakened guarantee instead of implying one it did not get.

    On macOS ``fsync`` returns once the data reaches the drive, not once the
    drive has committed it, so a power cut can still lose a write that fsync
    reported as done. ``F_FULLFSYNC`` is the call that asks for the media
    flush. Several file systems do not implement it, and that is not a reason
    to fail an update -- but "not implemented" and "the write failed" are
    different answers arriving through the same exception type, and only the
    first may be answered by quietly doing something weaker. Anything else
    propagates.
    """

    if fcntl is not None and sys.platform == "darwin":
        try:
            fcntl.fcntl(fd, getattr(fcntl, "F_FULLFSYNC", MACOS_FULL_FSYNC))
            return True
        except OSError as exc:
            if exc.errno not in UNSUPPORTED_FLUSH_ERRNOS:
                raise
            _emit_log(
                log,
                "This file system does not implement F_FULLFSYNC "
                f"({errno.errorcode.get(exc.errno, exc.errno)}); falling back to fsync, "
                "which returns before the drive has committed the write.",
            )
            os.fsync(fd)
            return False
    os.fsync(fd)
    # Off macOS, fsync is the strongest flush the standard library offers, and
    # on Linux it is a media flush; there is nothing weaker being substituted.
    return sys.platform != "darwin"


def sync_directory(path: Path, *, log: LogCallable | None = None) -> bool:
    """Persist a directory's own entries, so a rename that returned is on disk.

    Returns whether the flush was actually performed. Windows always returns
    False: opening a directory needs ``FILE_FLAG_BACKUP_SEMANTICS``, which
    ``os.open`` does not offer, and ``FlushFileBuffers`` is not documented to do
    anything useful for a directory handle even if one is obtained. POSIX
    returns False when the flush itself failed. Both are real, both are
    reported to the caller, and neither is papered over.

    **What this does and does not buy.** Flushing a directory is how a *rename*
    -- including the rename that publishes the journal -- is made durable; a
    file flush covers the bytes, never the name. So a False here means the
    journal's own publication is not proven durable, and the recovery below is
    written to be correct anyway: every state a lost publication can leave
    behind (no journal, the previous journal, or a complete temporary one) is
    handled conservatively, and none of them can make an unfinished
    installation look finished. That is the property this design rests on --
    not a claim that the flush always succeeds.
    """

    if not DIRECTORY_SYNC_SUPPORTED:
        return False
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        _emit_log(log, f"Could not open {path} to flush its directory entries: {exc}")
        return False
    try:
        _fsync_descriptor(fd, log=log)
    except OSError as exc:
        _emit_log(log, f"Could not flush the directory entries of {path}: {exc}")
        return False
    finally:
        os.close(fd)
    return True


def sync_file(path: Path, *, log: LogCallable | None = None) -> bool:
    """Flush one already-written file's contents to stable storage."""

    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as exc:
        _emit_log(log, f"Could not open {path} to flush it: {exc}")
        return False
    try:
        _fsync_descriptor(fd, log=log)
    except OSError as exc:
        _emit_log(log, f"Could not flush {path}: {exc}")
        return False
    finally:
        os.close(fd)
    return True


# --------------------------------------------------------------------------
# The update transaction journal
# --------------------------------------------------------------------------

#: The journal lives in the application data directory, never inside the bundle.
#: Two reasons, both load-bearing. It has to survive the very directories the
#: transaction renames, and on macOS anything added inside ``Contents`` would
#: have to be removed again before the ad-hoc seal could be restored -- which is
#: the dance ``_finish_healthy_macos_update`` already performs for ``.previous``
#: and which no recovery record should have to join.
#: The record's *name* carries which installation it belongs to, because its
#: contents cannot be relied on to: a truncated or half-published record has no
#: readable ``resources`` field, and one data directory can be shared by two
#: copies of the application -- a second install, or a ``--data-dir`` aimed at
#: an existing one. Attribution by filename is what lets recovery act on an
#: unreadable record that is unambiguously its own, while leaving another
#: copy's evidence untouched and unread.
#:
#: The consequence, stated because it is a real one: moving an installation
#: between a crash and the next start orphans its record. Nothing then reads or
#: removes it, and reconciliation falls back to the manifest and directory
#: checks that predate the journal, which are themselves conservative.
JOURNAL_PREFIX = "update-transaction"
JOURNAL_SCHEMA = 1

#: States that mean the transaction has reached a decided end. Only these permit
#: ``.previous`` to be reclaimed. Anything else -- including a journal that
#: cannot be parsed -- means "still in flight, keep the rollback material".
TERMINAL_JOURNAL_STATES = frozenset({"installed", "rolled-back", "aborted"})

#: The state read_journal reports for a journal that exists but cannot be read.
#: Deliberately not terminal: an unreadable record of a transaction is not a
#: record that there was none.
UNREADABLE_JOURNAL_STATE = "unreadable"

#: A journal that parsed but is not a record this module could have written.
INVALID_JOURNAL_STATE = "invalid"

#: A journal whose publication was interrupted -- the temporary file survives,
#: so what is at the published name may be this transaction's record or the one
#: it was replacing, and nothing on the disk says which.
INTERRUPTED_PUBLICATION_STATE = "publication-interrupted"

#: Every state that means "do not conclude anything from this record".
UNTRUSTED_JOURNAL_STATES = frozenset(
    {UNREADABLE_JOURNAL_STATE, INVALID_JOURNAL_STATE, INTERRUPTED_PUBLICATION_STATE}
)

#: Written before any restore begins, by every path that restores: the updater's
#: own failure handler, the rollback helper, and recovery itself. A start that
#: finds it knows a restore was under way and must be finished.
#:
#: This matters for one specific shape. A restore killed after its first layer
#: leaves one layer with a ``.previous`` beside it and one without -- which is
#: also what a finished swap leaves. For every bundle this project has ever
#: built the two are still distinguishable, because both manifests carry a
#: ``runtimeId`` and ``layers_disagree`` sees the mismatch; this marker is what
#: covers a bundle whose manifests do not, where that comparison has nothing to
#: compare. It is one write, and a write can be lost, so it is a second line
#: rather than the only one.
ROLLING_BACK_STATE = "rolling-back"


def installation_key(resources: Path) -> str:
    """A stable, filename-safe name for one installed copy of the application."""

    normalized = os.path.normcase(os.path.normpath(str(Path(resources))))
    return hashlib.sha256(normalized.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def journal_path(data_dir: Path, resources: Path) -> Path:
    return Path(data_dir) / f"{JOURNAL_PREFIX}-{installation_key(resources)}.json"


def journal_temp_path(data_dir: Path, resources: Path) -> Path:
    return Path(data_dir) / f"{JOURNAL_PREFIX}-{installation_key(resources)}.json.new"


def _invalid_journal(state: str, detail: str) -> dict[str, Any]:
    """A record that says only "something was in flight, and it is not decided".

    Everything that cannot be trusted collapses to this, and it is deliberately
    *not* terminal: it blocks reclaiming rollback material, and it makes
    recovery restore rather than conclude. A record nobody can read is not a
    record that there was nothing to do.
    """

    return {"state": state, "operation": "unknown", "layers": [], "detail": detail}


def _validate_journal(payload: object) -> dict[str, Any]:
    """Accept only a record this module could have written, or refuse it whole.

    Written because the alternative -- trusting any JSON object with the right
    two keys -- lets a truncated, hand-edited or foreign file reach the branch
    that concludes "installed" and permits deleting the only copy of the
    previous version. Validation failures are answered with
    ``_invalid_journal``, never with a default that lets the record through.
    """

    if not isinstance(payload, dict):
        return _invalid_journal(INVALID_JOURNAL_STATE, "the journal is not an object")
    if payload.get("schema") != JOURNAL_SCHEMA:
        return _invalid_journal(
            INVALID_JOURNAL_STATE, f"unsupported journal schema {payload.get('schema')!r}"
        )
    operation = payload.get("operation")
    if operation not in {"update", "rollback"}:
        return _invalid_journal(
            INVALID_JOURNAL_STATE, f"unknown journal operation {operation!r}"
        )
    state = payload.get("state")
    if not isinstance(state, str) or not state:
        return _invalid_journal(INVALID_JOURNAL_STATE, "the journal records no state")
    for key in ("transaction", "resources", "bundle"):
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            return _invalid_journal(INVALID_JOURNAL_STATE, f"the journal records no {key}")
    entries = payload.get("layers")
    if not isinstance(entries, list):
        return _invalid_journal(INVALID_JOURNAL_STATE, "the journal records no layers")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return _invalid_journal(INVALID_JOURNAL_STATE, "a journal layer is not an object")
        name = entry.get("name")
        # The name is the only journal field that ever becomes part of a path
        # this module touches, so it is restricted to the two directory names
        # the bundle owns. Nothing read from the journal can name a third place.
        if name not in BUNDLE_LAYERS:
            return _invalid_journal(
                INVALID_JOURNAL_STATE, f"a journal layer names {name!r}, which is not a layer"
            )
        if name in seen:
            return _invalid_journal(INVALID_JOURNAL_STATE, f"the journal repeats layer {name!r}")
        seen.add(name)
        staged = entry.get("staged")
        if staged is not None and (not isinstance(staged, str) or not staged):
            return _invalid_journal(
                INVALID_JOURNAL_STATE, f"layer {name!r} records an unusable staged path"
            )
    return dict(payload)


def read_journal(data_dir: Path, resources: Path) -> dict[str, Any] | None:
    """Return the recorded transaction for this data directory, or None.

    Both names are consulted. A surviving ``.json.new`` means a journal was
    being published when the machine stopped, and since publishing it is a
    rename -- durable only if the directory flush that followed it succeeded --
    that temporary file may be the *only* evidence that anything was in flight.
    It is never treated as the record itself: whatever it contains, the answer
    is "unresolved", which keeps the rollback material and sends recovery down
    the restoring path. The published record wins when it is newer in kind
    (non-terminal), and a terminal published record beside a leftover temporary
    is still reported unresolved, because the pair cannot say which came first.
    """

    path = journal_path(data_dir, resources)
    temporary = journal_temp_path(data_dir, resources)
    interrupted_publication = temporary.exists() or temporary.is_symlink()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if interrupted_publication:
            return _invalid_journal(
                INTERRUPTED_PUBLICATION_STATE,
                f"a transaction was being recorded when the machine stopped: {temporary}",
            )
        return None
    except (OSError, ValueError) as exc:
        return _invalid_journal(UNREADABLE_JOURNAL_STATE, f"{type(exc).__name__}: {exc}")
    record = _validate_journal(payload)
    if interrupted_publication:
        # The published record may be this transaction's, or the previous one's
        # that the interrupted publication was about to replace. Nothing on the
        # disk distinguishes them, so neither is allowed to decide anything.
        record = dict(record)
        record["state"] = INTERRUPTED_PUBLICATION_STATE
        record["detail"] = (
            f"a later transaction was being recorded when the machine stopped: {temporary}"
        )
    return record


@dataclass(frozen=True, slots=True)
class JournalDurability:
    """How durable the journal write that just returned actually is."""

    #: The record is at its published name and readable by any later process.
    published: bool
    #: The directory entry that publication created was flushed, so the *name*
    #: survives a power cut. False on Windows always, and on POSIX when the
    #: flush failed; see ``sync_directory``.
    name_synced: bool
    #: The bytes were flushed with the platform's strongest available primitive.
    contents_fully_synced: bool


def write_journal(
    data_dir: Path,
    resources: Path,
    payload: Mapping[str, Any],
    *,
    log: LogCallable | None = None,
) -> JournalDurability:
    """Put the transaction intent on the disk before anything moves.

    Temporary name, contents flushed, renamed over the target, directory
    flushed. A reader therefore sees the previous record or the complete new
    one, never half of either -- and, if the last flush did not happen, may see
    the temporary file as well, which ``read_journal`` treats as "unresolved".

    **What is guaranteed, and what is not.** The bytes are flushed before the
    rename on every platform. The *name* is durable only where the directory
    flush succeeded, which excludes Windows entirely. So this function does not
    promise that a power cut leaves the new record in place; it promises that
    every state a power cut can leave is one the reader above answers
    conservatively. The returned report says which guarantee was obtained, the
    record itself carries it, and no caller claims more than it was given.

    A failure to write at all is fatal to the update by design, and happens
    before the first rename, so a caller that sees it raise still has an
    installation it can simply reopen.
    """

    directory = Path(data_dir)
    target = journal_path(directory, resources)
    temporary = journal_temp_path(directory, resources)
    contents_fully_synced = True
    try:
        directory.mkdir(parents=True, exist_ok=True)
        body = json.dumps(dict(payload), indent=2, sort_keys=True) + "\n"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            contents_fully_synced = _fsync_descriptor(handle.fileno(), log=log)
        os.replace(temporary, target)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise ApplyUpdateError(
            f"Could not record the update transaction journal {target}: {exc}"
        ) from exc
    name_synced = sync_directory(directory, log=log)
    if not name_synced:
        _emit_log(
            log,
            f"The update transaction journal {target} was written but its directory entry "
            "could not be flushed; recovery treats an interrupted publication as "
            "unresolved, so this weakens diagnosis rather than safety.",
        )
    return JournalDurability(
        published=True,
        name_synced=name_synced,
        contents_fully_synced=contents_fully_synced,
    )


def remove_journal(data_dir: Path, resources: Path, *, log: LogCallable | None = None) -> bool:
    """Delete a decided transaction record, and any leftover temporary with it.

    The temporary is removed *first*: it is what makes ``read_journal`` report
    "unresolved", so a leftover that outlived the record it was replacing would
    make the next start refuse to reclaim anything, for ever.
    """

    removed = True
    for path in (journal_temp_path(data_dir, resources), journal_path(data_dir, resources)):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            _emit_log(log, f"Could not remove the resolved update transaction {path}: {exc}")
            removed = False
    sync_directory(Path(data_dir), log=log)
    return removed


#: The write landed.
JOURNAL_STATE_RECORDED = "recorded"
#: There was no record to advance. Reconciliation falls back to the structural
#: checks that predate the journal, which are conservative on their own.
JOURNAL_STATE_ABSENT = "absent"
#: The record cannot be trusted, so it was deliberately left alone. Not a
#: failure: an untrusted record already sends reconciliation down the restoring
#: path, so no marker is needed to steer it, and rewriting it from a partial
#: view would replace evidence with invention.
JOURNAL_STATE_UNTRUSTED = "untrusted"
#: The write was attempted and did not land. The only outcome a caller whose
#: correctness depends on the marker may not continue past.
JOURNAL_STATE_FAILED = "failed"


def set_journal_state(
    data_dir: Path,
    resources: Path,
    state: str,
    *,
    detail: str | None = None,
    log: LogCallable | None = None,
) -> str:
    """Record how far the transaction got, and say which of four things happened.

    **Most of these markers are advisory; one is not, and that is why this
    returns a status rather than a bool.** Reconciliation decides what to do
    from the live directories, so losing "swapped" or "launchers-refreshed"
    changes nothing. ``ROLLING_BACK_STATE`` is different: a restore killed after
    its first layer leaves exactly the shape a finished swap leaves, and for an
    app-only update -- or any update whose two layers share a ``runtimeId`` --
    the manifests cannot tell them apart either. That marker is the only thing
    that can, so a caller about to rename on the strength of it has to know
    whether it is actually on the disk. Collapsing "written", "nothing to
    write", "deliberately not written" and "the write failed" into one ``False``
    hid exactly that distinction.

    A terminal state is still safe to lose: it is only ever written after the
    work it describes has been done and observed, so a start that misses it
    reconciles the same installation again and reaches the same conclusion.
    """

    payload = read_journal(data_dir, resources)
    if payload is None:
        return JOURNAL_STATE_ABSENT
    if payload.get("state") in UNTRUSTED_JOURNAL_STATES:
        # There is nothing coherent to advance. Rewriting it from this partial
        # view would replace the evidence with a record this process invented.
        reason = payload.get("detail") or "the journal cannot be trusted"
        _emit_log(log, f"Not recording update progress {state!r}: {reason}.")
        return JOURNAL_STATE_UNTRUSTED
    payload["state"] = state
    payload["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    if detail is not None:
        payload["detail"] = detail
    try:
        write_journal(data_dir, resources, payload, log=log)
    except ApplyUpdateError as exc:
        _emit_log(log, f"Could not record update progress {state!r}: {exc}")
        return JOURNAL_STATE_FAILED
    return JOURNAL_STATE_RECORDED


def layer_manifest_name(layer_name: str) -> str:
    return "APP-MANIFEST.json" if layer_name == "app" else "RUNTIME-MANIFEST.json"


def read_layer_runtime_id(layer: Path) -> str | None:
    """Return the ``runtimeId`` a layer directory declares, if it declares one."""

    manifest = layer / layer_manifest_name(layer.name)
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("runtimeId")
    return value if isinstance(value, str) and value else None


def layer_runtime_ids(resources: Path) -> tuple[str | None, str | None]:
    """Return (the runtime the installed app requires, the runtime installed)."""

    return (
        read_layer_runtime_id(resources / "app"),
        read_layer_runtime_id(resources / "runtime"),
    )


def journal_describes(journal: Mapping[str, Any], resources: Path) -> bool:
    """Whether a recorded transaction is about *this* installation.

    A data directory is not private to one copy of the application: a second
    install, or a ``--data-dir`` aimed at an existing one, shares it.

    **The record's filename is what attributes it**, not this function.
    ``journal_path`` derives the name from the installation, so a record that
    was read at all was already addressed to this copy -- which is what makes
    it safe to act on an *unreadable* one, and what keeps another copy's
    evidence unread and unremoved. That mattered: attributing by content meant
    a truncated or half-published record, which has no readable ``resources``
    field, had to be assumed to belong to whoever asked, and recovery would
    then delete a shared record it could not prove was its own.

    This check is the second line, for a record whose contents *are* readable:
    it catches a file copied between data directories, or one whose name and
    contents disagree for any other reason. An untrusted record has nothing to
    compare and is answered by its filename alone, which is now sufficient.
    """

    if str(journal.get("state")) in UNTRUSTED_JOURNAL_STATES:
        return True
    recorded = journal.get("resources")
    if not isinstance(recorded, str) or not recorded:
        return False
    try:
        return os.path.normcase(os.path.normpath(recorded)) == os.path.normcase(
            os.path.normpath(str(resources))
        )
    except (TypeError, ValueError):
        return False


def layers_disagree(resources: Path) -> bool:
    """Report a live app and runtime that came from different generations.

    Unreadable or absent manifests are not treated as disagreement: an older
    bundle predates these fields, and refusing to start over a missing file
    would be worse than the mismatch this is looking for.
    """

    required, installed = layer_runtime_ids(resources)
    return required is not None and installed is not None and required != installed


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
    kernel32.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION | WINDOWS_SYNCHRONIZE, False, pid
    )
    waitable = bool(handle)
    if not handle:
        handle = kernel32.OpenProcess(WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # A live process we are not allowed to open still counts as running.
        return ctypes.get_last_error() == WINDOWS_ERROR_ACCESS_DENIED
    try:
        if waitable:
            state = kernel32.WaitForSingleObject(handle, 0)
            if state == WINDOWS_WAIT_OBJECT_0:
                return False
            if state == WINDOWS_WAIT_TIMEOUT:
                return True
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == WINDOWS_STILL_ACTIVE
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

    Both parent directories are flushed after a rename that returned, so on a
    platform that permits it the new directory entry is on stable storage before
    the next rename is issued. Windows does not permit it (``sync_directory``
    explains why) and nothing here depends on it: the journal makes recovery a
    question about the directories that exist, not about the order two renames
    were persisted in.
    """

    deadline = clock() + timeout
    while True:
        try:
            os.replace(source, destination)
            sync_directory(source.parent)
            if destination.parent != source.parent:
                sync_directory(destination.parent)
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
                _emit_log(log, f"Could not remove the earlier failed copy {candidate}: {exc}")
                continue
        return candidate
    raise ApplyUpdateError(f"Too many undeleted failed copies remain beside {target}.")


def plan_layer_swap(
    resources: Path,
    staged_app: Path,
    staged_runtime: Path | None,
) -> list[tuple[Path, Path]]:
    """Return the (live layer, staged layer) renames an update must perform.

    Separated from the swap itself so the transaction journal can be written
    from the same plan the swap will execute, before the swap starts. Every
    precondition is checked here, so a refusal costs nothing: nothing has moved.
    """

    layers: list[tuple[Path, Path]] = []
    if staged_runtime is not None:
        layers.append((resources / "runtime", staged_runtime.resolve()))
    # Install the app last. Each rename is atomic but the sequence is not, and
    # process death cannot run the rollback handler below. Interrupted between
    # the two, an old app on a newer runtime is likelier to start than a new app
    # on the runtime it explicitly replaced -- and the app layer is the one whose
    # manifest names the runtime it needs, so the mismatch is detectable at
    # startup rather than silent.
    layers.append((resources / "app", staged_app.resolve()))
    for target, staged in layers:
        previous = target.with_name(target.name + PREVIOUS_SUFFIX)
        if not target.is_dir():
            raise ApplyUpdateError(f"The installed layer is missing: {target}")
        if not staged.is_dir():
            raise ApplyUpdateError(f"The staged layer is missing: {staged}")
        if previous.exists() or previous.is_symlink():
            raise ApplyUpdateError(
                f"A previous update has not completed its healthy-start check: {previous}"
            )
    return layers


def stage_recovery_helper(
    data_dir: Path,
    *,
    bundle: Path | None = None,
    log: LogCallable | None = None,
) -> Path | None:
    """Put a runnable copy of this module outside the bundle, before the swap.

    The rollback handoff copies it too, but only when a *handled* failure hands
    off. A process killed mid-swap hands off nothing, and the copy is then
    exactly what is missing in the state that needs it most -- the app layer
    can be the directory that is gone, and this module lives inside it.

    Best effort by design: the swap is not worth failing over the loss of a
    manual repair route, and the automatic route does not depend on this copy.
    Returns the staged path, or None with the reason logged.
    """

    origin = Path(__file__).resolve()
    destination = Path(data_dir) / RECOVERY_HELPER_DIRECTORY / RECOVERY_HELPER_NAME
    # Two files, because this module imports the claim and refuses to run
    # without it. Staging one of them would produce a helper that cannot start,
    # which is worse than no helper at all: it looks like a repair route.
    companions = ((origin, destination), (origin.with_name(RECOVERY_LOCK_NAME),
                                          destination.with_name(RECOVERY_LOCK_NAME)))
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        for source, target in companions:
            shutil.copyfile(source, target)
            sync_file(target, log=log)
        sync_directory(destination.parent, log=log)
    except OSError as exc:
        _emit_log(log, f"Could not stage the recovery helper at {destination}: {exc}")
        return None
    # Written out in full because the window this covers is the one where no
    # launcher can run it for the user: an interruption while the app layer
    # itself is being replaced leaves nothing that reaches this code
    # automatically, on any platform. The line below is then the whole repair,
    # and the update log is where somebody will already be looking.
    if bundle is not None:
        _emit_log(
            log,
            "If this update is interrupted and the application will not start, "
            "recover it by running: <python3.13> "
            f'"{destination}" --recover --bundle "{bundle}" --data-dir "{Path(data_dir)}"',
        )
    return destination


def begin_update_transaction(
    *,
    data_dir: Path,
    bundle: Path,
    resources: Path,
    layers: Sequence[tuple[Path, Path]],
    platform_name: str = sys.platform,
) -> dict[str, Any]:
    """Record what is about to be renamed, durably, before renaming any of it.

    The record carries the runtime id each layer declares now and the one its
    replacement declares, because after the fact the manifests can only say what
    a layer *is*, never which of two renames was the one that reached the disk.

    An unresolved earlier transaction is refused rather than overwritten. That
    is the same conservatism as the ``.previous`` precondition above, one level
    up: an installation whose last update was never decided is not a base to
    start another one from.
    """

    existing = read_journal(data_dir, resources)
    if existing is not None and str(existing.get("state")) not in TERMINAL_JOURNAL_STATES:
        raise ApplyUpdateError(
            "An earlier update transaction "
            f"({existing.get('transaction', 'unidentified')}) is still unresolved in state "
            f"{existing.get('state')!r}; refusing to start another before it is recovered."
        )
    now = datetime.now().isoformat(timespec="seconds")
    payload: dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "transaction": uuid.uuid4().hex,
        "operation": "update",
        "state": "planned",
        "platform": platform_name,
        "bundle": str(bundle),
        "resources": str(resources),
        "pid": os.getpid(),
        "startedAt": now,
        "updatedAt": now,
        "directorySync": DIRECTORY_SYNC_SUPPORTED,
        "layers": [
            {
                "name": target.name,
                "staged": str(staged),
                "installedRuntimeId": read_layer_runtime_id(target),
                "stagedRuntimeId": read_layer_runtime_id(staged),
            }
            for target, staged in layers
        ],
    }
    if existing is not None:
        payload["supersedes"] = existing.get("transaction")
    write_journal(data_dir, resources, payload)
    return payload


def begin_rollback_transaction(
    *,
    data_dir: Path,
    bundle: Path,
    resources: Path,
    platform_name: str = sys.platform,
    reason: str,
) -> dict[str, Any]:
    """Record a restore before it starts, superseding whatever it is undoing.

    A rollback may legitimately begin on top of an unresolved update -- undoing
    it is the whole point -- so unlike ``begin_update_transaction`` this does
    not refuse one. What it must not do is lose the fact that something is in
    flight, so the previous transaction id is kept.
    """

    existing = read_journal(data_dir, resources)
    now = datetime.now().isoformat(timespec="seconds")
    payload: dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "transaction": uuid.uuid4().hex,
        "operation": "rollback",
        "state": "planned",
        "platform": platform_name,
        "bundle": str(bundle),
        "resources": str(resources),
        "pid": os.getpid(),
        "startedAt": now,
        "updatedAt": now,
        "directorySync": DIRECTORY_SYNC_SUPPORTED,
        "reason": reason,
        "layers": [
            {"name": name, "staged": None}
            for name in BUNDLE_LAYERS
            if (resources / f"{name}{PREVIOUS_SUFFIX}").is_dir()
        ],
    }
    if existing is not None:
        payload["supersedes"] = existing.get("transaction")
    write_journal(data_dir, resources, payload)
    return payload


def swap_staged_layers(
    resources: Path,
    staged_app: Path,
    staged_runtime: Path | None,
    *,
    renamer: RenameCallable = _rename,
    journal_dir: Path | None = None,
) -> None:
    """Swap complete layers into place and restore all old layers on failure."""

    layers = plan_layer_swap(resources, staged_app, staged_runtime)

    def progress(detail: str) -> None:
        if journal_dir is not None:
            set_journal_state(journal_dir, resources, "swapping", detail=detail)

    moved_old: list[tuple[Path, Path]] = []
    moved_new: list[tuple[Path, Path]] = []
    try:
        for target, staged in layers:
            previous = target.with_name(target.name + PREVIOUS_SUFFIX)
            renamer(target, previous)
            moved_old.append((target, previous))
            progress(f"moved {target.name} aside to {previous.name}")
            renamer(staged, target)
            moved_new.append((target, staged))
            progress(f"installed the staged {target.name}")
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

    replaced: list[tuple[Path, Path]] = []
    written: list[Path] = []
    try:
        incoming_directory.mkdir()
        for source, destination in files:
            copier(runtime / source, incoming_directory / destination)
        for _source, destination in files:
            # Flush the copies before any of them is renamed into place. A
            # launcher file whose directory entry survived a power cut but whose
            # contents did not is a bundle that cannot start, and unlike the
            # layer directories there is no manifest that would reveal it.
            sync_file(incoming_directory / destination, log=log)
        sync_directory(incoming_directory, log=log)
        for _source, destination in files:
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
            _emit_log(log, f"Could not remove the {description} {path}: {exc}")
            return
        removed.append(path)
        _emit_log(log, f"Removed {description}: {path}")

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
        _emit_log(log, message)

    layers = [
        (resources / name, resources / f"{name}{PREVIOUS_SUFFIX}")
        for name in BUNDLE_LAYERS
        if (resources / f"{name}{PREVIOUS_SUFFIX}").is_dir()
    ]
    launcher_files = [
        (
            previous.with_name(previous.name[: -len(PREVIOUS_SUFFIX)]),
            previous,
        )
        for previous in sorted(resources.glob(f"*{PREVIOUS_SUFFIX}"))
        if previous.is_file() or previous.is_symlink()
    ]
    if not layers and not launcher_files:
        report("No previous bundle layers or launcher files were available for rollback.")
        return False

    moved_current: list[tuple[Path, Path]] = []
    restored: list[tuple[Path, Path]] = []
    displaced: list[Path] = []

    def reverse_partial_rollback(
        restored_launchers: Sequence[tuple[Path, Path, Path | None]],
    ) -> None:
        for current, previous, failed in reversed(restored_launchers):
            try:
                if (current.exists() or current.is_symlink()) and not previous.exists():
                    renamer(current, previous)
            except OSError as exc:
                report(f"Could not undo the partial launcher restore of {current}: {exc}")
            try:
                if (
                    failed is not None
                    and (failed.exists() or failed.is_symlink())
                    and not current.exists()
                ):
                    renamer(failed, current)
            except OSError as exc:
                report(f"Could not put {failed} back as {current}: {exc}")
        for current, previous in reversed(restored):
            try:
                if (current.exists() or current.is_symlink()) and not previous.exists():
                    renamer(current, previous)
            except OSError as exc:
                report(f"Could not undo the partial restore of {current}: {exc}")
        for current, failed in reversed(moved_current):
            try:
                if (failed.exists() or failed.is_symlink()) and not current.exists():
                    renamer(failed, current)
            except OSError as exc:
                report(f"Could not put {failed} back as {current}: {exc}")

    try:
        for current, previous in layers:
            failed = _failed_path(current, log=log)
            if current.exists() or current.is_symlink():
                renamer(current, failed)
                moved_current.append((current, failed))
                displaced.append(failed)
                report(f"Moved the failed layer aside: {current} -> {failed}")
            renamer(previous, current)
            restored.append((current, previous))
            report(f"Restored the previous layer: {previous} -> {current}")
    except (OSError, ApplyUpdateError) as exc:
        report(f"Rollback could not restore the bundle layers: {exc}")
        reverse_partial_rollback(())
        report(
            "The bundle layers were left as the rollback found them; "
            "review the entries above before changing the installation."
        )
        return False

    # Launcher files saved beside the layers must go back with them, or a
    # restored runtime would keep the newer launcher that failed to start. One
    # file that cannot be restored must not stop the other five: a partially
    # refreshed launcher set is the very failure this loop repairs.
    restored_launchers: list[tuple[Path, Path, Path | None]] = []
    launcher_errors: list[str] = []
    for current, previous in launcher_files:
        moved_aside: Path | None = None
        try:
            if current.exists() or current.is_symlink():
                moved_aside = _failed_path(current, log=log)
                renamer(current, moved_aside)
                displaced.append(moved_aside)
            renamer(previous, current)
        except (OSError, ApplyUpdateError) as exc:
            launcher_errors.append(f"{current}: {exc}")
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
        restored_launchers.append((current, previous, moved_aside))
        report(f"Restored the previous launcher file: {current}")

    complete = (
        not launcher_errors
        and all((resources / name).is_dir() for name in BUNDLE_LAYERS)
        and all(current.is_dir() and not previous.exists() for current, previous in layers)
        and all(
            (current.is_file() or current.is_symlink()) and not previous.exists()
            for current, previous in launcher_files
        )
    )
    if not complete:
        reverse_partial_rollback(restored_launchers)
        detail = "; ".join(launcher_errors) if launcher_errors else "incomplete live paths"
        report(f"Rollback failed final-state validation: {detail}.")
        report(
            "The bundle layers and launcher files were left as the rollback found them; "
            "review the entries above before changing the installation."
        )
        return False

    suffix = f" and {len(launcher_files)} launcher files" if launcher_files else ""
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


# --------------------------------------------------------------------------
# Reconciling an interrupted transaction
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    """What reconciliation found and what it did about it."""

    #: ``none`` (nothing was in flight, or it was already decided),
    #: ``completed`` (the update had finished; the journal now says so),
    #: ``rolled-back`` (the previous version was restored),
    #: ``failed`` (the installation still needs attention -- do not start).
    action: str
    detail: str


def _layer_paths(resources: Path, entry: Mapping[str, Any]) -> tuple[Path, Path, Path | None]:
    name = str(entry.get("name") or "")
    live = resources / name
    previous = resources / f"{name}{PREVIOUS_SUFFIX}"
    staged_value = entry.get("staged")
    staged = Path(str(staged_value)) if isinstance(staged_value, str) and staged_value else None
    return live, previous, staged


def _update_layer_state(resources: Path, entry: Mapping[str, Any]) -> str:
    """Classify one layer of an interrupted update from what is on the disk.

    The staged directory is the durable marker the manifests cannot be: it
    exists until the moment it *becomes* the live layer, so its presence says
    "this layer was not installed" no matter which rename reached the platter
    first. That is why reconciliation needs no ordering guarantee from the file
    system, only the intent that named the staged path in the first place.
    """

    live, previous, staged = _layer_paths(resources, entry)
    if staged is not None and (staged.is_dir() or staged.is_symlink()):
        if live.is_dir() and not (previous.exists() or previous.is_symlink()):
            return "untouched"
        return "interrupted"
    if not live.is_dir():
        return "interrupted"
    if previous.exists() or previous.is_symlink():
        return "installed"
    # No staged directory, a live layer, and no rollback material. Either the
    # swap finished and a healthy start already reclaimed it, or this layer was
    # never part of the move. Both are settled states, and neither is a reason
    # to touch anything.
    return "settled"


def _rollback_layer_state(resources: Path, entry: Mapping[str, Any]) -> str:
    live, previous, _staged = _layer_paths(resources, entry)
    if previous.is_dir():
        return "pending"
    return "settled" if live.is_dir() else "interrupted"


def _launcher_files_are_settled(resources: Path) -> tuple[bool, str]:
    """Whether the launcher files beside the layers match the installed runtime.

    Windows keeps ``Waveguide Generator.exe`` and its DLLs beside ``runtime``
    rather than inside it, so a swap is only finished once those copies match
    the runtime that is now installed. Comparing them is a state check, not a
    progress marker, so it survives an interruption between any two of them.
    macOS and Linux declare no launcher files and settle trivially.
    """

    try:
        files = staged_launcher_files(resources / "runtime")
    except ApplyUpdateError as exc:
        return False, str(exc)
    for source, destination in files:
        live = resources / destination
        origin = resources / "runtime" / source
        if not origin.is_file():
            return False, f"the installed runtime is missing {origin}"
        if not live.is_file():
            return False, f"the launcher file {live} is missing"
        try:
            if live.stat().st_size != origin.stat().st_size or live.read_bytes() != (
                origin.read_bytes()
            ):
                return False, f"the launcher file {live} does not match the installed runtime"
        except OSError as exc:
            return False, f"could not compare the launcher file {live}: {exc}"
    return True, ""


@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    """What a restore achieved, in the parts that can fail separately."""

    #: Every available ``.previous`` layer and launcher file went back.
    restored: bool
    #: Why the bundle seal could not be restored afterwards, or None.
    seal_error: str | None
    #: One sentence for a log or a dialog.
    detail: str
    #: Whether anything was renamed at all. False only when the restore refused
    #: to begin because it could not record that it had, which is a different
    #: report to a user than "the rollback failed": nothing moved.
    attempted: bool = True

    @property
    def complete(self) -> bool:
        """Whether the installation is both restored *and* sealed."""

        return self.restored and self.seal_error is None


def restore_previous_generation(
    resources: Path,
    bundle: Path | None,
    *,
    platform_name: str,
    renamer: RenameCallable = _rename,
    runner: CommandRunner = subprocess.run,
    log: LogCallable | None = None,
    data_dir: Path | None = None,
) -> RestoreOutcome:
    """Roll back, re-seal, and only then record that the transaction ended.

    **Every path that initiates a rollback goes through here**, and that is the
    point of the function rather than a convenience. The ordering it enforces
    was implemented once, in reconciliation, and missing from the three places
    that actually start a rollback: the updater's own post-mutation handler, the
    detached rollback helper, and the desktop window's in-process fallback. All
    three published ``rolled-back`` as soon as the renames finished and only
    then tried to re-seal -- so a failed or interrupted reseal left a *terminal*
    record over an unsealed bundle, and the next start read the terminal state,
    returned "already decided", and never reached the retry that exists for
    exactly this. One unsealed macOS bundle, and nothing left that would ever
    come back to it.

    So the terminal state is written here, after the seal, and only if the seal
    came back. Anything short of that keeps the rolling-back marker with the
    reason in its detail, which is what steers the next start into finishing the
    job.

    ``data_dir`` is given whenever a journal exists, and **the marker written
    before the first rename is required, not advisory**. A restore killed after
    its first layer leaves exactly the shape a finished swap leaves; the
    manifests separate the two only when the layers carry different
    ``runtimeId``s, which an app-only update and any same-runtime update do not.
    So if that marker did not reach the disk and the restore ran anyway, a kill
    in the middle leaves the *old* update state on the record, and the next
    start can read a restored old app as an installed new one -- and reclaim the
    only copy of the version that worked. Nothing is renamed until it is
    recorded, exactly as no swap begins until its intent is.

    "Deliberately not written" is not the same as "did not land". An untrusted
    record is left alone on purpose and already sends reconciliation down the
    restoring path, so it needs no marker to steer it; a *failed write* is the
    one outcome this refuses to continue past.
    """

    def record(state: str, detail: str) -> str:
        if data_dir is None:
            return JOURNAL_STATE_ABSENT
        return set_journal_state(data_dir, resources, state, detail=detail, log=log)

    intent = record(ROLLING_BACK_STATE, "restoring the previous layers")
    if intent == JOURNAL_STATE_FAILED:
        detail = (
            "the rollback was not started because the record that a restore had begun "
            "could not be written; the installation was left exactly as it was found"
        )
        _emit_log(log, detail)
        return RestoreOutcome(
            restored=False, seal_error=None, detail=detail, attempted=False
        )
    pending = [
        name for name in BUNDLE_LAYERS if (resources / f"{name}{PREVIOUS_SUFFIX}").is_dir()
    ] + [
        path.name
        for path in sorted(resources.glob(f"*{PREVIOUS_SUFFIX}"))
        if path.is_file() or path.is_symlink()
    ]
    if not pending:
        detail = "no previous layer or launcher file was available to restore"
        record(ROLLING_BACK_STATE, detail)
        return RestoreOutcome(restored=False, seal_error=None, detail=detail)
    if not rollback_previous_layers(resources, renamer=renamer, log=log):
        detail = "the previous version could not be fully restored"
        record(ROLLING_BACK_STATE, detail)
        return RestoreOutcome(restored=False, seal_error=None, detail=detail)
    if bundle is not None:
        # The missing-layer path has always re-sealed here; the mixed-generation
        # path did not, which left a macOS bundle whose ad-hoc signature no
        # longer covered its contents. One call, every path.
        try:
            repair_bundle(bundle, platform_name=platform_name, runner=runner, log=log)
        except ApplyUpdateError as exc:
            detail = f"the restored bundle could not be signed and verified: {exc}"
            record(ROLLING_BACK_STATE, detail)
            return RestoreOutcome(restored=True, seal_error=str(exc), detail=detail)
    detail = "the previous version was restored"
    record("rolled-back", detail)
    return RestoreOutcome(restored=True, seal_error=None, detail=detail)


def _restore_previous_layers(
    resources: Path,
    bundle: Path | None,
    *,
    platform_name: str,
    renamer: RenameCallable,
    runner: CommandRunner,
    log: LogCallable | None,
    data_dir: Path | None = None,
) -> tuple[bool, str]:
    """Reconciliation's view of :func:`restore_previous_generation`."""

    outcome = restore_previous_generation(
        resources,
        bundle,
        platform_name=platform_name,
        renamer=renamer,
        runner=runner,
        log=log,
        data_dir=data_dir,
    )
    return outcome.complete, outcome.detail


def _seal_after_recovery(
    bundle: Path | None,
    *,
    platform_name: str,
    runner: CommandRunner,
    log: LogCallable | None,
) -> tuple[bool, str]:
    """Restore the bundle seal before a recovered installation may be launched.

    Reachable whenever the *layers* are already where they belong but the seal
    is not: a restore killed after its last rename, or one whose terminal state
    was the write that was lost. On macOS the ad-hoc signature then still covers
    the generation that was replaced, and the bundle either will not launch or
    launches with a seal that does not describe it.

    Called before any terminal state is written, and its failure keeps the
    transaction open on purpose -- a seal that could not be restored is a seal
    the next start has to try again.
    """

    if bundle is None:
        return True, ""
    try:
        repair_bundle(bundle, platform_name=platform_name, runner=runner, log=log)
    except ApplyUpdateError as exc:
        return False, f"the recovered bundle could not be signed and verified: {exc}"
    return True, ""


def recover_transaction(
    *,
    data_dir: Path,
    resources: Path,
    bundle: Path | None = None,
    platform_name: str = sys.platform,
    renamer: RenameCallable = _rename,
    runner: CommandRunner = subprocess.run,
    log: LogCallable | None = None,
) -> RecoveryOutcome:
    """Decide an interrupted update from its journal and the live directories.

    Runs in whatever process gets there first: the desktop launcher before it
    starts the server, or the standalone ``--recover`` helper when the launcher
    itself cannot run. It reads only the journal and the file system, so it
    needs nothing that the interrupted process left in memory -- which is the
    property that a killed process cannot take away.
    """

    journal = read_journal(data_dir, resources)
    if journal is None:
        return RecoveryOutcome("none", "No update transaction was recorded.")
    state = str(journal.get("state") or "")
    identifier = str(journal.get("transaction") or "unidentified")
    if state in TERMINAL_JOURNAL_STATES:
        return RecoveryOutcome("none", f"Update transaction {identifier} is already {state}.")

    if not journal_describes(journal, resources):
        # Two installations can share one data directory -- a second copy of the
        # app, or a --data-dir pointed at an existing one -- and a record from
        # the other copy says nothing about this one. Acting on it would move
        # directories on the strength of a file that never described them. So
        # this installation is treated as having no record, which leaves the
        # structural checks that predate the journal in charge, and the record
        # itself is left alone for the installation it belongs to.
        _emit_log(
            log,
            f"Ignoring update transaction {identifier}: it records "
            f"{journal.get('resources')!r}, not this installation at {resources}.",
        )
        return RecoveryOutcome(
            "none", "The recorded update transaction belongs to another installation."
        )

    entries = journal.get("layers")
    entries = [entry for entry in entries if isinstance(entry, Mapping)] if (
        isinstance(entries, list)
    ) else []
    operation = str(journal.get("operation") or "unknown")

    def decide(state_name: str, detail: str) -> None:
        """Record the end of the transaction, or clear a record that cannot hold one.

        ``set_journal_state`` refuses to advance an untrusted record, and it is
        right to: rewriting a truncated or half-published file from a partial
        view would replace the evidence with something this process invented.
        But a decided transaction has to stop blocking, or the rollback material
        it protects is protected for ever and the next update is refused too. So
        the untrusted record is removed instead of edited. Nothing is lost that
        could have been read, and recovery has by then acted only on directories
        derived from the caller's own installation.
        """

        if state in UNTRUSTED_JOURNAL_STATES:
            remove_journal(data_dir, resources, log=log)
            _emit_log(
                log,
                f"Removed the unreadable update transaction record after {state_name}: {detail}.",
            )
            return
        set_journal_state(data_dir, resources, state_name, detail=detail, log=log)

    def finish(restored: bool, detail: str) -> RecoveryOutcome:
        if restored:
            decide("rolled-back", detail)
            _emit_log(log, f"Recovered update transaction {identifier}: {detail}.")
            return RecoveryOutcome("rolled-back", detail)
        # Deliberately the rolling-back marker again, with the reason in the
        # detail, rather than a distinct "recovery-failed" state. The state's
        # only job is to steer the *next* start, and a restore that has been
        # announced but not finished is still a restore to finish. A separate
        # state lost that, and the retry then re-read a correctly restored old
        # generation as a completed update and recorded it as installed.
        set_journal_state(
            data_dir, resources, ROLLING_BACK_STATE, detail=detail, log=log
        )
        message = (
            f"Update transaction {identifier} could not be recovered: {detail}. "
            "The rollback material was kept."
        )
        _emit_log(log, message)
        return RecoveryOutcome("failed", message)

    if not entries or state in UNTRUSTED_JOURNAL_STATES:
        # An intent that cannot be read, cannot be trusted, or was only half
        # published is still an intent. Restore whatever rollback material
        # exists rather than assume the swap finished; the one thing that must
        # never come out of an unreadable record is the conclusion "installed".
        restored, detail = _restore_previous_layers(
            resources,
            bundle,
            platform_name=platform_name,
            renamer=renamer,
            runner=runner,
            log=log,
            data_dir=data_dir,
        )
        if not restored and "no previous layer" in detail:
            # Nothing was left to restore -- but an unreadable record cannot say
            # whether that is because nothing ever moved or because a restore
            # got all the way through its renames and stopped before its reseal.
            # The second leaves a bundle whose signature still covers the
            # generation that was replaced, so the seal is restored before this
            # installation is called usable, and a seal that will not come back
            # keeps the transaction open instead of closing it as "aborted".
            sealed, seal_detail = _seal_after_recovery(
                bundle, platform_name=platform_name, runner=runner, log=log
            )
            if not sealed:
                message = (
                    f"Update transaction {identifier} had nothing left to restore, but "
                    f"{seal_detail}."
                )
                _emit_log(log, message)
                return RecoveryOutcome("failed", message)
            decide("aborted", detail)
            return RecoveryOutcome("none", f"Update transaction {identifier}: {detail}.")
        return finish(restored, detail)

    if operation == "rollback":
        states = [_rollback_layer_state(resources, entry) for entry in entries]
        if all(value == "settled" for value in states):
            # The renames finished; the seal may not have. Re-seal *before*
            # recording the end, so a failure here is retried rather than
            # locked in behind a terminal state.
            sealed, seal_detail = _seal_after_recovery(
                bundle, platform_name=platform_name, runner=runner, log=log
            )
            if not sealed:
                set_journal_state(
                    data_dir, resources, ROLLING_BACK_STATE, detail=seal_detail, log=log
                )
                message = (
                    f"Rollback transaction {identifier} restored every layer, but "
                    f"{seal_detail}."
                )
                _emit_log(log, message)
                return RecoveryOutcome("failed", message)
            set_journal_state(
                data_dir,
                resources,
                "rolled-back",
                detail="every layer was already restored",
                log=log,
            )
            return RecoveryOutcome(
                "none", f"Rollback transaction {identifier} had already completed."
            )
        restored, detail = _restore_previous_layers(
            resources,
            bundle,
            platform_name=platform_name,
            renamer=renamer,
            runner=runner,
            log=log,
            data_dir=data_dir,
        )
        return finish(restored, detail)

    states = [_update_layer_state(resources, entry) for entry in entries]
    if state == ROLLING_BACK_STATE:
        # A restore was already under way. Finish it; do not re-examine whether
        # the update looks complete, because a restore that got through its
        # first layer leaves precisely that appearance.
        _emit_log(
            log,
            f"Update transaction {identifier} was already being rolled back; finishing it.",
        )
        restored, detail = _restore_previous_layers(
            resources,
            bundle,
            platform_name=platform_name,
            renamer=renamer,
            runner=runner,
            log=log,
            data_dir=data_dir,
        )
        if not restored and "no previous layer" in detail:
            # Same shape as above: nothing left to move, and a seal that may
            # still describe the generation that was replaced.
            sealed, seal_detail = _seal_after_recovery(
                bundle, platform_name=platform_name, runner=runner, log=log
            )
            if not sealed:
                set_journal_state(
                    data_dir, resources, ROLLING_BACK_STATE, detail=seal_detail, log=log
                )
                message = (
                    f"Update transaction {identifier} had already been rolled back, but "
                    f"{seal_detail}."
                )
                _emit_log(log, message)
                return RecoveryOutcome("failed", message)
            decide("rolled-back", "the restore had already finished")
            return RecoveryOutcome(
                "none", f"Update transaction {identifier} had already been rolled back."
            )
        return finish(restored, detail)

    if all(value == "untouched" for value in states):
        detail = "no layer had been swapped when the update stopped"
        set_journal_state(data_dir, resources, "aborted", detail=detail, log=log)
        _emit_log(log, f"Update transaction {identifier} was abandoned: {detail}.")
        return RecoveryOutcome("none", detail)

    if all(value in {"installed", "settled"} for value in states):
        reasons: list[str] = []
        if layers_disagree(resources):
            required, installed = layer_runtime_ids(resources)
            reasons.append(
                f"the installed app requires runtime {required!r} but runtime {installed!r} "
                "is installed"
            )
        settled, launcher_detail = _launcher_files_are_settled(resources)
        if not settled:
            reasons.append(launcher_detail)
        if not reasons:
            detail = "every layer of the interrupted update was already installed"
            if bundle is not None:
                try:
                    repair_bundle(bundle, platform_name=platform_name, runner=runner, log=log)
                except ApplyUpdateError as exc:
                    # The layers are right but the seal is not, and on macOS an
                    # unsealed bundle is not a startable one. Roll back rather
                    # than hand the user a bundle Gatekeeper will refuse.
                    restored, restore_detail = _restore_previous_layers(
                        resources,
                        bundle,
                        platform_name=platform_name,
                        renamer=renamer,
                        runner=runner,
                        log=log,
                        data_dir=data_dir,
                    )
                    return finish(restored, f"the swapped bundle could not be sealed: {exc}")
            set_journal_state(data_dir, resources, "installed", detail=detail, log=log)
            _emit_log(log, f"Update transaction {identifier} completed: {detail}.")
            return RecoveryOutcome("completed", detail)
        _emit_log(
            log,
            f"Update transaction {identifier} left an inconsistent installation: "
            + "; ".join(reasons),
        )

    restored, detail = _restore_previous_layers(
        resources,
        bundle,
        platform_name=platform_name,
        renamer=renamer,
        runner=runner,
        log=log,
        data_dir=data_dir,
    )
    return finish(restored, detail)


def commit_transaction(
    data_dir: Path,
    *,
    resources: Path,
    log: LogCallable | None = None,
) -> tuple[bool, str]:
    """Close a decided transaction so its ``.previous`` may be reclaimed.

    Called from the one place that has the evidence: a start that reached a
    healthy interface. Everything else -- an updater that renamed four
    directories, a reseal that passed, a relaunch that stayed up -- is progress,
    not proof, and none of it is allowed to reclaim the rollback material.

    Returns whether reclaiming may proceed. A transaction that is still open
    means recovery has not run or did not finish, and the answer is no.
    """

    journal = read_journal(data_dir, resources)
    if journal is None:
        return True, "no update transaction was recorded"
    state = str(journal.get("state") or "")
    identifier = str(journal.get("transaction") or "unidentified")
    if not journal_describes(journal, resources):
        # Another copy's transaction. It says nothing about this installation,
        # and this installation has just started healthily, so its own rollback
        # material is spent -- but the record stays for its owner.
        return True, "the recorded update transaction belongs to another installation"
    if state not in TERMINAL_JOURNAL_STATES:
        return False, (
            f"update transaction {identifier} is unresolved (state {state!r}); "
            "the rollback material was kept"
        )
    remove_journal(data_dir, resources, log=log)
    return True, f"update transaction {identifier} committed from state {state!r}"


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
    platform_name: str = sys.platform,
    timeout: float = RELAUNCH_CONFIRM_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> str | None:
    """Return a description of an immediate relaunch failure, or ``None``.

    ``Popen`` returning is not evidence that the application started: a command
    line the interpreter rejects -- ``unknown option --port`` -- produces a
    healthy-looking ``Popen`` and a process that is gone milliseconds later.
    Watch the child for a moment so the updater cannot log success over that.

    What is being watched differs by platform, and reading one as the other
    inverts the verdict. On Windows the child *is* the application, so an exit
    is the failure. On macOS the child is :program:`open`, a stub that hands
    the request to LaunchServices and exits immediately -- with status 0 when
    the application was started, non-zero when it could not be. Treating that
    exit as the application's own reported every successful macOS update as a
    relaunch failure, and the caller answered by rolling the update back.
    """

    poll = getattr(process, "poll", None)
    if not callable(poll):
        return None
    deadline = clock() + timeout
    while True:
        code = poll()
        if code is not None:
            if platform_name == "darwin":
                if code == 0:
                    return None
                return f"could not be reopened: open exited with code {code}"
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
        # Absolute, because the updater inherits whatever PATH the desktop had.
        command = ["/usr/bin/open", "-n", str(bundle)]
        if arguments:
            command.extend(("--args", *arguments))
        return command
    if platform_name == "win32":
        return [str(bundle / WINDOWS_LAUNCHER_NAME)]
    if platform_name.startswith("linux"):
        # The plainest of the three. The launcher is a shell script that execs
        # the bundled interpreter, so it takes the arguments directly: there is
        # no LaunchServices in the way as on macOS, and no renamed pythonw.exe
        # parsing them as interpreter options as on Windows.
        return [str(bundle / LINUX_LAUNCHER_NAME), *arguments]
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
        _emit_log(
            log,
            "The relaunch could not carry these arguments and started without "
            f"them: {' '.join(dropped)}"
        )
    try:
        process = relauncher(command, platform_name, environment=environment)
    except (OSError, subprocess.SubprocessError) as exc:
        _emit_log(log, f"Could not relaunch Waveguide Generator: {exc}")
        return 3
    _emit_log(log, f"Relaunched Waveguide Generator: {' '.join(command)}")
    failure = confirm(process)
    if failure is not None:
        _emit_log(
            log,
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
    confirm: Callable[[Any], str | None] | None = None,
    environ: Mapping[str, str] | None = None,
    logger: LogCallable | None = None,
    failure_reporter: LogCallable | None = None,
) -> int:
    """Wait, swap, repair the seal, and relaunch; return a process exit code."""

    # Bound to the platform this call is acting on, not to the one the module
    # was imported on: the probe reads a relaunch child that differs by
    # platform, so an unbound default would misread an injected platform.
    confirmer = confirm or (
        lambda process: confirm_relaunch(process, platform_name=platform_name)
    )
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
    environment, dropped = relaunch_environment(
        relaunch_arguments,
        platform_name,
        data_dir=data_dir,
        environ=environ,
    )

    def live_installation_is_complete() -> bool:
        if not all((resources / name).is_dir() for name in ("app", "runtime")):
            return False
        # macOS reaches its executable through the bundle, which `open` refuses
        # if it is malformed; the other two are plain files beside the layers
        # and a swap that lost one leaves a directory that looks installed.
        if platform_name == "win32":
            return (resources / WINDOWS_LAUNCHER_NAME).is_file()
        if platform_name.startswith("linux"):
            return (resources / LINUX_LAUNCHER_NAME).is_file()
        return True

    def relaunch_current() -> str | None:
        if not live_installation_is_complete():
            return "the on-disk installation is incomplete and was not relaunched"
        try:
            process = relauncher(command, platform_name, environment=environment)
        except Exception as exc:  # noqa: BLE001 - recovery must describe any launch failure
            return f"the application could not be relaunched: {type(exc).__name__}: {exc}"
        failure = confirmer(process)
        if failure is not None:
            return f"the application {failure}"
        return None

    def abandon_before_mutation(reason: str) -> int:
        """Report an update that stopped with the installation as it was."""

        relaunch_error = relaunch_current()
        outcome = (
            "The current version was reopened."
            if relaunch_error is None
            else f"The update was cancelled, but {relaunch_error}."
        )
        message = f"{reason}\n\n{outcome} Review update.log in the application data log directory."
        log(message)
        report(message)
        return 2

    def finish_failure_after_mutation(reason: str, exit_code: int) -> int:
        # Do not emit the failure diagnostic until rollback, required signing,
        # and the attempt to reopen the restored version have all run.
        #
        # The restore records its own end, after the seal and only if the seal
        # came back. Publishing "rolled-back" here, as this used to, put a
        # terminal record over a bundle whose signature had not been restored --
        # and a terminal record is precisely what makes the next start say
        # "already decided" and skip the retry.
        restore = restore_previous_generation(
            resources,
            resolved_bundle,
            platform_name=platform_name,
            renamer=renamer,
            runner=runner,
            log=selected_logger,
            data_dir=data_dir,
        )
        rolled_back = restore.restored
        repair_error = restore.seal_error
        relaunch_error = None if not restore.complete else relaunch_current()
        if not restore.attempted:
            # Nothing was renamed, so this is not "the rollback failed": the
            # installation is exactly as this updater found it, and the next
            # start reconciles it from the record the swap already wrote.
            outcome = (
                "The rollback was not started because it could not be recorded, so the "
                "installation is unchanged and will be repaired at the next start. "
                "It was not relaunched."
            )
        elif not rolled_back:
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
        # Plan, then journal, then move. Both of the first two refuse before
        # anything has changed, which is why their failure is answered by simply
        # reopening the version that is already installed. An update that could
        # not record what it was about to do does not get to do it: an
        # unjournalled swap is precisely the state this transaction exists to
        # make impossible.
        planned = plan_layer_swap(resources, staged_app, staged_runtime)
        begin_update_transaction(
            data_dir=data_dir,
            bundle=resolved_bundle,
            resources=resources,
            layers=planned,
            platform_name=platform_name,
        )
        # After the record, before the renames: from here on there is a copy of
        # this module outside the bundle whatever the swap leaves behind.
        stage_recovery_helper(data_dir, bundle=resolved_bundle, log=selected_logger)
    except ApplyUpdateError as exc:
        # Deliberately *not* recorded as aborted. One of the ways to arrive here
        # is that an earlier transaction is still unresolved -- which is exactly
        # the record that must keep protecting its rollback material. Writing a
        # terminal state now would decide somebody else's transaction on the
        # strength of this one having been refused.
        return abandon_before_mutation(str(exc))

    try:
        swap_staged_layers(
            resources,
            staged_app,
            staged_runtime,
            renamer=renamer,
            journal_dir=data_dir,
        )
    except ApplyUpdateError as exc:
        # The swap restored what it had moved before raising, so this
        # transaction really is decided, and it is ours to decide.
        set_journal_state(data_dir, resources, "aborted", detail=str(exc), log=selected_logger)
        return abandon_before_mutation(str(exc))

    set_journal_state(data_dir, resources, "swapped", log=selected_logger)
    if staged_runtime is not None:
        try:
            refresh_launcher_files(resources, log=log)
        except ApplyUpdateError as exc:
            return finish_failure_after_mutation(str(exc), 4)
        set_journal_state(data_dir, resources, "launchers-refreshed", log=selected_logger)

    try:
        repair_bundle(resolved_bundle, platform_name=platform_name, runner=runner, log=log)
    except ApplyUpdateError as exc:
        return finish_failure_after_mutation(str(exc), 5)
    # Installed, sealed, and not yet proven: the journal says "installed", which
    # only permits the next healthy start to reclaim the previous layers. It
    # does not reclaim anything itself.
    set_journal_state(data_dir, resources, "installed", log=selected_logger)
    if dropped:
        log(
            "The relaunch could not carry these arguments and started without "
            f"them: {' '.join(dropped)}"
        )
    log("Installed and verified the staged bundle layers.")
    try:
        process = relauncher(command, platform_name, environment=environment)
    except Exception as exc:  # noqa: BLE001 - a failed launch must restore the old version
        return finish_failure_after_mutation(
            f"Could not relaunch the updated Waveguide Generator: {type(exc).__name__}: {exc}",
            3,
        )
    failure = confirmer(process)
    if failure is not None:
        return finish_failure_after_mutation(
            f"The relaunched Waveguide Generator {failure}; it did not stay running.",
            5,
        )
    log(f"Relaunched Waveguide Generator: {' '.join(command)}")
    return 0


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
    confirm: Callable[[Any], str | None] | None = None,
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
    try:
        begin_rollback_transaction(
            data_dir=data_dir,
            bundle=bundle.resolve(),
            resources=resources,
            platform_name=platform_name,
            reason=f"the application that failed to start (pid {parent_pid}) was rolled back",
        )
    except ApplyUpdateError as exc:
        # Nothing has moved yet, so refusing costs only this attempt -- and a
        # restore nobody recorded is a restore nobody can finish.
        log(f"The rollback was not started because it could not be recorded: {exc}")
        return 3
    # Restore, re-seal, and record the end in that order -- see
    # restore_previous_generation. This helper published "rolled-back" before
    # its reseal, so a reseal that failed here left a terminal record over an
    # unsealed bundle and the next start skipped the retry.
    restore = restore_previous_generation(
        resources,
        bundle.resolve(),
        platform_name=platform_name,
        renamer=renamer,
        runner=runner,
        log=log,
        data_dir=data_dir,
    )
    if not restore.attempted:
        log(
            "The rollback was not started because the record that a restore had begun "
            "could not be written, so nothing was moved and the application was not "
            "reopened. The next start reconciles the installation from the record the "
            "update already wrote."
        )
        return 3
    if not restore.restored:
        log(
            "The rollback did not restore the previous version, so the "
            "application was not reopened. Review the entries above before "
            "changing the installation."
        )
        return 2
    if restore.seal_error is not None:
        log(
            "The previous version was restored, but the restored macOS bundle "
            f"could not be signed and verified: {restore.seal_error}. The "
            "application was not reopened."
        )
        return 4
    return _relaunch(
        bundle=bundle.resolve(),
        data_dir=data_dir,
        platform_name=platform_name,
        arguments=relaunch_arguments,
        relauncher=relauncher,
        confirm=confirm
        or (lambda process: confirm_relaunch(process, platform_name=platform_name)),
        environ=environ,
        log=log,
    )


def recover_bundle(
    *,
    bundle: Path,
    data_dir: Path,
    platform_name: str = sys.platform,
    renamer: RenameCallable = _rename,
    runner: CommandRunner = subprocess.run,
    logger: LogCallable | None = None,
) -> int:
    """Reconcile an interrupted update from outside the application.

    The recovery the desktop launcher performs needs the desktop launcher, which
    needs the ``app`` layer -- and a swap interrupted at the wrong moment is
    exactly the case where that layer is the one that is missing. This entry
    point needs neither: the module is standard library only, it declines to
    hard-fail when the shared name validator cannot be imported, and everything
    it consults is either the journal in the data directory or the bundle's own
    directories. Any Python 3.13 can run the copy in ``<data>/rollback``.
    """

    log = logger or (lambda message: append_update_log(data_dir, message))
    resolved = bundle.resolve()
    try:
        resources = resources_directory(resolved, platform_name)
    except ApplyUpdateError as exc:
        _emit_log(log, f"Recovery could not resolve the bundle {bundle}: {exc}")
        return 3
    _emit_log(log, f"Recovery helper {os.getpid()} started for {resolved}.")
    outcome = recover_transaction(
        data_dir=data_dir,
        resources=resources,
        bundle=resolved,
        platform_name=platform_name,
        renamer=renamer,
        runner=runner,
        log=log,
    )
    _emit_log(log, f"Recovery result ({outcome.action}): {outcome.detail}")
    return 3 if outcome.action == "failed" else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--staged-app-dir", type=Path)
    parser.add_argument("--staged-runtime-dir", type=Path)
    # Not required for --recover, which waits for nobody: it runs when the
    # application is not running at all, which is the state it exists for.
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="restore the .previous layers of a version that would not start",
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help="reconcile an interrupted update from its transaction journal",
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
    if args.recover:
        if args.rollback:
            parser.error("--recover decides for itself whether a rollback is needed")
        if args.staged_app_dir is not None or args.staged_runtime_dir is not None:
            parser.error("--recover reconciles installed layers and takes no staged directories")
        # Read at call time rather than inheriting the default bound when this
        # module was imported, so the entry point reports the platform it is
        # actually running on.
        #
        # --recover decides a transaction and restores layers, so it takes the
        # claim like every other path that does. It takes it here rather than
        # having its caller hold it across this process: a parent that held the
        # lock while waiting for a child that must acquire it is a deadlock, and
        # non-blocking acquisition plus a distinct exit code says the same thing
        # without one.
        resources = resources_directory(Path(args.bundle), sys.platform)
        try:
            with _claim_update(resources):
                return recover_bundle(
                    bundle=args.bundle,
                    data_dir=args.data_dir,
                    platform_name=sys.platform,
                )
        except UpdateInProgress as exc:
            append_update_log(
                Path(args.data_dir),
                f"Recovery declined: {exc}. The installation was left untouched.",
            )
            return EXIT_UPDATE_IN_PROGRESS
    if args.parent_pid is None:
        parser.error("--parent-pid is required unless --recover is given")
    if args.rollback:
        if args.staged_app_dir is not None or args.staged_runtime_dir is not None:
            parser.error("--rollback restores installed layers and takes no staged directories")
    elif args.staged_app_dir is None:
        parser.error("--staged-app-dir is required unless --rollback is given")
    # Every refusal above happens first, so an unusable command line still
    # creates nothing. Everything past this point moves a layer on purpose, so
    # it takes the installation's update claim for the whole of it: the
    # bootstrap recovery in ``bundle_recovery`` takes the same one and fails
    # closed against it, which is what stops a launcher started mid-swap from
    # renaming the directories this process is already renaming. Deliberately
    # not taken for --recover, because the bootstrap holds the claim across that
    # subprocess and a helper that re-acquired it would deadlock its own caller.
    resources = resources_directory(Path(args.bundle), sys.platform)
    try:
        with _claim_update(resources):
            return _run_transaction(args)
    except UpdateInProgress as exc:
        # Two updaters on one installation is the state this refuses to create.
        # Reported rather than raised, because this is a process the user never
        # sees and an exit code is what the caller can act on.
        append_update_log(
            Path(args.data_dir),
            f"Update declined: {exc}. The installation was left untouched.",
        )
        return EXIT_UPDATE_IN_PROGRESS


def _run_transaction(args: argparse.Namespace) -> int:
    if args.rollback:
        return rollback_bundle(
            bundle=args.bundle,
            data_dir=args.data_dir,
            parent_pid=args.parent_pid,
            relaunch_arguments=args.relaunch_args,
        )
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
