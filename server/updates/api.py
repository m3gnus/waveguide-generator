"""FastAPI mounting for release update status."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Query, status

from server.settings.store import SettingsStore

from .service import UpdateChannelUnavailable, UpdateInstallUnavailable, UpdateService


def mount_updates(
    application: FastAPI,
    *,
    running_version: str,
    data_dir: Path,
    repo_root: Path,
    update_request_path: Path | None = None,
    service: UpdateService | None = None,
    settings: SettingsStore | None = None,
) -> UpdateService:
    update_service = service or UpdateService(
        running_version=running_version,
        data_dir=data_dir,
        repo_root=repo_root,
        update_request_path=update_request_path,
        settings=settings,
    )
    application.state.update_service = update_service

    @application.get("/api/updates/status")
    async def update_status(
        refresh: bool = Query(default=False),
    ) -> dict[str, object]:
        return await asyncio.to_thread(update_service.get_status, force=refresh)

    @application.get("/api/updates/channel")
    async def update_channel() -> dict[str, object]:
        return {"channel": update_service.channel()}

    @application.put("/api/updates/channel")
    async def choose_update_channel(
        channel: str = Body(..., embed=True),
    ) -> dict[str, object]:
        # A preference rather than a per-update action, so it lives in Settings
        # and is written here rather than through the generic settings endpoint:
        # this is where the value is validated and where switching discards the
        # other channel's cached answer.
        try:
            return {"channel": await asyncio.to_thread(update_service.set_channel, channel)}
        except ValueError as exc:
            # Both an unrecognised channel and a refused settings write
            # (``SettingsError`` is a ``ValueError``) are the caller's problem.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except UpdateChannelUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not save the update channel: {exc}",
            ) from exc

    @application.post("/api/updates/install", status_code=status.HTTP_202_ACCEPTED)
    async def install_update(
        confirmation: str | None = Header(default=None, alias="X-WG-Update"),
    ) -> dict[str, object]:
        # A custom header prevents an unrelated web page from submitting a
        # simple cross-origin form to this loopback-only mutation endpoint.
        if confirmation != "install":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The update confirmation header is missing.",
            )
        try:
            return await asyncio.to_thread(update_service.request_install)
        except UpdateInstallUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    return update_service
