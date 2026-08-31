"""The report endpoints, including the states nobody reports from by choice."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any
import zipfile

from fastapi import FastAPI
import pytest

from server.diagnostics.api import (
    CLIENT_ERROR_RATE,
    MAX_CLIENT_ERRORS,
    ClientErrorLog,
    create_diagnostics_router,
)
from server.diagnostics.capabilities import capabilities_or_none


BUILD = {"version": "0.3.0", "label": "0.3.0+gabcd1234", "commit_short": "abcd1234", "source": "git"}


class Engine:
    def __init__(self, name: str) -> None:
        self.name = name
        self.available = True
        self.reason = ""
        self.version = "0.1.0"


class FakeRegistry:
    """A registry that answers, or hangs, or explodes -- all three happen."""

    def __init__(self, *, delay: float = 0.0, raises: bool = False) -> None:
        self._delay = delay
        self._raises = raises

    async def capabilities(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises:
            raise RuntimeError("the detector fell over")
        return (Engine("bempp"),)


class FakeStore:
    def __init__(self, rows: list[dict[str, Any]] | None = None, *, broken: bool = False) -> None:
        self._rows = rows or []
        self._broken = broken

    def list_jobs(self, limit: int = 50, offset: int = 0):
        if self._broken:
            raise RuntimeError("database is locked")
        return list(self._rows[:limit]), len(self._rows)

    def get_job_row(self, job_id: str):
        return next((row for row in self._rows if row["id"] == job_id), None)

    def get_job_log(self, job_id: str) -> str:
        return f"log for {job_id}\n"


class FakeRuntime:
    def __init__(self, store: FakeStore | None) -> None:
        self.store = store


class FakeSettings:
    def __init__(self, envelope: dict[str, Any] | None = None, *, broken: bool = False) -> None:
        self._envelope = envelope or {"schemaVersion": 1, "namespaces": {}}
        self._broken = broken

    def envelope(self) -> dict[str, Any]:
        if self._broken:
            raise RuntimeError("settings file is unreadable")
        return self._envelope


class Client:
    """A dependency-free ASGI harness, as elsewhere in this suite.

    The project has no httpx, so ``starlette.testclient`` is unavailable and
    each API module drives the app through the protocol directly.
    """

    def __init__(self, app) -> None:
        self.app = app

    def request(self, method: str, path: str, *, query: str = "", body: bytes = b""):
        async def run():
            sent: list[dict[str, Any]] = []
            delivered = False

            async def receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            headers = [(b"host", b"127.0.0.1:3100")]
            if body:
                headers.append((b"content-type", b"application/json"))
            await self.app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.3"},
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": query.encode(),
                    "root_path": "",
                    "headers": headers,
                    "client": ("127.0.0.1", 12345),
                    "server": ("127.0.0.1", 3100),
                },
                receive,
                send,
            )
            start = next(message for message in sent if message["type"] == "http.response.start")
            payload = b"".join(
                message.get("body", b"")
                for message in sent
                if message["type"] == "http.response.body"
            )
            return (
                start["status"],
                {name.decode().lower(): value.decode() for name, value in start["headers"]},
                payload,
            )

        return asyncio.run(run())


def client_for(tmp_path: Path, **overrides) -> Client:
    arguments: dict[str, Any] = {
        "data_dir": tmp_path,
        "version": "0.3.0",
        "build": BUILD,
        "engine_registry": FakeRegistry(),
        "settings": FakeSettings(),
        "jobs": FakeRuntime(FakeStore()),
    }
    arguments.update(overrides)
    app = FastAPI()
    app.include_router(create_diagnostics_router(**arguments))
    return Client(app)


def test_summary_carries_both_the_object_and_the_pasteable_text(tmp_path: Path) -> None:
    status, _headers, payload = client_for(tmp_path).request("GET", "/api/diagnostics/summary")
    assert status == 200
    body = json.loads(payload)
    assert body["summary"]["build"]["label"] == "0.3.0+gabcd1234"
    assert body["text"].startswith("Waveguide Generator 0.3.0 (0.3.0+gabcd1234)")


def test_the_bundle_downloads_as_a_named_zip(tmp_path: Path) -> None:
    status, headers, payload = client_for(tmp_path).request("GET", "/api/diagnostics/bundle")
    assert status == 200
    assert headers["content-type"] == "application/zip"
    assert 'filename="wg-report-abcd1234-' in headers["content-disposition"]
    # Already deflated. Declaring identity is what stops GZipMiddleware
    # spending event-loop CPU compressing it a second time.
    assert headers["content-encoding"] == "identity"
    assert "manifest.json" in zipfile.ZipFile(io.BytesIO(payload)).namelist()


def test_a_selected_run_adds_its_log(tmp_path: Path) -> None:
    rows = [{"id": "run-1", "run_number": 1, "status": "failed", "config_json": {"throat": 25.4}}]
    client = client_for(tmp_path, jobs=FakeRuntime(FakeStore(rows)))
    _status, _headers, payload = client.request("GET", "/api/diagnostics/bundle", query="job=run-1")
    members = zipfile.ZipFile(io.BytesIO(payload)).namelist()
    assert "logs/job-run-1.log" in members
    # The stored request is the design, so it follows the design checkbox.
    assert "design/job-run-1.request.json" not in members


def test_a_selected_run_ships_its_request_only_with_the_design_box(tmp_path: Path) -> None:
    rows = [{"id": "run-1", "run_number": 1, "status": "failed", "config_json": {"throat": 25.4}}]
    client = client_for(tmp_path, jobs=FakeRuntime(FakeStore(rows)))
    _status, _headers, payload = client.request(
        "GET", "/api/diagnostics/bundle", query="job=run-1&design=true"
    )
    assert "design/job-run-1.request.json" in zipfile.ZipFile(io.BytesIO(payload)).namelist()


def test_an_unknown_run_is_a_404_and_not_a_silent_omission(tmp_path: Path) -> None:
    status, _headers, _payload = client_for(tmp_path).request(
        "GET", "/api/diagnostics/bundle", query="job=nope"
    )
    assert status == 404


def test_a_broken_job_database_still_produces_a_report(tmp_path: Path) -> None:
    """The state being reported on is exactly the state that breaks the report."""

    client = client_for(tmp_path, jobs=FakeRuntime(FakeStore(broken=True)))
    status, _headers, payload = client.request("GET", "/api/diagnostics/bundle")
    assert status == 200
    summary = json.loads(zipfile.ZipFile(io.BytesIO(payload)).read("summary.json"))
    assert summary["recentJobs"] == []


def test_unreadable_settings_still_produce_a_report(tmp_path: Path) -> None:
    client = client_for(tmp_path, settings=FakeSettings(broken=True))
    status, _headers, payload = client.request("GET", "/api/diagnostics/bundle")
    assert status == 200
    assert json.loads(zipfile.ZipFile(io.BytesIO(payload)).read("settings.json"))["included"] == {}


def test_a_build_without_a_job_store_still_reports(tmp_path: Path) -> None:
    status, _headers, _payload = client_for(tmp_path, jobs=None).request(
        "GET", "/api/diagnostics/bundle"
    )
    assert status == 200


def test_a_slow_solver_probe_does_not_hold_the_report(tmp_path: Path) -> None:
    """A hung probe is the likeliest reason somebody is filing a report at all."""

    payload = asyncio.run(capabilities_or_none(FakeRegistry(delay=5.0), timeout=0.05))
    assert payload is None


def test_a_probe_that_raises_is_reported_rather_than_propagated() -> None:
    assert asyncio.run(capabilities_or_none(FakeRegistry(raises=True), timeout=1.0)) is None


def test_a_timed_out_probe_is_named_in_the_bundle(tmp_path: Path) -> None:
    client = client_for(tmp_path, engine_registry=FakeRegistry(delay=5.0), probe_timeout=0.05)
    status, _headers, payload = client.request("GET", "/api/diagnostics/bundle")
    assert status == 200
    capabilities = json.loads(zipfile.ZipFile(io.BytesIO(payload)).read("capabilities.json"))
    assert capabilities["status"] == "unavailable"


def test_a_client_error_is_recorded_and_reaches_the_bundle(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    status, _headers, payload = client.request(
        "POST",
        "/api/diagnostics/client-log",
        body=json.dumps({"message": "Cannot read properties of undefined", "source": "boundary"}).encode(),
    )
    assert status == 200 and json.loads(payload)["recorded"] is True

    _status, _headers, bundle = client.request("GET", "/api/diagnostics/bundle")
    errors = json.loads(zipfile.ZipFile(io.BytesIO(bundle)).read("frontend-errors.json"))
    assert errors[0]["message"] == "Cannot read properties of undefined"
    assert errors[0]["source"] == "boundary"


def test_a_client_error_without_a_message_is_refused(tmp_path: Path) -> None:
    status, _headers, _payload = client_for(tmp_path).request(
        "POST", "/api/diagnostics/client-log", body=json.dumps({"stack": "..."}).encode()
    )
    assert status == 400


@pytest.mark.parametrize("body", [b'"a string"', b"[1, 2]"])
def test_a_client_error_that_is_not_an_object_is_refused(tmp_path: Path, body: bytes) -> None:
    status, _headers, _payload = client_for(tmp_path).request(
        "POST", "/api/diagnostics/client-log", body=body
    )
    assert status == 400


def test_a_render_loop_cannot_fill_the_log(tmp_path: Path) -> None:
    """The rate limit exists so a repeating error cannot evict the 5 MB log."""

    errors = ClientErrorLog()
    accepted = sum(1 for index in range(200) if errors.record({"message": str(index)}, now=1.0))
    assert accepted == CLIENT_ERROR_RATE


def test_the_error_buffer_forgets_the_oldest(tmp_path: Path) -> None:
    errors = ClientErrorLog()
    for index in range(MAX_CLIENT_ERRORS + 10):
        errors.record({"message": str(index)}, now=float(index) * 10.0)
    assert len(errors) == MAX_CLIENT_ERRORS
    assert errors.snapshot()[-1]["message"] == str(MAX_CLIENT_ERRORS + 9)


def test_the_rate_window_reopens(tmp_path: Path) -> None:
    errors = ClientErrorLog()
    for index in range(CLIENT_ERROR_RATE + 5):
        errors.record({"message": str(index)}, now=1.0)
    assert errors.record({"message": "later"}, now=200.0) is True


def test_a_long_client_message_is_truncated_rather_than_refused(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    status, _headers, _payload = client.request(
        "POST",
        "/api/diagnostics/client-log",
        body=json.dumps({"message": "x" * 40_000}).encode(),
    )
    assert status == 200
    _status, _headers, bundle = client.request("GET", "/api/diagnostics/bundle")
    errors = json.loads(zipfile.ZipFile(io.BytesIO(bundle)).read("frontend-errors.json"))
    assert 0 < len(errors[0]["message"]) < 40_000


def test_the_client_log_body_is_capped_by_the_application(tmp_path: Path) -> None:
    """Enforced by the body-limit middleware, before anything is buffered.

    The endpoint truncates what it stores, but only after reading the body, so
    the ceiling that matters is the one ``create_app`` registers for this path.
    """

    from server.app import create_app
    from server.diagnostics.api import CLIENT_LOG_PATH, MAX_CLIENT_LOG_BODY_BYTES

    application = create_app(data_dir=tmp_path)
    limits = [
        middleware.kwargs.get("path_limits", {})
        for middleware in application.user_middleware
        if "path_limits" in getattr(middleware, "kwargs", {})
    ]
    assert limits and limits[0][CLIENT_LOG_PATH] == MAX_CLIENT_LOG_BODY_BYTES
