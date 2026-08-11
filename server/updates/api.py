"""FastAPI mounting for release update status."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, Query

from .service import UpdateService


def mount_updates(
    application: FastAPI,
    *,
    running_version: str,
    data_dir: Path,
    repo_root: Path,
    service: UpdateService | None = None,
) -> UpdateService:
    update_service = service or UpdateService(
        running_version=running_version,
        data_dir=data_dir,
        repo_root=repo_root,
    )
    application.state.update_service = update_service

    @application.get("/api/updates/status")
    async def update_status(
        refresh: bool = Query(default=False),
    ) -> dict[str, object]:
        return await asyncio.to_thread(update_service.get_status, force=refresh)

    return update_service
