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
import contextlib
from dataclasses import dataclass
import io
import json
import logging
import secrets
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence
import urllib.error
import urllib.parse
import urllib.request

from server.cadlink.limits import MAX_DOWNLOAD_BYTES

from .credentials import OnshapeCredentials


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60.0
UPLOAD_TIMEOUT_S = 300.0
_MAX_ERROR_BODY = 600
#: Chunk size for the streaming download. Small enough that the refusal fires
#: within a chunk of the limit, large enough not to syscall per kilobyte.
_STREAM_CHUNK_BYTES = 256 * 1024


class OnshapeError(RuntimeError):
    """Base class for every failure this client reports."""


class OnshapeTransportError(OnshapeError):
    """The request never produced an HTTP response."""


class OnshapeDownloadTooLarge(OnshapeError):
    """The response body exceeds the untrusted-download limit.

    Raised either from a declared ``Content-Length`` before a single body byte
    is read, or from the streaming loop the moment the running total passes the
    limit.  Either way the complete body is never held in memory.
    """

    def __init__(self, limit_bytes: int, *, declared: int | None = None) -> None:
        detail = (
            f"declared {declared:,} bytes"
            if declared is not None
            else "kept sending past the limit"
        )
        super().__init__(
            f"Onshape's download exceeds WG's {limit_bytes:,} byte limit for an "
            f"untrusted CAD payload ({detail}). WG stopped reading rather than "
            "buffer it."
        )
        self.limit_bytes = limit_bytes
        self.declared_bytes = declared


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


class ByteStream(Protocol):
    """The minimum a streamed body has to offer: bounded reads."""

    def read(self, size: int, /) -> bytes: ...


class StreamTransport(Protocol):
    """The seam for a body that must never be read whole into memory.

    Unlike :class:`Transport` this yields an open reader rather than ``bytes``,
    so the caller decides how much it is willing to accept.  The context
    manager owns the connection and closes it on the way out, which is what
    makes an early refusal an actual abort rather than a discarded buffer.
    """

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> contextlib.AbstractContextManager[tuple[int, Mapping[str, str], ByteStream]]: ...


@contextlib.contextmanager
def _urllib_stream_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> Iterator[tuple[int, Mapping[str, str], ByteStream]]:
    """Open one response and hand back the reader without draining it."""

    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        # An error reply is still a readable stream; the caller bounds it.
        with exc:
            yield (
                int(exc.code),
                {key.lower(): value for key, value in exc.headers.items()} if exc.headers else {},
                exc,
            )
        return
    with response:
        yield (
            int(response.status),
            {key.lower(): value for key, value in response.headers.items()},
            response,
        )


