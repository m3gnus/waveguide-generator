"""A multipart write-export request for calling the route's endpoint directly.

The route takes only multipart/form-data. Tests that call the endpoint
function (instead of going through ASGI) build the request here, with the
fields a client sends. Each member is ``{"relative_path", "text"}`` or
``{"relative_path", "content"}`` (bytes). The form is rebuilt on every read,
so one request can be passed to the endpoint more than once, as a retry is.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Mapping
from typing import Any

from starlette.datastructures import FormData, Headers, UploadFile
from starlette.requests import Request


class _FormRequest(Request):
    def __init__(self, fields: list[tuple[str, Any]], app: Any = None) -> None:
        scope: dict[str, Any] = {
            "type": "http",
            "method": "POST",
            "path": "/api/workspace/write-export",
            "headers": [(b"content-type", b"multipart/form-data; boundary=wg-test")],
        }
        if app is not None:
            scope["app"] = app
        super().__init__(scope)
        self._fields = fields

    async def form(self, **_limits: Any) -> FormData:  # type: ignore[override]
        items: list[tuple[str, str | UploadFile]] = []
        for name, value in self._fields:
            if isinstance(value, tuple):
                filename, content = value
                items.append((name, UploadFile(
                    io.BytesIO(content), filename=filename,
                    headers=Headers({"content-type": "application/octet-stream"}),
                )))
            else:
                items.append((name, value))
        return FormData(items)


def write_export_request(
    *,
    subdirectory: str = "",
    members: Iterable[Mapping[str, Any]] = (),
    existing: str = "reject",
    destination: str | None = None,
    job_id: str | None = None,
    app: Any = None,
) -> Request:
    fields: list[tuple[str, Any]] = [("subdirectory", subdirectory), ("existing", existing)]
    if destination is not None:
        fields.append(("destination", destination))
    if job_id is not None:
        fields.append(("job_id", job_id))
    members = list(members)
    fields += [("relative_path", member["relative_path"]) for member in members]
    for member in members:
        content = member["content"] if "content" in member else str(member["text"]).encode("utf-8")
        fields.append(("file", (str(member["relative_path"]).rsplit("/", 1)[-1], content)))
    return _FormRequest(fields, app)
