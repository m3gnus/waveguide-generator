"""Backend-managed output workspace."""

from typing import TYPE_CHECKING

from server._lazy_exports import lazy_exports

if TYPE_CHECKING:
    from .api import mount_workspace

__getattr__, __dir__ = lazy_exports(globals(), {"mount_workspace": ".api"})

__all__ = ["mount_workspace"]
