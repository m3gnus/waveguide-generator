"""Bounded, ephemeral stage counts for request-owned work (not solve jobs).

Keep the completed stages, rather than just the latest count: a poll cannot
lose a short stage. No design data or durable operation is stored here.
"""

from collections import OrderedDict
from contextlib import contextmanager
import threading
import json
import time
from typing import Iterator
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from server.jobs.request_cancellation import ClientDisconnected, RequestCancellation

HEADER = "X-WG-Operation-Id"
_LOCK = threading.Lock()
_COUNTS: OrderedDict[str, tuple[float, list[str], "RequestOperation"]] = OrderedDict()
_TTL = 300
_LIMIT = 128


class OperationProgress(BaseModel):
    stages: list[str]
    total: int


def _key(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise HTTPException(422, "Invalid operation id.") from exc


@contextmanager
def request_operation(request: Request | None, stages: tuple[str, ...]) -> Iterator["RequestOperation"]:
    operation = RequestOperation(request, stages)
    try:
        yield operation
    except ClientDisconnected as exc:
        raise HTTPException(499, "Operation was cancelled.") from exc
    finally:
        operation.cancellation.close()


class RequestOperation:
    def __init__(self, request: Request | None, stages: tuple[str, ...]) -> None:
        self.key = _key(request.headers[HEADER]) if request and HEADER in request.headers else None
        self.stages = stages
        self.committed = False
        self.cancellation = RequestCancellation(request.is_disconnected if request else None)
        if self.key:
            with _LOCK:
                now = time.monotonic()
                for key, (created, _, _) in list(_COUNTS.items()):
                    if now - created > _TTL:
                        del _COUNTS[key]
                if self.key in _COUNTS:
                    self.cancellation.close()
                    raise HTTPException(409, "Operation id is already in use.")
                _COUNTS[self.key] = (now, [], self)
                while len(_COUNTS) > _LIMIT:
                    _COUNTS.popitem(last=False)

    def attach(self, response: Response) -> None:
        if self.key:
            with _LOCK:
                entry = _COUNTS.get(self.key)
                if entry is not None:
                    response.headers["X-WG-Operation-Stages"] = json.dumps(
                        {"stages": entry[1], "total": len(entry[2].stages)}, separators=(",", ":")
                    )

    def check(self) -> None:
        if not self.committed:
            self.cancellation.check()

    def begin_commit(self) -> None:
        """Serialize the final cancellation check with acknowledgement of Stop.

        After this succeeds the caller must finish publication. A late Stop
        gets 409 and keeps the original request alive to receive its outcome.
        """
        with _LOCK:
            self.check()
            self.committed = True

    def progress(self, stage: str) -> None:
        if self.key:
            with _LOCK:
                entry = _COUNTS.get(self.key)
                if entry is not None:
                    completed = entry[1]
                    if len(completed) < len(self.stages) and self.stages[len(completed)] == stage:
                        completed.append(stage)


def mount_operation_progress(application: FastAPI) -> None:
    @application.get("/api/operation-progress/{operation_id}", response_model=OperationProgress)
    async def operation_progress(operation_id: str) -> OperationProgress:
        with _LOCK:
            entry = _COUNTS.get(_key(operation_id))
            if entry is None or time.monotonic() - entry[0] > _TTL:
                raise HTTPException(404, "Operation progress is no longer available.")
            return OperationProgress(stages=list(entry[1]), total=len(entry[2].stages))

    @application.delete("/api/operation-progress/{operation_id}", status_code=204)
    async def cancel_operation(operation_id: str) -> Response:
        with _LOCK:
            entry = _COUNTS.get(_key(operation_id))
            if entry is None or time.monotonic() - entry[0] > _TTL:
                raise HTTPException(404, "Operation progress is no longer available.")
            operation = entry[2]
            if operation.committed:
                raise HTTPException(409, "The operation is already being sent. Wait for its result.")
            operation.cancellation.disconnected.set()
        return Response(status_code=204)
