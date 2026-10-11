"""What the per-request middleware writes, and what it deliberately does not.

uvicorn's access log is off (``launch/serve.py``), so ``log_request`` in
``server/app.py`` is the only per-request line anything writes. Measured on the
packaged app sitting idle with nobody touching the window, that was ~8 lines a
second appended to ``server.log`` forever: a health probe, the shell document
and three CAD-link pollers, none of which says anything the second time it is
read. The cost is not the formatting -- it is a permanent trickle of small disk
writes that keeps the drive out of its low-power states, and a log in which the
line that mattered is a needle in megabytes of "still here".

The pollers themselves are being quietened at the callers. This file pins the
backstop: those routes stay logged, they just stop shouting, and nothing that
went wrong is ever quietened with them.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from server.app import QUIET_REQUEST_ROUTES, create_app

# The suite's ASGI harness rather than a second copy of it. ``server/tests`` is
# a namespace package, which is the same import shape the suite already uses
# for its shared doubles.
from server.tests.test_app_batch_e import TestClient


REQUEST_LOGGER = "wg.requests"

JSON = {"Content-Type": "application/json"}
#: The smallest body ``/api/cadlink/fusion-status`` accepts: the wrapper needs a
#: design, and ``DesignConfig`` is a union discriminated on ``formula``. The
#: rest defaults, and with no WGLink folder selected the handler answers without
#: touching one -- which is the shape the idle poll actually has.
FUSION_STATUS_BODY = b'{"design": {"formula": "OSSE"}}'

IDLE_POLLS = (
    ("GET", "/health"),
    ("GET", "/"),
    ("GET", "/api/cadlink/returns"),
    ("GET", "/api/cadlink/solve-command"),
    ("POST", "/api/cadlink/fusion-status"),
)


def _messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == REQUEST_LOGGER and record.levelno == level
    ]


def test_idle_pollers_log_at_debug_and_everything_else_stays_at_info(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    # Captured at DEBUG so the quiet lines are visible to the assertions: they
    # moved level, they did not vanish, and a developer running the server at
    # DEBUG still gets the full trace. In the shipped app the root logger sits
    # at INFO (``server/platform/logging_setup.py``) and these are dropped
    # before anything is formatted or queued, which is the point.
    with caplog.at_level(logging.DEBUG, logger=REQUEST_LOGGER):
        for method, path in IDLE_POLLS:
            response = (
                client.post(path, FUSION_STATUS_BODY, JSON)
                if method == "POST"
                else client.get(path)
            )
            assert response.status_code == 200, (method, path)

        assert _messages(caplog, logging.INFO) == []

        assert client.get("/api/capabilities").status_code == 200

    debug_lines = _messages(caplog, logging.DEBUG)
    assert len(debug_lines) == len(IDLE_POLLS)
    for method, path in IDLE_POLLS:
        assert any(
            line.startswith(f"{method} {path} -> 200 ") for line in debug_lines
        ), (method, path)

    info_lines = _messages(caplog, logging.INFO)
    assert len(info_lines) == 1
    assert info_lines[0].startswith("GET /api/capabilities -> 200 ")


def test_a_quietened_route_that_starts_failing_is_still_logged_at_info(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Quiet belongs to the boring answer, not to the route.

    A poller that begins answering 403 or 404 is exactly what somebody opens
    this log to find out, so the level follows the status code rather than the
    path alone. ``/health`` from a disallowed Origin is the cheapest way to
    make one of the five answer badly: ``origin_guard`` runs inside
    ``log_request``, so its rejection is a response the logger sees.
    """

    client = TestClient(create_app(data_dir=tmp_path))

    with caplog.at_level(logging.DEBUG, logger=REQUEST_LOGGER):
        rejected = client.get("/health", headers={"Origin": "https://example.com"})
        assert rejected.status_code == 403

    info_lines = _messages(caplog, logging.INFO)
    assert len(info_lines) == 1
    assert info_lines[0].startswith("GET /health -> 403 ")
    assert _messages(caplog, logging.DEBUG) == []
    # And the guard's own explanation of *why* stays exactly where it was.
    assert any(
        "disallowed Origin" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


def test_the_quiet_list_names_the_measured_idle_traffic() -> None:
    """Method as well as path, so chatter one way cannot quieten action the other."""

    assert QUIET_REQUEST_ROUTES == frozenset(IDLE_POLLS)
    # Nothing that changes state is on the list -- including the sibling route
    # a CAD client posts a solve's outcome back to.
    assert ("POST", "/api/cadlink/solve-command/outcome") not in QUIET_REQUEST_ROUTES
    assert ("POST", "/health") not in QUIET_REQUEST_ROUTES
