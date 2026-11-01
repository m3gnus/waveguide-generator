"""A plain sentence for the user never costs the developers the original reason."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.jobs.runtime.core import JobResourceUnavailableError, JobRuntime


def _runtime(snapshot: dict[str, Any] | None = None) -> Any:
    async def start() -> None:
        return None

    async def artifact(_job_id: str) -> bytes:
        return b"not an npz"

    return SimpleNamespace(
        start=start,
        get_radiation_impedance=artifact,
        store=SimpleNamespace(get_archive_snapshot=lambda _job_id: snapshot),
    )


def _snapshot(**overrides: Any) -> dict[str, Any]:
    return {
        "job": {"status": "complete"},
        "results_text": json.dumps({"frequencies": []}),
        "channel_bases": None,
        "radiation_impedance": None,
        **overrides,
    }


def _detail_logged(caplog: pytest.LogCaptureFixture, marker: str) -> bool:
    return any(marker in record.getMessage() and record.levelno >= logging.WARNING for record in caplog.records)


def test_a_truncated_port_matrix_keeps_its_parse_failure_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(JobResourceUnavailableError) as refused:
        asyncio.run(JobRuntime.get_radiation_impedance_presentation(_runtime(), "job"))
    assert str(refused.value) == "WG could not read this run’s port data. Solve the design again."
    assert refused.value.__cause__ is not None
    assert _detail_logged(caplog, str(refused.value.__cause__))


@pytest.mark.parametrize(
    ("overrides", "sentence"),
    [
        ({"results_text": "{"}, "WG could not read this run’s results. Solve the design again."),
        ({"channel_bases": b"not an npz"}, "WG could not read this run’s saved response. Solve the design again."),
        ({"radiation_impedance": b"not an npz"}, "WG could not read this run’s port data. Solve the design again."),
    ],
)
def test_an_archive_snapshot_keeps_its_parse_failure_in_the_log(
    overrides: dict[str, Any], sentence: str, caplog: pytest.LogCaptureFixture
) -> None:
    runtime = _runtime(_snapshot(**overrides))
    with caplog.at_level(logging.WARNING), pytest.raises(JobResourceUnavailableError) as refused:
        asyncio.run(JobRuntime.get_archive_snapshot(runtime, "job"))
    assert str(refused.value) == sentence
    assert refused.value.__cause__ is not None
    assert _detail_logged(caplog, str(refused.value.__cause__))


def test_the_logs_folder_failure_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import server.diagnostics.api as diagnostics

    def refused(*_args: object, **_kwargs: object) -> None:
        raise OSError("no file manager here")

    monkeypatch.setattr(diagnostics.subprocess, "Popen", refused)
    app = FastAPI()
    app.include_router(diagnostics.create_diagnostics_router(data_dir=tmp_path, version="0", build={}, engine_registry=None))
    with caplog.at_level(logging.WARNING):
        response = TestClient(app).post("/api/diagnostics/open-logs")
    assert response.status_code == 500
    assert "no file manager here" not in response.text
    assert _detail_logged(caplog, "no file manager here")


def test_an_unreadable_export_file_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from server.tests.test_workspace_write_export import call, request, selected_state, workspace_api

    state, workspace = selected_state(tmp_path)
    call(state, request("Horn_A", [("Horn_A.csv", "frequency,level\n100,90\n")]))
    published = workspace / "Horn_A/Horn_A.csv"
    real_open = Path.open

    def refuse(self: Path, *args: object, **kwargs: object) -> Any:
        if self == published:
            raise PermissionError(13, "Access refused by the test")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse)
    payload = workspace_api.WriteExportRequest(subdirectory="Horn_A", existing="merge_identical",
                                               members=[{"relative_path": "Horn_A.csv", "text": "frequency,level\n100,90\n"}])
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as caught:
        call(state, payload)
    assert "Access refused by the test" not in str(caught.value.detail)
    assert _detail_logged(caplog, "Access refused by the test")
