"""Take a file by renaming it, so that at most one taker ever wins.

The live claim and the Fusion add-in's file claim both take a request the same
way: rename it to a private name, and whoever renamed it has it. That rests on
the rename being an exclusive decision, and on Windows ``os.rename`` is not one.

``os.rename`` is ``MoveFileExW``, which opens the source *by name* and then
renames the open handle. Its open shares delete access, so two takers that both
open the source before either renames each get a handle to the same file, and
both renames succeed: the second moves the file away from wherever the first
had just put it. Measured on a Windows 11 VM on 2026-10-03, two threads calling
``os.rename`` on one source with different targets both returned success in
1,999 rounds of 2,000, leaving one file. A live claim that "won" that way then
reads its private name and finds nothing, or holds a request the add-in has
also taken -- the request is delivered twice or put back after being claimed.

The fix is on this side only, because the pinned add-in is out of reach:
open the source with delete access while sharing *no* delete access, then
rename through that handle. Nobody can open the file for a rename while the
handle is held, and if someone already has it open for one, this open fails
with a sharing violation instead. Either way the two renames cannot overlap.
The add-in's own claim treats any ``OSError`` from its rename as "not taken
this pass" and tries again later, so a sharing violation on its side is safe.

A sharing violation on *this* side means another taker is mid-rename (or a
scanner has the file briefly open); waiting a moment answers which: the file
is then gone, or free to open.

POSIX ``rename(2)`` already makes the decision exclusive -- of two renames of
one source, exactly one finds it -- so there this is ``os.rename``. It would
replace an existing target, though, so the target is checked first. That
check is advisory (check, then rename), not atomic as on Windows; it is enough
because every caller's target is a private name unique to its claim, or is
written only under ``fusion_delivery._LOCK``.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

#: How long to wait for another opener before giving up with ``PermissionError``.
SHARING_WAIT_SECONDS = 2.0

_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ACCESS_DENIED = 5  # also what a delete-pending or briefly-held file answers
_ERROR_SHARING_VIOLATION = 32
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183


def take_by_rename(source: Path, target: Path, *, wait_seconds: float = SHARING_WAIT_SECONDS) -> None:
    """Rename ``source`` to ``target`` unless another taker has it.

    Raises ``FileNotFoundError`` when ``source`` is gone (someone else took it),
    ``PermissionError`` when it stayed held by another opener for
    ``wait_seconds``, ``FileExistsError`` when ``target`` exists (it is not
    replaced; on POSIX that check is advisory, see the module docstring), and
    ``OSError`` for anything else.
    """

    if sys.platform != "win32":
        if os.path.lexists(target):
            raise FileExistsError(17, os.strerror(17), str(target))
        os.rename(source, target)
        return
    _take_by_rename_windows(Path(source), Path(target), wait_seconds)


def _extended(path: Path) -> str:
    """The absolute extended-length form, so a path past MAX_PATH still opens."""

    prefix = "\\\\?\\"
    text = os.path.abspath(str(path))
    if text.startswith(prefix):
        return text
    if text.startswith("\\\\"):
        return prefix + "UNC\\" + text[2:]
    return prefix + text


def _take_by_rename_windows(source: Path, target: Path, wait_seconds: float) -> None:
    import ctypes
    from ctypes import wintypes

    class FileRenameInfo(ctypes.Structure):
        # FILE_RENAME_INFO. The first member is a union of BOOLEAN
        # ReplaceIfExists and DWORD Flags; zero is "never replace". On x64
        # RootDirectory is 8-aligned, so FileName starts at byte 20 and
        # sizeof() is 24. FileNameLength is in bytes and excludes the NUL.
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    set_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]

    delete, synchronize, read_attributes = 0x00010000, 0x00100000, 0x0080
    share_read_only = 0x00000001  # deliberately no FILE_SHARE_DELETE (0x4)
    open_existing = 3
    open_reparse_point = 0x00200000
    file_rename_info_class = 3
    invalid = wintypes.HANDLE(-1).value

    source_name = _extended(source)
    deadline = time.monotonic() + wait_seconds
    while True:
        handle = create_file(
            source_name, delete | synchronize | read_attributes, share_read_only,
            None, open_existing, open_reparse_point, None,
        )
        if handle not in (None, invalid):
            break
        error = ctypes.get_last_error()
        if error in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
            raise FileNotFoundError(2, os.strerror(2), str(source))
        if error in (_ERROR_SHARING_VIOLATION, _ERROR_ACCESS_DENIED):
            if time.monotonic() < deadline:
                time.sleep(0.001)
                continue
            raise PermissionError(13, f"{source} stayed held by another process (Windows error {error})", str(source))
        raise ctypes.WinError(error)
    try:
        # UTF-16 code units, not code points: a character outside the BMP is
        # a surrogate pair, and counting len(name) would cut the name short.
        # surrogatepass keeps an unpaired surrogate (legal in an NTFS name) as
        # the unit Windows has, as CreateFileW receives it for the source.
        encoded = _extended(target).encode("utf-16-le", "surrogatepass")
        name_bytes = len(encoded)
        size = ctypes.sizeof(FileRenameInfo) + name_bytes
        buffer = ctypes.create_string_buffer(size)
        info = FileRenameInfo.from_buffer(buffer)
        info.Flags = 0
        info.RootDirectory = None
        info.FileNameLength = name_bytes
        # The name's exact UTF-16 bytes, then a NUL unit; sizeof() already
        # holds the struct's one WCHAR, so the buffer has room for it.
        ctypes.memmove(
            ctypes.addressof(buffer) + FileRenameInfo.FileName.offset,
            encoded + b"\0\0",
            name_bytes + 2,
        )
        if not set_information(handle, file_rename_info_class, buffer, size):
            error = ctypes.get_last_error()
            if error in (_ERROR_ALREADY_EXISTS, _ERROR_FILE_EXISTS):
                raise FileExistsError(17, os.strerror(17), str(target))
            raise ctypes.WinError(error)
    finally:
        close_handle(handle)
