"""FastAPI mounting for release update status."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from server.integration.contracts import error_envelope
from server.settings.store import SettingsStore

from .restart import UPDATE_RESTART_PENDING, RestartApproval
from .service import (
    UpdateBuildNotHeldBack,
    UpdateChannelUnavailable,
    UpdateInstallUnavailable,
    UpdateService,
)


class HeldBackBuild(BaseModel):
    """A build the completion record holds back, by contract §2.3's interim identity."""

    model_config = ConfigDict(extra="forbid")

    version: str
    commit: str | None = None
    runtimeId: str | None = None


def mount_updates(
    application: FastAPI,
    *,
    running_version: str,
    data_dir: Path,
    repo_root: Path,
    update_request_path: Path | None = None,
    service: UpdateService | None = None,
    settings: SettingsStore | None = None,
    restart_approval: RestartApproval | None = None,
) -> UpdateService:
    """Attach the update routes.

    ``restart_approval`` is the server's restart-approved latch (contract
    §4.2), shared with the job routes. A supplied ``service`` already owns one,
    and that one is used.
    """

    update_service = service or UpdateService(
        running_version=running_version,
        data_dir=data_dir,
        repo_root=repo_root,
        update_request_path=update_request_path,
        settings=settings,
        restart_approval=restart_approval,
    )
    application.state.update_service = update_service

    @application.get("/api/updates/status")
    async def update_status(
        refresh: bool = Query(default=False),
    ) -> dict[str, object]:
        return await asyncio.to_thread(update_service.get_status, force=refresh)

    @application.get("/api/updates/diagnostics")
    async def update_diagnostics() -> dict[str, object]:
        # The logs "Copy update diagnostics" adds to the update state: each a
        # bounded tail, scrubbed of the home folder. Read from disk, so off the
        # event loop, as the status is.
        return await asyncio.to_thread(update_service.diagnostic_logs)

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
        # A restart is already approved, so another installation would stage
        # or hand off under one that is about to happen (contract §4.2). The
        # envelope's ``detail`` is the message, which is all the update dialog
        # reads. A service without a latch has approved nothing.
        approval = getattr(update_service, "restart_approval", None)
        refusal = approval.refusal() if approval is not None else None
        if refusal is not None:
            return JSONResponse(  # type: ignore[return-value]
                status_code=status.HTTP_409_CONFLICT,
                content=error_envelope(
                    code=UPDATE_RESTART_PENDING,
                    stage="submission",
                    message=refusal,
                    retryable=True,
                ),
            )
        try:
            return await asyncio.to_thread(update_service.request_install)
        except UpdateInstallUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @application.post("/api/updates/retry")
    async def retry_held_back_build(
        build: HeldBackBuild,
        confirmation: str | None = Header(default=None, alias="X-WG-Update"),
    ) -> dict[str, object]:
        # The explicit retry of contract §2.3. It lifts the one suppression it
        # names and installs nothing; the dialog then offers the build again.
        # Guarded by a custom header for the same reason install is.
        if confirmation != "retry":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The update confirmation header is missing.",
            )
        try:
            lifted = await asyncio.to_thread(update_service.lift_suppression, build.model_dump())
        except UpdateBuildNotHeldBack as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not retry that build: {exc}",
            ) from exc
        return {"lifted": lifted}

    return update_service
