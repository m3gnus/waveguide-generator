"""Platform-safe options for silent background child processes."""

from __future__ import annotations

import platform
import subprocess


def background_process_kwargs(*, system: str | None = None) -> dict[str, int]:
    """Hide a child console on Windows while leaving GUI windows visible.

    WG's backend is started without a console.  Console executables such as
    ``tasklist``, ``git`` and ``powershell`` otherwise create a flashing CMD
    window each time they are polled.  ``CREATE_NO_WINDOW`` suppresses that
    console without suppressing an intentional child GUI such as a folder
    picker or Explorer.
    """

    resolved = platform.system() if system is None else system
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if resolved == "Windows" else 0
    return {"creationflags": flags} if flags else {}


__all__ = ["background_process_kwargs"]
