"""Repair files this app published with a private, non-inheriting ACL.

`server/platform/staging.py` explains how they came to exist: `tempfile.mkdtemp`
gives its directory mode `0o700`, Windows implements that as a security
descriptor granting SYSTEM, Administrators and OWNER RIGHTS with inheritance
switched off, files staged inside pick it up, and `os.replace` carries a file's
DACL to the destination rather than letting the destination directory's
inheritable entries apply. `publish_staging_directory` stopped that happening.
It does nothing for the files it already happened to.

Those files stay broken, and they stay *invisibly* broken for as long as the
owner does not change -- OWNER RIGHTS grants full control to whoever owns the
object, so the account that wrote them can still read them. The moment the owner
changes, which one elevated run or one UAC reboot is enough to do, the app can
no longer read files it wrote itself, and the run archive's `design.json` is
where that surfaces first.

**This module resets, and never deletes.** A user's run archive is their data.
The repair is to hand the object back its parent's inheritance -- the same
entries it should have carried from the start -- which is a change to the
access-control list alone and leaves every byte of content untouched.

## What it will and will not touch

Only a descriptor matching the exact shape staging left behind is repaired:

- the DACL is *protected*, i.e. inheritance from the parent is switched off;
- it holds exactly three access-allowed entries, for `SYSTEM` (`S-1-5-18`),
  `Administrators` (`S-1-5-32-544`) and `OWNER RIGHTS` (`S-1-3-4`);
- each grants exactly `FILE_ALL_ACCESS`;
- none is inherited, and none carries a flag beyond the two inheritance bits a
  directory needs. A file left by staging carries no flags; a directory carries
  `OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE`. Both were measured on a real
  affected install.

Anything else -- a descriptor a user tightened deliberately, a different set of
principals, a narrower mask -- is left exactly as it is. The point of matching
the pattern this precisely is that the app must never be the thing that widens
access to a file somebody meant to restrict.

## The limit, which is real and is not worked around here

Resetting a DACL needs `WRITE_DAC`, and reading one needs `READ_CONTROL`. An
object's owner holds both implicitly, which is why this works at all: on a
machine where the owner is unchanged, every poisoned file is repairable.

Where the owner *has* changed -- which is exactly the case that made the problem
visible -- an unelevated process holds neither right, and both the read and the
write fail with `ERROR_ACCESS_DENIED`. Measured on an affected install: of 53
poisoned paths under one workspace, 22 were repairable and 31 could not even
have their descriptor read.

Recovering those needs the owner's privileges: running the app once as an
administrator repairs them through this same code path, because an elevated
token has the Administrators SID enabled and the descriptor names
Administrators. **Taking ownership is deliberately not attempted.** It is a
privileged act on files the app cannot prove anything about, and the honest
answer is to report the count and say what would fix it.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from enum import Enum
import logging
import os
from pathlib import Path

log = logging.getLogger("wg.acl")

__all__ = [
    "Outcome",
    "process_is_elevated",
    "RepairCounts",
    "descriptor_is_poisoned",
    "repair_path",
    "sweep",
]

WINDOWS = os.name == "nt"

# The three principals `mkdtemp`'s 0o700 descriptor names, and nothing else.
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
OWNER_RIGHTS_SID = "S-1-3-4"
POISONED_SIDS = frozenset({SYSTEM_SID, ADMINISTRATORS_SID, OWNER_RIGHTS_SID})

FILE_ALL_ACCESS = 0x001F01FF
OBJECT_INHERIT_ACE = 0x01
CONTAINER_INHERIT_ACE = 0x02
INHERITED_ACE = 0x10
ACCESS_ALLOWED_ACE_TYPE = 0

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x00000004
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
_SE_DACL_PROTECTED = 0x1000
_ACL_REVISION = 2
_ERROR_SUCCESS = 0

# An empty ACL needs room for its header only; this is generous and fixed, so
# the buffer can never be the thing that fails.
_EMPTY_ACL_BYTES = 256


class Outcome(str, Enum):
    """What happened to one path."""

    NOT_APPLICABLE = "not_applicable"   # POSIX, where none of this exists
    NOT_POISONED = "not_poisoned"       # descriptor read, pattern did not match
    REPAIRED = "repaired"
    UNREADABLE = "unreadable"           # no READ_CONTROL: owner changed
    FAILED = "failed"                   # matched, but the reset was refused


@dataclass(frozen=True, slots=True)
class RepairCounts:
    """Totals for one sweep, reported rather than summarised away."""

    scanned: int = 0
    repaired: int = 0
    skipped: int = 0
    unreadable: int = 0
    failed: int = 0
    truncated: bool = False

    def as_log_fields(self) -> str:
        return (
            f"scanned={self.scanned} repaired={self.repaired} "
            f"skipped={self.skipped} unreadable={self.unreadable} "
            f"failed={self.failed}"
        )


if WINDOWS:  # pragma: no cover - exercised only on Windows
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", ctypes.c_ubyte),
            ("Sbz1", ctypes.c_ubyte),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    class _ACCESS_ALLOWED_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            # The SID is variable-length and starts here; the struct declares
            # its first DWORD so the offset is something ctypes can compute.
            ("SidStart", wintypes.DWORD),
        ]

    # Without prototypes ctypes assumes every argument and the return value is
    # an int, which is wrong for pointers on 64-bit and silently truncates them.
    _advapi32.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD

    _advapi32.SetNamedSecurityInfoW.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD

    _advapi32.GetSecurityDescriptorControl.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    _advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL

    _advapi32.GetAce.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi32.GetAce.restype = wintypes.BOOL

    _advapi32.ConvertSidToStringSidW.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    )
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    _advapi32.InitializeAcl.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _advapi32.InitializeAcl.restype = wintypes.BOOL

    _kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    _kernel32.LocalFree.restype = ctypes.c_void_p


@dataclass(frozen=True, slots=True)
class AccessEntry:
    """One access-allowed entry, reduced to what the pattern match needs."""

    sid: str
    mask: int
    flags: int
    ace_type: int


def read_dacl(path: Path | str) -> tuple[bool, list[AccessEntry]]:
    """Return (dacl_is_protected, entries) for `path`.

    Raises `OSError` when the descriptor cannot be read, which on an affected
    install is the ordinary case rather than an exceptional one -- see the
    module docstring.
    """

    if not WINDOWS:
        raise OSError("Security descriptors are a Windows concept")

    descriptor = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    error = _advapi32.GetNamedSecurityInfoW(
        str(path),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if error != _ERROR_SUCCESS:
        raise OSError(
            0, f"Cannot read the security descriptor ({error})", str(path), error
        )
    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not _advapi32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        protected = bool(control.value & _SE_DACL_PROTECTED)

        entries: list[AccessEntry] = []
        if dacl:
            header = ctypes.cast(dacl, ctypes.POINTER(_ACL)).contents
            for index in range(header.AceCount):
                ace_pointer = ctypes.c_void_p()
                if not _advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                    raise ctypes.WinError(ctypes.get_last_error())
                ace_header = ctypes.cast(
                    ace_pointer, ctypes.POINTER(_ACE_HEADER)
                ).contents
                ace = ctypes.cast(
                    ace_pointer, ctypes.POINTER(_ACCESS_ALLOWED_ACE)
                ).contents
                sid_pointer = ctypes.c_void_p(
                    ctypes.addressof(ace) + _ACCESS_ALLOWED_ACE.SidStart.offset
                )
                text = wintypes.LPWSTR()
                if not _advapi32.ConvertSidToStringSidW(
                    sid_pointer, ctypes.byref(text)
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    sid = text.value or ""
                finally:
                    _kernel32.LocalFree(text)
                entries.append(
                    AccessEntry(
                        sid=sid,
                        mask=int(ace.Mask),
                        flags=int(ace_header.AceFlags),
                        ace_type=int(ace_header.AceType),
                    )
                )
        return protected, entries
    finally:
        _kernel32.LocalFree(descriptor)


def entries_match_staging_pattern(
    protected: bool, entries: list[AccessEntry], *, is_directory: bool
) -> bool:
    """Whether this descriptor is one staging left behind, and nothing else.

    Deliberately exact. Every clause here is a way for a descriptor that merely
    resembles the broken one to be left alone, because widening access to a file
    somebody restricted on purpose is a worse failure than declining to repair
    one this app broke.
    """

    if not protected:
        # Inheritance is already on: whatever else is true, this is not the
        # descriptor that loses a file to an ownership change.
        return False
    if len(entries) != 3:
        return False
    if {entry.sid for entry in entries} != POISONED_SIDS:
        return False
    allowed_flags = (
        OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE if is_directory else 0
    )
    for entry in entries:
        if entry.ace_type != ACCESS_ALLOWED_ACE_TYPE:
            return False
        if entry.mask != FILE_ALL_ACCESS:
            return False
        if entry.flags & INHERITED_ACE:
            # A protected DACL should hold no inherited entry at all. If one is
            # here the descriptor is not what this module thinks it is.
            return False
        if entry.flags & ~allowed_flags:
            return False
    return True


def descriptor_is_poisoned(path: Path | str) -> bool:
    """Whether `path` carries the staging descriptor. Raises on an unreadable one."""

    path = Path(path)
    protected, entries = read_dacl(path)
    return entries_match_staging_pattern(
        protected, entries, is_directory=path.is_dir()
    )


def _reset_to_inherit(path: Path) -> None:
    """Hand the object back its parent's inheritance.

    An *empty* DACL is passed rather than a NULL one. NULL would mean "no DACL",
    which grants everyone full access -- the opposite of the intent. Empty plus
    `UNPROTECTED` means the resulting list is the parent's inheritable entries
    and nothing besides.
    """

    buffer = ctypes.create_string_buffer(_EMPTY_ACL_BYTES)
    if not _advapi32.InitializeAcl(buffer, _EMPTY_ACL_BYTES, _ACL_REVISION):
        raise ctypes.WinError(ctypes.get_last_error())
    error = _advapi32.SetNamedSecurityInfoW(
        str(path),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION | _UNPROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.cast(buffer, ctypes.c_void_p),
        None,
    )
    if error != _ERROR_SUCCESS:
        raise OSError(
            0, f"Cannot reset the security descriptor ({error})", str(path), error
        )


def repair_path(path: Path | str) -> Outcome:
    """Repair one path if -- and only if -- it carries the staging descriptor."""

    if not WINDOWS:
        return Outcome.NOT_APPLICABLE
    path = Path(path)
    try:
        poisoned = descriptor_is_poisoned(path)
    except OSError as exc:
        log.debug("Cannot read the descriptor for %s: %s", path, exc)
        return Outcome.UNREADABLE
    if not poisoned:
        return Outcome.NOT_POISONED
    try:
        _reset_to_inherit(path)
    except OSError as exc:
        log.debug("Cannot repair %s: %s", path, exc)
        return Outcome.FAILED
    return Outcome.REPAIRED


def process_is_elevated() -> bool:
    """Whether this process runs with the Administrators SID enabled.

    It is the one condition that changes whether a descriptor whose owner has
    changed can be repaired at all, so the boot sweep records it and uses it to
    decide whether re-examining a damaged root could produce a different answer.
    """

    if not WINDOWS:
        return False
    try:
        return bool(ctypes.WinDLL("shell32", use_last_error=True).IsUserAnAdmin())
    except OSError:  # pragma: no cover - shell32 is always present in practice
        return False


def sweep(root: Path | str, *, limit: int = 20_000) -> RepairCounts:
    """Repair every poisoned path under `root`, and report what happened.

    `limit` bounds the walk. A workspace is a folder the user chose, so it can
    be far larger than anything this app wrote, and a boot-time sweep must have
    a ceiling it cannot be argued out of. Hitting it is reported, not hidden:
    the on-encounter repair still covers whatever the walk did not reach.

    Symlinks and junctions are not followed. Repairing through one would let a
    link inside the workspace redirect this at a tree the user never pointed it
    at, and the app publishes no reparse points of its own.
    """

    root = Path(root)
    if not WINDOWS:
        return RepairCounts()
    scanned = repaired = skipped = unreadable = failed = 0
    truncated = False

    pending: list[Path] = [root]
    while pending:
        current = pending.pop()
        if scanned >= limit:
            truncated = True
            break
        scanned += 1
        outcome = repair_path(current)
        if outcome is Outcome.REPAIRED:
            repaired += 1
        elif outcome is Outcome.UNREADABLE:
            unreadable += 1
        elif outcome is Outcome.FAILED:
            failed += 1
        else:
            skipped += 1
        try:
            if current.is_dir() and not current.is_symlink():
                pending.extend(current.iterdir())
        except OSError:
            # A directory whose listing is refused is counted by whatever its
            # own descriptor said; there is nothing further to do with it.
            continue

    return RepairCounts(
        scanned=scanned,
        repaired=repaired,
        skipped=skipped,
        unreadable=unreadable,
        failed=failed,
        truncated=truncated,
    )
