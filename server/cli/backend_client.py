"""HTTP solve client and a relay of the existing Jobs Protocol v1 stream.

urllib keeps HTTP dependency-free and sends no Origin header. The pinned
websockets client preserves live progress/partial-result records without
inventing polling events. HTTP status remains the completion authority.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
import urllib.error
import urllib.request

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from server.jobs.models import SolveAccepted, SolveRequest
from server.jobs.runtime import JobResourceUnavailableError
from server.platform.backend_owner import BackendHandle


class BackendClientError(RuntimeError):
    def __init__(
        self, message: str, *, error: dict[str, Any] | None = None,
        http_status: int | None = None,
    ):
        super().__init__(message)
        self.http_status = http_status
        self.error = error if isinstance(error, dict) else {
            "code": "backend_connection_failed",
            "stage": "backend",
            "message": message,
            "retryable": True,
        }


class BackendClient:
    def __init__(self, handle: BackendHandle):
        self.handle = handle
        # Loopback traffic must not depend on a shell's HTTP proxy settings.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._requests: dict[str, dict[str, Any]] = {}

    def job_url(self, job_id: str) -> str:
        return f"{self.handle.base_url}/?job={job_id}"

    async def _http(
        self, path: str, *, body: dict[str, Any] | None = None, method: str = "GET"
    ) -> str:
        def fetch() -> str:
            data = json.dumps(body, allow_nan=False).encode("utf-8") if body is not None else None
            request = urllib.request.Request(
                self.handle.base_url + path,
                data=data,
                method=method,
                headers={"Content-Type": "application/json"} if data is not None else {},
            )
            try:
                with self._opener.open(request, timeout=30) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                try:
                    payload = json.loads(exc.read().decode("utf-8"))
                except (ValueError, UnicodeError):
                    payload = {}
                error = payload.get("error") if isinstance(payload, dict) else None
                detail = payload.get("detail") if isinstance(payload, dict) else None
                message = str(error.get("message") if isinstance(error, dict) else detail or exc)
                if not isinstance(error, dict):
                    error = {
                        "code": "backend_http_error", "stage": "backend",
                        "message": message, "retryable": exc.code >= 500,
                        "details": {"http_status": exc.code},
                    }
                raise BackendClientError(message, error=error, http_status=exc.code) from exc
            except (OSError, UnicodeError) as exc:
                raise BackendClientError(f"Could not reach WG backend at {self.handle.base_url}: {exc}") from exc

        return await asyncio.to_thread(fetch)

    async def _json(self, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            payload = json.loads(await self._http(path, **kwargs))
            if not isinstance(payload, dict):
                raise ValueError("expected a JSON object")
            return payload
        except ValueError as exc:
            raise BackendClientError(f"Invalid WG backend response for {path}: {exc}") from exc

    async def submit(self, request: SolveRequest) -> str:
        try:
            accepted = await self._json(
                "/api/solve", body=request.model_dump(mode="json"), method="POST"
            )
        except BackendClientError as exc:
            if exc.error.get("code") == "submission_key_conflict":
                message = (
                    f"{exc}. The stored job was made by a different request or "
                    "solver configuration; choose a new clientRequestId to solve again."
                )
                raise BackendClientError(
                    message, http_status=exc.http_status,
                    error=dict(exc.error, code="submission_conflict", message=message),
                ) from exc
            raise
        try:
            return SolveAccepted.model_validate(accepted).job_id
        except ValueError as exc:
            raise BackendClientError(f"Invalid WG solve acceptance: {exc}") from exc

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return await self._json(f"/api/status/{job_id}")

    async def stop(self, job_id: str) -> None:
        await self._http(f"/api/stop/{job_id}", method="POST")

    async def get_results_text(self, job_id: str) -> str:
        return await self._http(f"/api/results/{job_id}")

    async def _job_requests(self, job_id: str) -> dict[str, Any]:
        if job_id not in self._requests:
            self._requests[job_id] = await self._json(f"/api/jobs/{job_id}/requests")
        return self._requests[job_id]

    async def get_effective_request(self, job_id: str) -> dict[str, Any]:
        # Stored requests are artifact documents, not new submissions. Keep
        # their wire values exactly, including snapshots from older schemas.
        return (await self._job_requests(job_id))["effective_request"]

    async def get_execution_request(self, job_id: str) -> dict[str, Any]:
        return (await self._job_requests(job_id))["execution_request"]

    async def get_mesh_artifact(self, job_id: str) -> str:
        try:
            return await self._http(f"/api/mesh-artifact/{job_id}")
        except BackendClientError as exc:
            if isinstance(exc.__cause__, urllib.error.HTTPError) and exc.__cause__.code in {404, 410}:
                raise JobResourceUnavailableError(str(exc)) from exc
            raise

    async def get_log(self, job_id: str) -> str:
        return await self._http(f"/api/jobs/{job_id}/log")

    async def run_events(self, transport: Any) -> None:
        """Relay until the CLI closes; never leave a receiver printing after outcome."""

        closed = asyncio.create_task(transport.receive())
        receiver: asyncio.Task[Any] | None = None
        cursor: int | None = None
        connected = False
        try:
            while not closed.done():
                resume_cursor = cursor
                try:
                    async with connect(
                        self.handle.base_url.replace("http://", "ws://", 1) + "/ws/jobs",
                        origin=None,
                        proxy=None,
                        max_size=None,
                        open_timeout=10,
                    ) as websocket:
                        while not closed.done():
                            receiver = asyncio.create_task(websocket.recv())
                            done, _ = await asyncio.wait({receiver, closed}, return_when=asyncio.FIRST_COMPLETED)
                            if closed in done:
                                break
                            message = json.loads(receiver.result())
                            if message.get("kind") == "hello":
                                connected = True
                                if resume_cursor is not None:
                                    await websocket.send(json.dumps({
                                        "v": 1, "kind": "resume", "epoch": message["epoch"],
                                        "cursor": resume_cursor,
                                    }))
                            if type(message.get("cursor")) is int:
                                cursor = max(cursor or 0, message["cursor"])
                            await transport.send_json(message)
                except (OSError, WebSocketException, ValueError) as exc:
                    if not connected:
                        raise BackendClientError(f"WG job event stream closed: {exc}") from exc
                    # Status polling continues independently during reconnect.
                    await asyncio.wait({closed}, timeout=0.2)
        finally:
            for task in (receiver, closed):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(task for task in (receiver, closed) if task is not None), return_exceptions=True)
