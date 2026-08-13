"""A small Onshape REST client built on the standard library.

Deliberately not ``requests`` or ``httpx``.  WG's runtime lock is a short,
fully-resolved list (``server/requirements-lock.txt``) and the Onshape leg is a
handful of calls per export: no connection pooling, streaming, or HTTP/2 to
justify three more locked packages and their transitive surface.  What the
adapter actually needs -- Basic auth, JSON, one multipart upload, timeouts and
legible errors -- is a hundred lines of ``urllib``.

Two behaviours here are security choices rather than convenience:

* redirects are **not** followed.  ``urllib`` would re-send the ``Authorization``
  header to whatever host the redirect names, so a redirect is surfaced as an
  error instead of silently forwarding the key pair.
* no exception, log line, or ``repr`` in this module includes the key pair, and
  response bodies are truncated before they reach a message.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import logging
import secrets
from typing import Any, Callable, Mapping, Protocol, Sequence
import urllib.error
import urllib.request

from .credentials import OnshapeCredentials


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60.0
UPLOAD_TIMEOUT_S = 300.0
_MAX_ERROR_BODY = 600


class OnshapeError(RuntimeError):
    """Base class for every failure this client reports."""


class OnshapeTransportError(OnshapeError):
    """The request never produced an HTTP response."""


class OnshapeHttpError(OnshapeError):
    """Onshape answered with a 4xx or 5xx status."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        method: str,
        path: str,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.method = method
        self.path = path
        self.body = body

    @property
    def is_auth_failure(self) -> bool:
        return self.status in {401, 403}

    @property
    def is_rate_limited(self) -> bool:
        return self.status == 429


@dataclass(frozen=True)
class OnshapeResponse:
    """One decoded Onshape reply."""

    status: int
    body: Any
    headers: Mapping[str, str]

    @property
    def rate_limit_remaining(self) -> int | None:
        raw = self.headers.get("x-rate-limit-remaining")
        try:
            return int(raw) if raw is not None else None
        except ValueError:
            return None