def stream_from_transport(transport: Transport) -> StreamTransport:
    """Adapt a whole-body :class:`Transport` to the streaming seam.

    Test doubles and any caller that already holds the bytes keep working
    through this, and the limit arithmetic still runs in exactly one place.
    It is *not* how the network path works: real downloads go through
    :func:`_urllib_stream_transport`, which never materialises the body.
    """

    @contextlib.contextmanager
    def adapted(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Iterator[tuple[int, Mapping[str, str], ByteStream]]:
        status, response_headers, raw = transport(method, url, headers, body, timeout)
        with io.BytesIO(raw) as reader:
            yield status, response_headers, reader

    return adapted


def _read_bounded(stream: ByteStream, limit: int) -> bytes:
    """Read at most ``limit`` bytes, refusing the moment the body exceeds it."""

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(_STREAM_CHUNK_BYTES)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            # Everything read so far is dropped unreferenced and the enclosing
            # context manager closes the connection. Nothing further is read.
            raise OnshapeDownloadTooLarge(limit)
        chunks.append(chunk)


def _declared_length(headers: Mapping[str, str], limit: int) -> None:
    """Refuse an over-limit ``Content-Length`` before reading a single byte."""

    raw = headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(str(raw).strip())
    except ValueError:
        # An unparseable length is not a licence to read without a bound; the
        # streaming loop remains the enforcement of record.
        return
    if declared > limit:
        raise OnshapeDownloadTooLarge(limit, declared=declared)


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
            headers_out = {key.lower(): value for key, value in response.headers.items()}
            # Even a JSON reply is remote input. This path handles small API
            # answers, but "small" is the server's claim, not a guarantee, so
            # the same download ceiling is the backstop here too.
            _declared_length(headers_out, MAX_DOWNLOAD_BYTES)
            return (int(response.status), headers_out, _read_bounded(response, MAX_DOWNLOAD_BYTES))
    except urllib.error.HTTPError as exc:
        payload = _read_bounded(exc, MAX_DOWNLOAD_BYTES) if exc.fp is not None else b""
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
            "My account > Developer > API keys and update the key file."
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
        stream_transport: StreamTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._credentials = credentials
        self._transport: Transport = transport or _urllib_transport
        # A caller that replaced only the whole-body seam still gets the
        # streaming limit logic, applied to its canned bytes.
        self._stream_transport: StreamTransport = (
            stream_transport
            or (stream_from_transport(transport) if transport is not None else None)
            or _urllib_stream_transport
        )
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

    def request_bytes(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        timeout: float | None = None,
        expected: Sequence[int] = (200,),
        accept: str = "application/octet-stream",
        max_redirects: int = 3,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
    ) -> OnshapeResponse:
        """Stream a binary body under a hard ceiling, without leaking auth.

        Onshape's download endpoints may redirect to an attachment host.  The
        API key is retained only for redirects that stay on the configured API
        origin; an HTTPS cross-origin attachment is fetched without it.  HTTP,
        user-info URLs, fragments, and redirect loops are refused.

        The body is untrusted CAD: it is read in chunks and abandoned the
        moment the running total passes ``max_bytes``, and a ``Content-Length``
        already over the limit is refused before the first read
        (``docs/plans/STEP-PARSER-ISOLATION.md``).
        """

        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects must be non-negative")
        initial = (
            f"{self.base_url.rstrip('/')}{path}"
            if path.startswith("/")
            else urllib.parse.urljoin(self.base_url.rstrip("/") + "/", path)
        )
        api_origin = urllib.parse.urlsplit(self.base_url)
        current = initial
        redirects = 0
        while True:
            target = urllib.parse.urlsplit(current)
            if target.scheme != "https" or target.username or target.password or target.fragment:
                raise OnshapeTransportError(
                    "Onshape returned an unsafe download URL; only HTTPS URLs "
                    "without credentials or fragments are accepted."
                )
            same_origin = (
                target.scheme.casefold(), target.hostname, target.port
            ) == (
                api_origin.scheme.casefold(), api_origin.hostname, api_origin.port
            )
            headers = {"Accept": accept}
            if same_origin:
                headers["Authorization"] = self._authorization
            if content_type is not None:
                headers["Content-Type"] = content_type
            try:
                opened = self._stream_transport(
                    method, current, headers, body, timeout or self._timeout
                )
            except OnshapeError:
                raise
            except Exception as exc:
                raise OnshapeTransportError(
                    f"Could not retrieve Onshape download ({method} {path}): {exc}."
                ) from exc

            redirect_to: str | None = None
            try:
                with opened as (raw_status, raw_headers, stream):
                    status = int(raw_status)
                    response_headers = {
                        str(key).lower(): str(value) for key, value in raw_headers.items()
                    }
                    remaining = response_headers.get("x-rate-limit-remaining")
                    if remaining is not None:
                        try:
                            self.last_rate_limit_remaining = int(remaining)
                        except ValueError:
                            self.last_rate_limit_remaining = None
                    if 300 <= status < 400:
                        if redirects >= max_redirects:
                            raise OnshapeHttpError(
                                status,
                                "Onshape download exceeded the redirect limit.",
                                method=method,
                                path=path,
                            )
                        location = response_headers.get("location")
                        if not location:
                            raise OnshapeHttpError(
                                status,
                                "Onshape download redirected without a Location header.",
                                method=method,
                                path=path,
                            )
                        # The redirect body is never read; the connection is
                        # dropped by leaving this block.
                        redirect_to = urllib.parse.urljoin(current, location)
                    elif status not in expected:
                        # A diagnostic snippet, not a payload: truncate rather
                        # than refuse, and never read past the snippet.
                        text = stream.read(_MAX_ERROR_BODY).decode("utf-8", errors="replace")
                        raise OnshapeHttpError(
                            status,
                            _describe(status, path, text),
                            method=method,
                            path=path,
                            body=text,
                        )
                    else:
                        _declared_length(response_headers, max_bytes)
                        payload = _read_bounded(stream, max_bytes)
            except OnshapeError:
                raise
            except Exception as exc:
                raise OnshapeTransportError(
                    f"Could not retrieve Onshape download ({method} {path}): {exc}."
                ) from exc

            if redirect_to is not None:
                current = redirect_to
                method = "GET"
                body = None
                content_type = None
                redirects += 1
                continue
            return OnshapeResponse(status=status, body=payload, headers=response_headers)

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
    "MAX_DOWNLOAD_BYTES",
    "ByteStream",
    "OnshapeClient",
    "OnshapeDownloadTooLarge",
    "OnshapeError",
    "OnshapeHttpError",
    "OnshapeResponse",
    "OnshapeTransportError",
    "StreamTransport",
    "Transport",
    "UPLOAD_TIMEOUT_S",
    "stream_from_transport",
]
