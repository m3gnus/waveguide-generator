"""Persistent solve jobs and their HTTP/WebSocket transports.

Like server.mesh and server.cadlink, re-export services lazily. Importing job
models or storage must not load the API/runtime assembly, which itself depends
on solver consumers of those models and storage.
"""

from typing import TYPE_CHECKING

from server._lazy_exports import lazy_exports


if TYPE_CHECKING:
    from .api import mount_jobs
    from .runtime import JobRuntime
    from .store import JobStore


_EXPORTS = {
    "JobRuntime": ".runtime",
    "JobStore": ".store",
    "mount_jobs": ".api",
}


__getattr__, __dir__ = lazy_exports(globals(), _EXPORTS)


__all__ = ["JobRuntime", "JobStore", "mount_jobs"]
