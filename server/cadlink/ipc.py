"""Machine-local WGLink IPC paths and the legacy solve-command ledger."""

from __future__ import annotations

from pathlib import Path

from server.platform.private_paths import ensure_private_directory


IPC_SUBDIRECTORY = Path("ipc") / "wglink"
# The JSON outcome ledger of earlier versions. The store imports it once and
# renames it; nothing reads it after that.
LEDGER_FILENAME = "solve-commands.json"


def ipc_folder(data_dir: Path, *, create: bool = False) -> Path:
    # Harden through the path as configured, so a symlinked data root is seen
    # and left alone; hand callers the resolved folder, as before.
    folder = Path(data_dir) / IPC_SUBDIRECTORY
    if create:
        ensure_private_directory(folder, parents=True, data_root=Path(data_dir))
    return folder.resolve()



def legacy_ledger_path(data_dir: Path) -> Path:
    """Where earlier versions kept terminal solve-command outcomes."""

    return ipc_folder(Path(data_dir)) / LEDGER_FILENAME



__all__ = ["IPC_SUBDIRECTORY", "LEDGER_FILENAME", "ipc_folder", "legacy_ledger_path"]
