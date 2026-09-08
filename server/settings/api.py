"""REST surface for durable interface settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, FastAPI, Header, HTTPException

from server.settings.store import SettingsError, SettingsStore, StaleWriteError


#: A page can have two writes for one namespace in flight at once, because
#: closing the window sends the pending value immediately rather than behind
#: the request queue.  These identify the page and order its own writes, so a
#: request the network delivered late cannot overwrite the value that
#: superseded it.  Both are optional: a client that sends neither is written
#: unconditionally, exactly as before they existed.
WRITER_HEADER = "X-WG-Settings-Writer"
SEQUENCE_HEADER = "X-WG-Settings-Seq"

#: A refused write is a conflict, not a bad request: the payload was fine and
#: one of the sender's own later writes has already been stored.
STALE_WRITE_RESPONSE: dict[int | str, dict[str, Any]] = {
    409: {"description": "A later write from the same writer is already stored"},
}


def create_settings_router(store: SettingsStore) -> APIRouter:
    router = APIRouter(prefix="/api/settings", tags=["settings"])

    @router.get("")
    async def read_settings() -> dict[str, Any]:
        return store.envelope()

    @router.put("/{namespace}", responses=STALE_WRITE_RESPONSE)
    async def write_namespace(
        namespace: str,
        value: Any = Body(...),
        writer: str | None = Header(default=None, alias=WRITER_HEADER),
        sequence: int | None = Header(default=None, alias=SEQUENCE_HEADER),
    ) -> dict[str, Any]:
        try:
            return store.put(namespace, value, writer=writer, sequence=sequence)
        except StaleWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Could not persist settings: {exc}"
            ) from exc

    @router.delete("/{namespace}", responses=STALE_WRITE_RESPONSE)
    async def delete_namespace(
        namespace: str,
        writer: str | None = Header(default=None, alias=WRITER_HEADER),
        sequence: int | None = Header(default=None, alias=SEQUENCE_HEADER),
    ) -> dict[str, Any]:
        try:
            return store.delete(namespace, writer=writer, sequence=sequence)
        except StaleWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Could not persist settings: {exc}"
            ) from exc

    return router


def mount_settings(application: FastAPI) -> SettingsStore:
    store = SettingsStore(Path(application.state.data_dir))
    application.state.settings = store
    application.include_router(create_settings_router(store))
    return store


__all__ = [
    "SEQUENCE_HEADER",
    "WRITER_HEADER",
    "create_settings_router",
    "mount_settings",
]
