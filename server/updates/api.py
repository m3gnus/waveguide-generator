"""FastAPI mounting for release update status."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, status

from .service import UpdateInstallUnavailable, UpdateService


def mount_updates(
    application: FastAPI,
    *,
    running_version: str,
    data_dir: Path,
    repo_root: Path,
    update_request_path: Path | None = None,
    service: UpdateService | None = None,
) -> UpdateService:
    update_service = service or UpdateService(
        running_version=running_version,
        data_dir=data_dir,
        repo_root=repo_root,
        update_request_path=update_request_path,
    )
    application.state.update_service = update_service

    @application.get("/api/updates/status")
    async def update_status(
        refresh: bool = Query(default=False),
    ) -> dict[str, object]:
        return await asyncio.to_thread(update_service.get_status, force=refresh)

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
