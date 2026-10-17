"""Path spellings shared by the installer client and its handoff.

Standard library only, so the launcher can import it before the server layer.
"""

from __future__ import annotations

import os
from pathlib import Path


def ordinary_path(value: str) -> str:
    """Use ordinary Win32 spelling for path identities; leave POSIX bytes alone.

    The embedded pre-import counterpart is ``windows/startup_hook.py``'s
    ``_ordinary_path``; keep its drive/UNC conversion in agreement with this.
    """
    if os.name == "nt":
        if (value[:8].upper() == "\\\\?\\UNC\\"
                and len(parts := value[8:].split("\\")) >= 2
                and parts[0] and parts[1]):
            return "\\\\" + value[8:]
        if (value.startswith("\\\\?\\") and len(value) >= 7
                and value[4] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                and value[5:7] == ":\\"):
            return value[4:]
    return value


def resolved_path(value: str | os.PathLike[str], *, strict: bool = False) -> Path:
    """Resolve links, then use plain spelling for Windows drive/UNC paths."""
    return Path(ordinary_path(str(Path(value).resolve(strict=strict))))
