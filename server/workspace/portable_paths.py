"""Portable path validation shared by allocation and the workspace writer."""

import re
import unicodedata

_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def path_segments(raw: str, label: str) -> list[str]:
    if raw.startswith(("/", "\\")) or _WINDOWS_DRIVE.match(raw):
        raise ValueError(f"{label} must be a relative path")
    segments = re.split(r"[\\/]", raw)
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(f"{label} contains an empty, '.' or '..' path segment")
    for segment in segments:
        if any(character in '<>:"|?*' for character in segment):
            raise ValueError(f"{label} contains an invalid path character")
        if len(segment.encode("utf-8")) > 255:
            raise ValueError(f"{label} contains a path segment exceeding the 255-byte limit")
        if segment.endswith((".", " ")):
            raise ValueError(f"{label} contains a segment ending in a dot or space")
        if any(unicodedata.category(character) == "Cc" for character in segment):
            raise ValueError(f"{label} contains a control character")
        if _WINDOWS_DEVICE_NAME.fullmatch(unicodedata.normalize("NFKC", segment)):
            raise ValueError(f"{label} contains reserved Windows device name {segment!r}")
    return segments
