"""Staging directories for files the app publishes where someone will read them.

Writing straight to the destination is not an option: a reader that arrives
mid-write finds a truncated file, and a failed write leaves one behind. The
answer everywhere in this codebase is to stage the bytes beside the destination
and `os.replace` them into place, which is atomic on both platforms.

`tempfile.mkdtemp` is the obvious way to make that staging directory, and on
Windows it is the wrong one. CPython gives the directory mode `0o700`, and
implements that as a security descriptor granting SYSTEM, Administrators and
OWNER RIGHTS -- with no inheritance from the parent. Files created inside pick
it up, and `MoveFileEx` carries a file's DACL with it rather than letting the
destination directory's inheritable entries apply. So every file published this
way lands with a private ACL naming nobody but whoever owned the writing
process:

    NT AUTHORITY\\SYSTEM:(F)  BUILTIN\\Administrators:(F)  OWNER RIGHTS:(F)

That is invisible while the owner stays the same, and OWNER RIGHTS keeps it
readable to the account that wrote it. It stops being invisible the moment the
owner changes -- one elevated run, a different account, a machine where UAC was
switched on between sessions. The files from before are then unreadable to the
app, and it cannot even read their ACL to say why.

The run archive is where that surfaces first, because it is the one place the
app reads a file it published earlier: `design.json`, the pointer naming which
design a run folder belongs to. `_write_export_sync` reads it back to check the
incoming archive belongs to the same lineage, gets `PermissionError`, and
refuses to overwrite a file it can no longer prove anything about. The guard is
right; the file should never have been written that way.

A plain `mkdir` in the same parent inherits that parent's ACL, which is what the
published file should have carried from the start. It gives nothing up: these
staging directories are always created inside the destination's own parent --
the user's workspace or the app's data directory, never a shared temp root --
so the location already provides whatever privacy `0o700` was buying, and POSIX
keeps the tight mode anyway (see below).
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

__all__ = ["publish_staging_directory"]

# A collision needs two callers to draw the same 96 random bits in the same
# directory. The retries are here so that a collision is a retry rather than a
# failed export, not because one is expected.
_NAME_ATTEMPTS = 8


def publish_staging_directory(parent: Path | str, prefix: str) -> Path:
    """Create an empty staging directory under `parent`, inheriting its ACL.

    The replacement for `tempfile.mkdtemp(prefix=prefix, dir=parent)` at every
    site whose staged files are `os.replace`d somewhere a user, or a later run
    of the app, has to be able to read. See this module's docstring for why the
    two are not interchangeable on Windows.
    """

    parent = Path(parent)
    for _ in range(_NAME_ATTEMPTS):
        candidate = parent / f"{prefix}{secrets.token_hex(6)}"
        try:
            # No mode argument, deliberately. On Windows that is the whole
            # point: the directory takes the parent's inheritable entries
            # instead of a private descriptor, and so do the files staged in it.
            candidate.mkdir()
        except FileExistsError:
            continue
        if os.name != "nt":
            # POSIX loses nothing by keeping `mkdtemp`'s privacy. Mode bits do
            # not travel with a rename there -- the published file keeps the
            # mode `write_bytes` gave it -- so tightening the directory costs
            # the destination nothing and narrows the window in which a staged
            # file is visible to anyone who can reach the parent.
            candidate.chmod(0o700)
        return candidate
    raise OSError(f"Could not create a staging directory under {parent}")
