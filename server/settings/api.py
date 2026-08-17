"""REST surface for durable interface settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException

from server.settings.store import SettingsError, SettingsStore


def create_settings_router(store: SettingsStore) -> APIRouter:
    router = APIRouter(prefix="/api/settings", tags=["settings"])

    @router.get("")
    async def read_settings() -> dict[str, Any]:
        return store.envelope()

    @router.put("/{namespace}")
    async def write_namespace(
        namespace: str, value: Any = Body(...)
    ) -> dict[str, Any]:
        try:
            return store.put(namespace, value)
        except SettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Could not persist settings: {exc}"
            ) from exc

    @router.delete("/{namespace}")
    async def delete_namespace(namespace: str) -> dict[str, Any]:
        try:
            return store.delete(namespace)
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


__all__ = ["create_settings_router", "mount_settings"]
