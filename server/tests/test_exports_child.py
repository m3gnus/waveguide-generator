"""STEP routes contain native failures in the shared, killable mesh child."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time

from fastapi import HTTPException
import pytest

from server.exports import api, core
from server.mesh import child


def _failed_geometry(design_dump):
    Path(os.environ["WG_TEST_EXPORT_MARKER"]).write_text(str(os.getpid()))
    if os.environ["WG_TEST_EXPORT_FAILURE"] == "crash":
        os._exit(7)
    time.sleep(120)


def _probe():
    return os.getpid()


def _request():
    return api.ExportRequest.model_validate({
        "design": {"formula": "OSSE", "L": 40, "a": 45},
        "designRevision": 0,
    })


@pytest.fixture
def isolated_exports(monkeypatch):
    monkeypatch.delenv(child.IN_PROCESS_ENV, raising=False)
    child.close_mesher_child()
    yield
    child.close_mesher_child()


@pytest.mark.parametrize("body", ["surface", "solid"])
@pytest.mark.parametrize("failure", ["crash", "hang"])
def test_step_child_failure_is_a_clear_error_and_parent_survives(
    body, failure, monkeypatch, tmp_path, isolated_exports,
):
    marker = tmp_path / "geometry-pid"
    monkeypatch.setenv("WG_TEST_EXPORT_MARKER", str(marker))
    monkeypatch.setenv("WG_TEST_EXPORT_FAILURE", failure)
    monkeypatch.setenv(child.TIMEOUT_ENV, "15")
    name = "_build_step_sync" if body == "surface" else "_build_step_solid_sync"
    monkeypatch.setattr(core, name, _failed_geometry)

    async def scenario():
        with pytest.raises(HTTPException) as refused:
            await api.export_step(_request(), body=body)
        assert refused.value.status_code == 503
        assert marker.exists(), "the geometry function never entered the child"
        assert int(marker.read_text()) != os.getpid()
        expected = child.CRASH_MESSAGE if failure == "crash" else "did not finish within 15 s"
        assert expected in refused.value.detail
        # A fresh child can answer in the same surviving server process.
        monkeypatch.setenv(child.TIMEOUT_ENV, "30")
        assert await child.run_mesh_build(_probe) != os.getpid()

    asyncio.run(scenario())


@pytest.mark.parametrize("body", ["surface", "solid"])
def test_step_geometry_and_result_cross_the_real_child(body, isolated_exports):
    response = asyncio.run(api.export_step(_request(), body=body))
    assert response.status_code == 200
    text = response.body.decode("utf-8")
    core._assert_step(text)
    assert "SI_UNIT(.MILLI.,.METRE.)" in text
