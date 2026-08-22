"""Validate file names that arrive inside downloaded update archives.

Both the archive extractor and the Windows launcher refresh copy files whose
names come from a manifest or a ZIP directory, which is untrusted input even
when it is signed by nothing worse than a checksum. The rules here are
deliberately Windows-strict on every platform: a macOS build must reject a name
that would be dangerous on Windows, or the two platforms disagree about what a
release contains, and the tests would only catch it on one of them.
"""

from __future__ import annotations

import os.path


#: Names that address a device rather than a file on Windows, with or without
#: an extension. ``os.path.isreserved`` knows these on 3.13, but only when it
#: runs on Windows, so the set is spelled out for host-independent validation.
_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{digit}" for digit in "123456789¹²³"}
    | {f"LPT{digit}" for digit in "123456789¹²³"}
)

_SEPARATORS = ("/", "\\")


class UnsafeName(ValueError):
    """A manifest or archive named something that must never be written."""


def is_reserved_windows_name(name: str) -> bool:
    """Report whether *name* addresses a Windows device rather than a file."""

    stem = name.partition(".")[0].rstrip(" ").upper()
    if stem in _RESERVED_STEMS:
        return True
    # Ask the platform too: it is authoritative where it applies, and it keeps
    # this in step with names a future Windows release adds.
    isreserved = getattr(os.path, "isreserved", None)
    if isreserved is not None and os.name == "nt":
        try:
            return bool(isreserved(name))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return False
    return False


def validate_relative_name(name: object, *, what: str = "name") -> str:
    """Return *name* if it is a plain file name that is safe on any platform.

    Rejects anything that is not a single component, anything addressing an
    NTFS alternate data stream or a device, and the trailing dot and space that
    Windows silently strips -- ``"app."`` and ``"app "`` both open ``app``, so a
    name that looks new can quietly alias an existing directory.
    """

    if not isinstance(name, str) or not name:
        raise UnsafeName(f"{what} must be a non-empty string, not {name!r}")
    if name in {".", ".."}:
        raise UnsafeName(f"{what} must not be a relative directory: {name!r}")
    if any(separator in name for separator in _SEPARATORS):
        raise UnsafeName(f"{what} must be a single path component: {name!r}")
    if ":" in name:
        # A colon is a drive separator in the first component and an alternate
        # data stream everywhere else; neither is a file this may write.
        raise UnsafeName(f"{what} must not address a drive or data stream: {name!r}")
    if any(character < " " or character == "\x7f" for character in name):
        raise UnsafeName(f"{what} must not contain control characters: {name!r}")
    if name[-1] in {".", " "}:
        raise UnsafeName(f"{what} must not end in a dot or space: {name!r}")
    if is_reserved_windows_name(name):
        raise UnsafeName(f"{what} must not be a reserved device name: {name!r}")
    return name


def collision_key(name: str) -> str:
    """Return the key two names share when a filesystem treats them as one.

    Windows and the default macOS filesystem are both case-insensitive, so
    ``APP`` and ``app`` are the same directory. Callers use this to reject a
    manifest that names one destination twice, or that aliases a directory it
    has no business touching.
    """

    return name.casefold()
