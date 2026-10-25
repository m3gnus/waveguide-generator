"""Persistent solve jobs and their HTTP/WebSocket transports.

Like server.mesh and server.cadlink, re-export services lazily. Importing job
models or storage must not load the API/runtime assembly, which itself depends
on solver consumers of those models and storage.
"""

from importlib import import_module
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .api import mount_jobs
    from .runtime import JobRuntime
    from .store import JobStore


_EXPORTS = {
    "JobRuntime": ".runtime",
    "JobStore": ".store",
    "mount_jobs": ".api",
}


def __getattr__(name: str) -> object:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})


__all__ = ["JobRuntime", "JobStore", "mount_jobs"]
