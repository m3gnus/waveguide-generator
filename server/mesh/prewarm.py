"""Import the mesher's hot module graph before the first interaction needs it.

Every mesher entry point in this server imports lazily, so a missing or broken
optional mesher degrades to a clear per-request error instead of a dead server.
The cost is that somebody pays the import, and that somebody was whoever
touched a control first: ``POST /api/design/symmetry`` measured 1152 ms on the
first call against a running server and 57-150 ms on every call after.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import importlib
import logging

from server.platform.warmup import BackgroundWarmup


log = logging.getLogger("wg.mesh")

#: Modules on the first-interaction path: symmetry resolution
#: (``server/solver/symmetry.py``), live preview (``server/preview/core.py``),
#: viewport export (``server/exports/core.py``) and mesh integrity
#: (``server/mesh/integrity.py``).
PREWARM_MODULES: tuple[str, ...] = (
    "hornlab_mesher.config_builder",
    "hornlab_mesher.preview.api",
    "hornlab_mesher.viewport",
    "hornlab_mesher.normals",
    "hornlab_mesher.tags",
    # The interpolating acoustic surface fit (``translate.acoustic_surface_fit``)
    # imports ``scipy.interpolate`` lazily inside the fit itself, so without this
    # the very first solve or export mesh of a process pays 0.30 s that has
    # nothing to do with the geometry. Warm, that fit is 0.386 s against the
    # compatibility fit's 0.443 s on a stock OSSE -- it is the cold import, not
    # the fit, that is expensive.
    "scipy.interpolate",
)


def import_mesher_modules(modules: Sequence[str] = PREWARM_MODULES) -> list[str]:
    """Import each module, tolerating an absent or broken optional mesher.

    A failure here is never fatal: the lazy import at the real call site raises
    the error that actually explains what the user should do about it.
    """

    imported: list[str] = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:
            log.info("Mesher prewarm skipped %s: %s", name, exc)
            continue
        imported.append(name)
    return imported


async def _import_off_thread() -> None:
    imported = await asyncio.to_thread(import_mesher_modules)
    log.info("Mesher prewarm imported %d of %d modules", len(imported), len(PREWARM_MODULES))


#: The process-wide warmup. Exported so tests can observe it without reaching
#: through a private name; there is exactly one mesher import graph per process.
mesher_warmup = BackgroundWarmup("mesher-import", _import_off_thread)


async def prewarm_mesher() -> None:
    """Start warming in the background.  This must never delay startup."""

    await mesher_warmup.start()


async def shutdown_mesher_prewarm() -> None:
    """Finish a warmup that is still running when the server stops."""

    await mesher_warmup.stop()


__all__ = [
    "PREWARM_MODULES",
    "import_mesher_modules",
    "mesher_warmup",
    "prewarm_mesher",
    "shutdown_mesher_prewarm",
]
