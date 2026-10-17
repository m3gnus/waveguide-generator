"""One loopback request straight through an ASGI app, without a server or socket."""

from __future__ import annotations

import asyncio
import json
from typing import Any

#: A small dry-run solve the jobs routes accept.
SOLVE_BODY: dict[str, Any] = {
    "design": {
        "formula": "OSSE",
        "L": 120,
        "a": 45,
        "simulation": {"f1": 300, "f2": 3000, "num_frequencies": 4},
    },
    "options": {"engine": "dryrun", "stage_delay_ms": 1},
}


async def _post(
    app: Any,
    path: str,
    body: dict[str, Any] | None,
    *,
    method: str = "POST",
    headers: tuple[tuple[bytes, bytes], ...] = (),
) -> tuple[int, bytes]:
    """One loopback request straight through the ASGI app, as ``test_jobs_api.py`` sends them."""

    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {
                "type": "http.request",
                "body": b"" if body is None else json.dumps(body).encode(),
                "more_body": False,
            }
        # A live connection blocks here until the client goes away; answering
        # with a disconnect instead can cancel the response before it starts.
        await asyncio.Event().wait()
        raise RuntimeError("unreachable")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"127.0.0.1:3100"),
                (b"content-type", b"application/json"),
                *headers,
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 3100),
        },
        receive,
        send,
    )
    start = next(item for item in sent if item["type"] == "http.response.start")
    response = b"".join(
        item.get("body", b"") for item in sent if item["type"] == "http.response.body"
    )
    return start["status"], response
