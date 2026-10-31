"""Revision-bound geometry export REST surface, exported lazily."""

from typing import TYPE_CHECKING

from server._lazy_exports import lazy_exports


if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .api import mount_exports


__getattr__, __dir__ = lazy_exports(globals(), {"mount_exports": ".api"})

__all__ = ["mount_exports"]
