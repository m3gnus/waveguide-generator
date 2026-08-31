"""REST surface for problem reports."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping
import logging
from pathlib import Path
import subprocess
import time
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException, Query
from fastapi.responses import Response

from server.platform.paths import data_paths
from server.platform.process import background_process_kwargs
from server.workspace.api import open_folder_command
from .bundle import (
    MAX_JOB_LOG_BYTES,
    build_bundle,
    build_summary,
    bundle_filename,
    collect_system,
    summary_text,
)
from .capabilities import PROBE_TIMEOUT_SECONDS, capabilities_or_none
from .scrub import scrub_rules


log = logging.getLogger("wg.diagnostics")
client_log = logging.getLogger("wg.frontend")

#: How many recent runs the summary describes. Enough to show the failure and
#: the successes around it; not so many that the clipboard form stops being
#: something a person will paste.
RECENT_JOB_COUNT = 10

#: One interface error is a fact; a render loop is a denial of service against
#: the 5 MB log the report exists to carry. The buffer forgets, and the rate
#: limit stops the log itself from filling.
MAX_CLIENT_ERRORS = 50
MAX_CLIENT_ERROR_BYTES = 8 * 1024

#: The client-log path and its own body ceiling.
#:
#: The endpoint truncates what it stores, but truncation happens after the body
#: has been read and parsed. The global limit is 64 MB, which is right for a
#: mesh upload and absurd for an error message, so this one is enforced by the
#: body-limit middleware before any of it is buffered.
CLIENT_LOG_PATH = "/api/diagnostics/client-log"
MAX_CLIENT_LOG_BODY_BYTES = 64 * 1024
CLIENT_ERROR_RATE = 30
CLIENT_ERROR_WINDOW_SECONDS = 60.0


class ClientErrorLog:
    """The interface's own errors, kept where a report can reach them.

    The frontend's error boundary and its window handlers report here. Records
    go to the application log, which is what a maintainer greps, *and* to a
    bounded buffer, which is what survives the log rotating between the crash
    and the report.
    """

    def __init__(self, *, capacity: int = MAX_CLIENT_ERRORS) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._window_started = 0.0
        self._window_count = 0

    def record(self, entry: Mapping[str, Any], *, now: float | None = None) -> bool:
        """Keep one report, or refuse it because too many just arrived."""

        current = time.monotonic() if now is None else now
        if current - self._window_started >= CLIENT_ERROR_WINDOW_SECONDS:
            self._window_started = current
            self._window_count = 0
        self._window_count += 1
        if self._window_count > CLIENT_ERROR_RATE:
            return False
        self._entries.append(dict(entry))
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def create_diagnostics_router(
    *,
    data_dir: Path,
    version: str,
    build: Mapping[str, Any],
    engine_registry: Any,
    settings: Any = None,
    jobs: Any = None,
    client_errors: ClientErrorLog | None = None,
    probe_timeout: float = PROBE_TIMEOUT_SECONDS,
) -> APIRouter:
    router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])
    paths = data_paths(data_dir)
    errors = client_errors if client_errors is not None else ClientErrorLog()
    router.client_errors = errors  # type: ignore[attr-defined]

    def _store() -> Any:
        return getattr(jobs, "store", None)

    def _recent_jobs() -> list[dict[str, Any]]:
        store = _store()
        if store is None:
            return []
        try:
            rows, _total = store.list_jobs(limit=RECENT_JOB_COUNT)
        except Exception:
            # A database too damaged to list is itself worth reporting, and it
            # is exactly the state somebody would be reporting from.
            log.warning("Recent jobs could not be listed for a problem report", exc_info=True)
            return []
        return rows

    def _settings_envelope() -> dict[str, Any] | None:
        if settings is None:
            return None
        try:
            return settings.envelope()
        except Exception:
            log.warning("Settings could not be read for a problem report", exc_info=True)
            return None

    async def _summary() -> dict[str, Any]:
        capabilities = await capabilities_or_none(engine_registry, timeout=probe_timeout)
        rules = scrub_rules()
        jobs_rows = await asyncio.to_thread(_recent_jobs)
        return build_summary(
            build=build,
            version=version,
            data_dir=paths.root,
            capabilities=capabilities,
            jobs=jobs_rows,
            frontend_error_count=len(errors),
            rules=rules,
        )

    @router.get("/summary")
    async def read_summary() -> dict[str, Any]:
        summary = await _summary()
        return {"summary": summary, "text": summary_text(summary)}

    @router.get("/bundle")
    async def download_bundle(
        job: str | None = Query(default=None),
        design: bool = Query(default=False),
    ) -> Response:
        capabilities = await capabilities_or_none(engine_registry, timeout=probe_timeout)
        rules = scrub_rules()
        system = collect_system()
        jobs_rows = await asyncio.to_thread(_recent_jobs)
        summary = build_summary(
            build=build,
            version=version,
            data_dir=paths.root,
            capabilities=capabilities,
            jobs=jobs_rows,
            frontend_error_count=len(errors),
            rules=rules,
            system=system,
        )

        job_log: str | None = None
        job_request: Any = None
        if job:
            store = _store()
            if store is None:
                raise HTTPException(status_code=404, detail="This build has no job store.")
            job_log, job_request = await asyncio.to_thread(_read_job, store, job)
            if job_log is None:
                raise HTTPException(status_code=404, detail=f"No run {job!r}.")

        envelope = await asyncio.to_thread(_settings_envelope)
        draft = None
        if design and isinstance(envelope, Mapping):
            namespaces = envelope.get("namespaces")
            if isinstance(namespaces, Mapping):
                draft = namespaces.get("designDraft")

        payload = await asyncio.to_thread(
            build_bundle,
            paths=paths,
            summary=summary,
            system=system,
            capabilities=capabilities,
            settings_envelope=envelope,
            rules=rules,
            job_id=job,
            job_log=job_log,
            job_request=job_request,
            design_draft=draft,
            include_design=design,
            frontend_errors=errors.snapshot(),
        )
        log.info(
            "Problem report built: %d bytes, job=%s, design=%s", len(payload), job or "-", design
        )
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{bundle_filename(summary)}"',
                # The payload is already deflated. Declaring the encoding is
                # what makes ``GZipMiddleware`` pass it through instead of
                # spending event-loop CPU re-compressing it for no bytes.
                "Content-Encoding": "identity",
                "Cache-Control": "no-store",
            },
        )

    @router.post("/open-logs")
    async def open_logs() -> dict[str, str]:
        try:
            paths.logs.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(open_folder_command(paths.logs), **background_process_kwargs())
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to open the logs folder: {exc}"
            ) from exc
        return {"status": "opened"}

    @router.post("/client-log")
    async def record_client_error(entry: Any = Body(...)) -> dict[str, Any]:
        if not isinstance(entry, Mapping):
            raise HTTPException(status_code=400, detail="Expected an object.")
        message = str(entry.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="An error report needs a message.")
        record = {
            "message": message[:MAX_CLIENT_ERROR_BYTES],
            "stack": str(entry.get("stack") or "")[:MAX_CLIENT_ERROR_BYTES] or None,
            "source": str(entry.get("source") or "unknown")[:64],
            "at": str(entry.get("at") or "")[:64] or None,
        }
        if not errors.record(record):
            # 202, not 429: the interface is already failing, and a rejected
            # error report is not a second failure it should try to handle.
            return {"recorded": False, "reason": "rate limited"}
        client_log.warning(
            "Interface error (%s): %s", record["source"], record["message"].splitlines()[0][:400]
        )
        return {"recorded": True}

    return router


def _read_job(store: Any, job_id: str) -> tuple[str | None, Any]:
    """One run's log and its stored request, or ``(None, None)`` if unknown."""

    row = store.get_job_row(job_id)
    if row is None:
        return None, None
    try:
        text = store.get_job_log(job_id)
    except Exception:
        text = ""
    if len(text) > MAX_JOB_LOG_BYTES:
        text = text[-MAX_JOB_LOG_BYTES:]
    return text, row.get("config_json")


def mount_diagnostics(
    application: FastAPI,
    *,
    version: str,
    build: Mapping[str, Any],
    data_dir: str | Path,
) -> APIRouter:
    """Attach the report surface to an assembled application.

    Mounted last, after the stores it reads: the settings store, the job
    runtime and the engine registry all live on ``application.state`` by the
    time ``create_app`` reaches this, and reading them through the state means
    the report describes the application that is actually running rather than
    a second copy of it.
    """

    router = create_diagnostics_router(
        data_dir=Path(data_dir),
        version=version,
        build=build,
        engine_registry=application.state.engine_registry,
        settings=getattr(application.state, "settings", None),
        jobs=getattr(application.state, "jobs_runtime", None),
    )
    application.state.client_errors = router.client_errors  # type: ignore[attr-defined]
    application.include_router(router)
    return router


__all__ = [
    "CLIENT_ERROR_RATE",
    "CLIENT_LOG_PATH",
    "MAX_CLIENT_LOG_BODY_BYTES",
    "MAX_CLIENT_ERRORS",
    "MAX_CLIENT_ERROR_BYTES",
    "ClientErrorLog",
    "create_diagnostics_router",
    "mount_diagnostics",
]
