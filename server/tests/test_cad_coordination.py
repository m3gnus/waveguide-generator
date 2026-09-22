"""WG's CAD coordination gate (server/cadlink/coordination.py; M1 contract C7, C8).

Read once at start-up, reported on the startup line and to the frontend (on
the CAD returns listing the page reads at mount), and default-off: absent or
unrecognised values stop clock-driven coordination. It never touches the
solve-command consumer, which has its own variable and is part of the transfer
path, not coordination.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.cadlink.api import list_returns
from server.cadlink.coordination import (
    CAD_COORDINATION_ENV,
    COORDINATION_OFF,
    COORDINATION_ON,
    read_cad_coordination,
)


@pytest.mark.parametrize("value", [None, "", "off", "OFF", " 0 ", "false", "no", "offf", "disabled"])
def test_the_gate_is_off_without_an_explicit_on_value(value: str | None) -> None:
    environ = {} if value is None else {CAD_COORDINATION_ENV: value}
    assert read_cad_coordination(environ) == COORDINATION_OFF


@pytest.mark.parametrize("value", ["on", "ON", " 1 ", "true", "yes"])
def test_the_gate_is_on_for_recognized_explicit_values(value: str) -> None:
    assert read_cad_coordination({CAD_COORDINATION_ENV: value}) == COORDINATION_ON


def test_the_gate_is_not_the_delivery_switch() -> None:
    from server.cadlink.api import CAD_DELIVERY_ENV

    assert CAD_COORDINATION_ENV != CAD_DELIVERY_ENV
    # Turning delivery off says nothing about coordination, and the reverse.
    assert read_cad_coordination({CAD_DELIVERY_ENV: "0"}) == COORDINATION_OFF
    assert read_cad_coordination({CAD_DELIVERY_ENV: "0", CAD_COORDINATION_ENV: "on"}) == COORDINATION_ON


def _app(tmp_path: Path):
    from server.app import create_app

    return create_app(data_dir=tmp_path / "data", workspace_dir=tmp_path / "runs")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, COORDINATION_OFF), ("invalid", COORDINATION_OFF), ("on", COORDINATION_ON)],
)
def test_startup_reads_the_gate_once_and_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    value: str | None, expected: str,
) -> None:
    if value is None:
        monkeypatch.delenv(CAD_COORDINATION_ENV, raising=False)
    else:
        monkeypatch.setenv(CAD_COORDINATION_ENV, value)
    with caplog.at_level(logging.INFO, logger="wg"):
        application = _app(tmp_path)
    assert application.state.cad_coordination == expected
    lines = [record.getMessage() for record in caplog.records if "application initialized" in record.getMessage()]
    assert len(lines) == 1
    assert f"CAD coordination {expected} ({CAD_COORDINATION_ENV})" in lines[0]

    # Read once: changing the variable afterwards changes nothing in this process.
    monkeypatch.setenv(CAD_COORDINATION_ENV, "on" if expected == COORDINATION_OFF else "off")
    listing = asyncio.run(list_returns(SimpleNamespace(app=application)))
    assert listing["coordination"] == expected


def test_a_listing_with_a_folder_carries_the_gate_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CAD_COORDINATION_ENV, "off")
    application = _app(tmp_path)
    folder = tmp_path / "wglink"
    folder.mkdir()
    workspace = SimpleNamespace(selected_path=lambda: folder)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        cad_workspace=workspace, cad_coordination=application.state.cad_coordination,
    )))
    listing = asyncio.run(list_returns(request))
    assert listing["cadFolderConfigured"] is True
    assert listing["coordination"] == COORDINATION_OFF


def test_an_application_assembled_without_the_read_answers_off(tmp_path: Path) -> None:
    folder = tmp_path / "wglink"
    folder.mkdir()
    bare = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        cad_workspace=SimpleNamespace(selected_path=lambda: folder),
    )))
    assert asyncio.run(list_returns(bare))["coordination"] == COORDINATION_OFF


def test_the_delivery_consumer_is_registered_whatever_the_gate_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate stops no transfer work: the consumer's start-up step is there either way."""

    names = {}
    for value in (None, "on", "off"):
        if value is None:
            monkeypatch.delenv(CAD_COORDINATION_ENV, raising=False)
        else:
            monkeypatch.setenv(CAD_COORDINATION_ENV, value)
        application = _app(tmp_path / (value or "default"))
        names[value] = {getattr(handler, "__name__", "") for handler in application.router.on_startup}
    assert "start_cad_delivery" in names["off"]
    assert names[None] == names["off"] == names["on"]
