"""Persistent CAD-link identity and ingestion APIs, exported lazily."""

from typing import TYPE_CHECKING

from server._lazy_exports import lazy_exports


if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .api import mount_cadlink
    from .identity import CadLink, OpenClassification, SaveIdentity, classify_open, design_hash
    from .onshape.api import mount_onshape
    from .store import CadLinkStore


_EXPORTS = {
    "mount_cadlink": ".api",
    "mount_onshape": ".onshape.api",
    "CadLink": ".identity",
    "OpenClassification": ".identity",
    "SaveIdentity": ".identity",
    "classify_open": ".identity",
    "design_hash": ".identity",
    "CadLinkStore": ".store",
}


__getattr__, __dir__ = lazy_exports(globals(), _EXPORTS)

__all__ = [
    "CadLink",
    "CadLinkStore",
    "OpenClassification",
    "SaveIdentity",
    "classify_open",
    "design_hash",
    "mount_cadlink",
    "mount_onshape",
]
