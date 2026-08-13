"""Persistent CAD-link identity and ingestion APIs, exported lazily."""

from typing import TYPE_CHECKING


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


def __getattr__(name: str) -> object:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})

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