class Transport(Protocol):
    """The one seam tests replace instead of reaching the network."""

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, Mapping[str, str], bytes]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface redirects rather than forwarding credentials to a new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, Mapping[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return (
                int(response.status),
                {key.lower(): value for key, value in response.headers.items()},
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        payload = exc.read() if exc.fp is not None else b""
        return (
            int(exc.code),
            {key.lower(): value for key, value in exc.headers.items()}
            if exc.headers
            else {},
            payload,
        )


def _multipart(
    fields: Mapping[str, str],
    filename: str,
    content: bytes,
    *,
    file_field: str = "file",
    content_type: str = "application/step",
    boundary: str | None = None,
) -> tuple[bytes, str]:
    """Encode one ``multipart/form-data`` body with a single file part."""

    marker = boundary or f"----WGLink{secrets.token_hex(16)}"
    # A filename is interpolated into a header, so a quote or newline in it
    # would let a design name forge part headers. Neither survives here.
    safe_name = filename.replace("\\", "_").replace('"', "_").replace("\r", "").replace("\n", "")
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(
            f"--{marker}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    chunks.append(
        f"--{marker}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{safe_name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
    )
    chunks.append(content)
    chunks.append(f"\r\n--{marker}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={marker}"


def _describe(status: int, path: str, body: str) -> str:
    """Turn an Onshape error into something a WG user can act on."""

    detail = ""
    try:
        parsed = json.loads(body) if body else None
    except ValueError:
        parsed = None
    if isinstance(parsed, Mapping):
        for key in ("message", "moreInfoUrl", "error"):
            value = parsed.get(key)
            if isinstance(value, str) and value:
                detail = value
                break
    if not detail:
        detail = body.strip()[:_MAX_ERROR_BODY]
    if status == 401:
        return (
            "Onshape rejected the API key pair (401). Regenerate it at "
            "https://dev-portal.onshape.com/keys and update the key file."
        )
    if status == 403:
        return (
            "Onshape refused this request (403). The key pair may lack a "
            f"required scope, or the plan may not allow it. {detail}".strip()
        )
    if status == 404:
        return (
            "Onshape could not find this document or element (404). It may have "
            f"been deleted or moved to another account. {detail}".strip()
        )
    if status == 429:
        return (
            "Onshape rate limit reached (429). Wait a moment and send again. "
            f"{detail}".strip()
        )
    if status >= 500:
        return f"Onshape had a server error ({status}). {detail}".strip()
    return f"Onshape rejected the request ({status}). {detail}".strip()


class OnshapeClient:
    """Authenticated access to one Onshape account."""

    def __init__(
        self,
        credentials: OnshapeCredentials,
        *,
        transport: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._credentials = credentials
        self._transport: Transport = transport or _urllib_transport
        self._timeout = timeout
        self._sleep = sleep
        token = base64.b64encode(
            f"{credentials.access_key}:{credentials.secret_key}".encode("utf-8")
        ).decode("ascii")
        self._authorization = f"Basic {token}"
        self.last_rate_limit_remaining: int | None = None

    @property
    def base_url(self) -> str:
        return self._credentials.base_url

    def document_url(self, document_id: str, workspace_id: str | None = None) -> str:
        """The browser URL for a document, derived from the API base URL."""

        root = self.base_url[: -len("/api")] if self.base_url.endswith("/api") else self.base_url
        suffix = f"/w/{workspace_id}" if workspace_id else ""
        return f"{root}/documents/{document_id}{suffix}"

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        body: bytes | None = None,
        content_type: str | None = None,
        timeout: float | None = None,
        expected: Sequence[int] = (200, 201, 202, 204),
    ) -> OnshapeResponse:
        """Issue one API call, raising ``OnshapeError`` on anything unexpected."""

        if json_body is not None and body is not None:
            raise ValueError("pass either json_body or body, not both")
        headers = {
            "Authorization": self._authorization,
            "Accept": "application/json;charset=UTF-8; qs=0.09",
        }
        payload = body
        if json_body is not None:
            payload = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif content_type is not None:
            headers["Content-Type"] = content_type
        url = f"{self.base_url}{path}"
        try:
            status, response_headers, raw = self._transport(
                method, url, headers, payload, timeout or self._timeout
            )
        except OnshapeError:
            raise
        except Exception as exc:  # urllib raises URLError, socket.timeout, ssl errors
            raise OnshapeTransportError(
                f"Could not reach Onshape ({method} {path}): {exc}. "
                "Check the network connection and try again."
            ) from exc

        remaining = response_headers.get("x-rate-limit-remaining")
        if remaining is not None:
            try:
                self.last_rate_limit_remaining = int(remaining)
            except ValueError:
                self.last_rate_limit_remaining = None

        text = raw.decode("utf-8", errors="replace") if raw else ""
        if 300 <= status < 400:
            raise OnshapeHttpError(
                status,
                f"Onshape redirected {method} {path}, which WG does not follow "
                "because it would forward the API key pair to another host.",
                method=method,
                path=path,
            )
        if status not in expected:
            raise OnshapeHttpError(
                status,
                _describe(status, path, text),
                method=method,
                path=path,
                body=text[:_MAX_ERROR_BODY],
            )
        parsed: Any = None
        if text.strip():
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = text
        return OnshapeResponse(status=status, body=parsed, headers=response_headers)

    def get(self, path: str, **kwargs: Any) -> OnshapeResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> OnshapeResponse:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> OnshapeResponse:
        return self.request("DELETE", path, **kwargs)

    def upload_file(
        self,
        path: str,
        *,
        fields: Mapping[str, str],
        filename: str,
        content: bytes,
        file_content_type: str = "application/step",
        timeout: float = UPLOAD_TIMEOUT_S,
    ) -> OnshapeResponse:
        """POST one ``multipart/form-data`` body carrying a single file."""

        body, content_type = _multipart(
            fields, filename, content, content_type=file_content_type
        )
        return self.request(
            "POST",
            path,
            body=body,
            content_type=content_type,
            timeout=timeout,
        )

    def session_info(self) -> dict[str, Any]:
        """Identify the authenticated account. Read-only; changes nothing."""

        response = self.get("/users/sessioninfo")
        return response.body if isinstance(response.body, dict) else {}

    def plan_summary(self) -> dict[str, Any]:
        """Describe the account's plan so WG can state what it implies.

        Onshape's Free plan makes every document public (CAD-LINK-PLAN.md
        section 8.4). WG must say so before it creates one, so this reads the
        plan rather than assuming.
        """

        try:
            response = self.get("/users/current")
        except OnshapeError:
            # Advisory. A plan we cannot read must not block a send, but it
            # must not be reported as private either.
            return {"group": None, "name": None, "public_only": None}
        body = response.body if isinstance(response.body, dict) else {}
        plan = body.get("activePlan")
        plan = plan if isinstance(plan, Mapping) else {}
        group = plan.get("group")
        description = plan.get("description")
        name = plan.get("name")
        public_only: bool | None = None
        if isinstance(group, str) and group:
            public_only = group.strip().lower() == "free"
        if isinstance(description, str) and "public only" in description.lower():
            public_only = True
        return {
            "group": group if isinstance(group, str) else None,
            "name": name if isinstance(name, str) else (description if isinstance(description, str) else None),
            "public_only": public_only,
        }


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "OnshapeClient",
    "OnshapeError",
    "OnshapeHttpError",
    "OnshapeResponse",
    "OnshapeTransportError",
    "Transport",
    "UPLOAD_TIMEOUT_S",
]
