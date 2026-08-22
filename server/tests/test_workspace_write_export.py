from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import FormData, UploadFile

from server import app as app_module
from server.platform import paths
from server.workspace import api as workspace_api


def endpoint(state: workspace_api.WorkspaceState):
    router = workspace_api.create_workspace_router(state)
    return next(route.endpoint for route in router.routes if route.path == "/api/workspace/write-export")


def path_endpoint(state: workspace_api.WorkspaceState):
    router = workspace_api.create_workspace_router(state)
    return next(route.endpoint for route in router.routes if route.path == "/api/workspace/path")


def cad_path_endpoint(state: workspace_api.CadWorkspaceState):
    router = workspace_api.create_cad_workspace_router(state)
    return next(route.endpoint for route in router.routes if route.path == "/api/cad-workspace/path")


def cad_select_endpoint(state: workspace_api.CadWorkspaceState):
    router = workspace_api.create_cad_workspace_router(state)
    return next(route.endpoint for route in router.routes if route.path == "/api/cad-workspace/select")


def request(subdirectory: str, members: list[tuple[str, str]]):
    return workspace_api.WriteExportRequest(
        subdirectory=subdirectory,
        members=[{"relative_path": path, "text": text} for path, text in members],
    )


def selected_state(tmp_path: Path) -> tuple[workspace_api.WorkspaceState, Path]:
    state = workspace_api.WorkspaceState(tmp_path / "data")
    workspace = tmp_path / "chosen"
    workspace.mkdir()
    state.select(workspace)
    return state, workspace.resolve()


def call(state: workspace_api.WorkspaceState, payload: workspace_api.WriteExportRequest):
    return asyncio.run(endpoint(state)(payload))


def test_write_export_happy_path(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    response = call(state, request("horn_1", [("hor/a.frd", "one"), ("ver/b.frd", "two")]))

    assert response == {
        "directory": str(workspace / "horn_1"),
        "files": [str(workspace / "horn_1/hor/a.frd"), str(workspace / "horn_1/ver/b.frd")],
    }
    assert (workspace / "horn_1/hor/a.frd").read_text() == "one"
    assert (workspace / "horn_1/ver/b.frd").read_text() == "two"


def test_cad_workspace_is_separate_and_requires_a_selection(tmp_path: Path) -> None:
    data = tmp_path / "data"
    output = workspace_api.WorkspaceState(data, default_path=tmp_path / "output")
    proposed = tmp_path / "proposed" / "cadlink"
    cad = workspace_api.CadWorkspaceState(data, proposed_path=proposed)

    assert output.path() == (tmp_path / "output").resolve()
    assert asyncio.run(cad_path_endpoint(cad)()) == {
        "selected": False,
        "path": None,
        "proposed": str(proposed),
        "proposedExists": False,
        "captureDocument": True,
        "captureMode": "run",
    }
    assert not proposed.exists()
    with pytest.raises(ValueError, match="No WGLink folder"):
        cad.path()

    exchange = tmp_path / "fusion-exchange"
    exchange.mkdir()
    cad.select(exchange)
    assert cad.path() == exchange.resolve()
    assert json.loads((data / "cadlink_settings.json").read_text()) == {
        "schemaVersion": 1,
        "cadLinkPath": str(exchange.resolve()),
        "captureDocument": True,
        "captureMode": "run",
    }


def test_cad_workspace_accepts_a_manual_path_when_no_native_picker_exists(
    tmp_path: Path,
) -> None:
    state = workspace_api.CadWorkspaceState(tmp_path / "data")
    exchange = tmp_path / "manual-exchange"
    exchange.mkdir()
    payload = workspace_api.SelectCadWorkspaceRequest(path=str(exchange))

    result = asyncio.run(cad_select_endpoint(state)(payload))

    assert result == {"selected": True, "path": str(exchange.resolve())}
    assert state.selected_path() == exchange.resolve()


def test_cad_workspace_adopts_the_previous_shared_selection(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    old = tmp_path / "old-shared-workspace"
    old.mkdir()
    (old / "wglink").mkdir()
    (data / "workspace_settings.json").write_text(
        json.dumps({"schemaVersion": 1, "workspacePath": str(old)}),
        encoding="utf-8",
    )

    cad = workspace_api.CadWorkspaceState(data)
    assert cad.selected_path() == old.resolve()
    assert json.loads((data / "cadlink_settings.json").read_text()) == {
        "schemaVersion": 1,
        "cadLinkPath": str(old.resolve()),
    }

    # The migration is durable: changing the output selection cannot move CAD.
    newer_output = tmp_path / "new-output"
    newer_output.mkdir()
    workspace_api.WorkspaceState(data).select(newer_output)
    assert workspace_api.CadWorkspaceState(data).selected_path() == old.resolve()


def test_output_only_legacy_selection_does_not_silently_configure_cad(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    output_only = tmp_path / "exports"
    output_only.mkdir()
    (data / "workspace_settings.json").write_text(
        json.dumps({"schemaVersion": 1, "workspacePath": str(output_only)}),
        encoding="utf-8",
    )

    cad = workspace_api.CadWorkspaceState(data)
    assert cad.selected_path() is None
    assert not (data / "cadlink_settings.json").exists()


def test_binary_auto_export_merges_new_files_and_accepts_identical_retries(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    first = workspace_api.WriteExportRequest(
        subdirectory="horn_1",
        existing="merge_identical",
        members=[
            {
                "relative_path": "horn_1_plot.png",
                "content_base64": base64.b64encode(b"\x89PNG\r\n").decode("ascii"),
            }
        ],
    )
    call(state, first)
    retry = workspace_api.WriteExportRequest(
        subdirectory="horn_1",
        existing="merge_identical",
        members=[
            first.members[0].model_dump(),
            {"relative_path": "horn_1.csv", "text": "frequency,level\n100,90\n"},
        ],
    )

    response = call(state, retry)

    assert response["files"] == [
        str(workspace / "horn_1/horn_1_plot.png"),
        str(workspace / "horn_1/horn_1.csv"),
    ]
    assert (workspace / "horn_1/horn_1_plot.png").read_bytes() == b"\x89PNG\r\n"
    assert (workspace / "horn_1/horn_1.csv").read_text() == "frequency,level\n100,90\n"


def test_large_multipart_export_is_responsive_and_identical_retry_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, workspace = selected_state(tmp_path)
    content = b"x" * (64 * 1024 * 1024)

    def multipart_request() -> tuple[SimpleNamespace, object]:
        stream = tempfile.TemporaryFile()
        stream.write(content)
        stream.seek(0)
        upload = UploadFile(stream, filename="large.bin")
        form = FormData(
            [
                ("subdirectory", "large-run"),
                ("existing", "merge_identical"),
                ("relative_path", "large.bin"),
                ("file", upload),
            ]
        )

        async def read_form() -> FormData:
            return form

        return (
            SimpleNamespace(
                headers={"content-type": "multipart/form-data; boundary=test"},
                form=read_form,
            ),
            stream,
        )

    async def write_with_ticker() -> tuple[dict[str, object], float]:
        gaps: list[float] = []
        stop = asyncio.Event()

        async def ticker() -> None:
            previous = asyncio.get_running_loop().time()
            while not stop.is_set():
                await asyncio.sleep(0.005)
                current = asyncio.get_running_loop().time()
                gaps.append(current - previous)
                previous = current

        request_value, stream = multipart_request()
        ticker_task = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        try:
            response = await endpoint(state)(request_value)
        finally:
            stop.set()
            await ticker_task
            stream.close()
        return response, max(gaps)

    response, largest_gap = asyncio.run(write_with_ticker())
    destination = workspace / "large-run" / "large.bin"
    initial_mtime = destination.stat().st_mtime_ns
    staging_calls: list[object] = []
    original_mkdtemp = workspace_api.tempfile.mkdtemp

    def counted_mkdtemp(*args, **kwargs):
        staging_calls.append((args, kwargs))
        return original_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(workspace_api.tempfile, "mkdtemp", counted_mkdtemp)
    retry_request, retry_stream = multipart_request()
    try:
        retry = asyncio.run(endpoint(state)(retry_request))
    finally:
        retry_stream.close()

    assert response == retry
    assert destination.stat().st_size == len(content)
    assert destination.read_bytes() == content
    assert destination.stat().st_mtime_ns == initial_mtime
    assert staging_calls == []
    assert largest_gap < 0.03


def test_multipart_transport_pairs_repeated_paths_with_binary_parts(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    boundary = b"wg-boundary"

    def field(name: str, value: bytes, filename: str | None = None) -> bytes:
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        content_type = (
            b"Content-Type: application/octet-stream\r\n" if filename else b""
        )
        return (
            b"--" + boundary + b"\r\n" + disposition.encode("ascii") + b"\r\n"
            + content_type + b"\r\n" + value + b"\r\n"
        )

    body = b"".join(
        [
            field("subdirectory", b"binary-run"),
            field("existing", b"merge_identical"),
            field("relative_path", b"nested/first.bin"),
            field("relative_path", b"second.bin"),
            field("file", b"\x00\x01\xff", "first.bin"),
            field("file", b"second\x00member", "second.bin"),
            b"--" + boundary + b"--\r\n",
        ]
    )
    delivered = False

    async def receive() -> dict[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    from starlette.requests import Request

    request_value = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/workspace/write-export",
            "headers": [
                (b"content-type", b"multipart/form-data; boundary=" + boundary)
            ],
        },
        receive,
    )
    response = asyncio.run(endpoint(state)(request_value))

    assert response["files"] == [
        str(workspace / "binary-run/nested/first.bin"),
        str(workspace / "binary-run/second.bin"),
    ]
    assert (workspace / "binary-run/nested/first.bin").read_bytes() == b"\x00\x01\xff"
    assert (workspace / "binary-run/second.bin").read_bytes() == b"second\x00member"


def test_merge_refuses_to_overwrite_a_different_existing_export(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    call(
        state,
        workspace_api.WriteExportRequest(
            subdirectory="horn_1",
            existing="merge_identical",
            members=[{"relative_path": "horn_1.csv", "text": "original"}],
        ),
    )
    conflicting = workspace_api.WriteExportRequest(
        subdirectory="horn_1",
        existing="merge_identical",
        members=[{"relative_path": "horn_1.csv", "text": "replacement"}],
    )

    with pytest.raises(HTTPException, match="different content") as caught:
        call(state, conflicting)

    assert caught.value.status_code == 409
    assert (workspace / "horn_1/horn_1.csv").read_text() == "original"


def test_repeat_manual_export_replaces_changed_files(tmp_path: Path) -> None:
    """A user asking for an export again gets the export again.

    Manual exports cannot merge: the JSON, summary and VACS builders stamp the
    current time into their output, so a second export of the same run is never
    byte-identical and ``merge_identical`` rejected the entire bundle.
    """

    state, workspace = selected_state(tmp_path)
    call(
        state,
        workspace_api.WriteExportRequest(
            subdirectory="horn_1",
            existing="merge_identical",
            members=[{"relative_path": "horn_1.json", "text": '{"timestamp": "first"}'}],
        ),
    )

    response = call(
        state,
        workspace_api.WriteExportRequest(
            subdirectory="horn_1",
            existing="overwrite",
            members=[
                {"relative_path": "horn_1.json", "text": '{"timestamp": "second"}'},
                {"relative_path": "horn_1_summary.txt", "text": "new file"},
            ],
        ),
    )

    assert response["files"] == [
        str(workspace / "horn_1/horn_1.json"),
        str(workspace / "horn_1/horn_1_summary.txt"),
    ]
    assert (workspace / "horn_1/horn_1.json").read_text() == '{"timestamp": "second"}'
    assert (workspace / "horn_1/horn_1_summary.txt").read_text() == "new file"


def test_archive_pointer_refuses_to_overwrite_another_lineage(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    original = json.dumps(
        {"schemaVersion": 1, "folder": "Horn_A", "lineageId": "wgl_first"}
    )
    replacement = json.dumps(
        {"schemaVersion": 1, "folder": "Horn_A", "lineageId": "wgl_second"}
    )
    call(
        state,
        workspace_api.WriteExportRequest(
            subdirectory="Horn_A",
            existing="merge_identical",
            members=[{"relative_path": "design.json", "text": original}],
        ),
    )

    with pytest.raises(HTTPException, match="another lineage") as caught:
        call(
            state,
            workspace_api.WriteExportRequest(
                subdirectory="Horn_A",
                existing="overwrite",
                members=[{"relative_path": "design.json", "text": replacement}],
            ),
        )

    assert caught.value.status_code == 409
    assert (workspace / "Horn_A/design.json").read_text() == original


def test_overwrite_refuses_to_replace_a_directory_with_a_file(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    (workspace / "horn_1/horn_1.json").mkdir(parents=True)

    with pytest.raises(HTTPException, match="is a directory") as caught:
        call(
            state,
            workspace_api.WriteExportRequest(
                subdirectory="horn_1",
                existing="overwrite",
                members=[{"relative_path": "horn_1.json", "text": "replacement"}],
            ),
        )

    assert caught.value.status_code == 409
    assert (workspace / "horn_1/horn_1.json").is_dir()


def test_overwrite_leaves_no_staging_directory_behind(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    payload = workspace_api.WriteExportRequest(
        subdirectory="horn_1",
        existing="overwrite",
        members=[{"relative_path": "horn_1.csv", "text": "frequency,level\n"}],
    )

    call(state, payload)
    call(state, payload)

    assert sorted(item.name for item in workspace.iterdir()) == ["horn_1"]
    assert sorted(item.name for item in (workspace / "horn_1").iterdir()) == ["horn_1.csv"]


def test_write_export_rejects_invalid_binary_encoding_without_writing(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    payload = workspace_api.WriteExportRequest(
        subdirectory="horn_1",
        members=[{"relative_path": "bad.png", "content_base64": "not base64!"}],
    )

    with pytest.raises(HTTPException, match="invalid base64") as caught:
        call(state, payload)

    assert caught.value.status_code == 422
    assert list(workspace.iterdir()) == []


def test_write_export_uses_visible_default_without_folder_selection(tmp_path: Path) -> None:
    workspace = tmp_path / "waveguide-generator" / "output"
    state = workspace_api.WorkspaceState(
        tmp_path / "data",
        default_path=workspace,
    )

    response = call(state, request("horn_1", [("a.frd", "one")]))

    assert response == {
        "directory": str(workspace / "horn_1"),
        "files": [str(workspace / "horn_1" / "a.frd")],
    }
    assert (workspace / "horn_1" / "a.frd").read_text() == "one"
    assert state.selected_path() is None


def test_deleted_workspace_selection_refuses_exports_until_it_returns(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    default = tmp_path / "default"
    state = workspace_api.WorkspaceState(data, default_path=default)
    workspace = tmp_path / "chosen"
    workspace.mkdir()
    state.select(workspace)
    workspace = workspace.resolve()
    workspace.rmdir()
    # A restart while the selected volume is absent must retain the configured
    # path, not silently adopt the default for the rest of that process.
    state = workspace_api.WorkspaceState(data, default_path=default)

    response = asyncio.run(path_endpoint(state)())

    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    assert json.loads(response.body) == {
        "code": "workspace_unavailable",
        "detail": f"The selected workspace folder is unavailable: {workspace}",
        "path": str(workspace),
    }
    refused = call(state, request("horn_1", [("a.frd", "one")]))
    assert isinstance(refused, JSONResponse)
    assert refused.status_code == 409
    assert json.loads(refused.body)["code"] == "workspace_unavailable"
    assert not workspace.exists()
    assert not default.exists()
    assert json.loads((data / "workspace_settings.json").read_text()) == {
        "schemaVersion": 1,
        "workspacePath": str(workspace),
    }

    workspace.mkdir()

    assert asyncio.run(path_endpoint(state)()) == {
        "path": str(workspace),
        "selected": True,
    }
    assert call(state, request("horn_1", [("a.frd", "one")])) == {
        "directory": str(workspace / "horn_1"),
        "files": [str(workspace / "horn_1" / "a.frd")],
    }
    assert (workspace / "horn_1" / "a.frd").read_text() == "one"


def test_failed_workspace_selection_keeps_the_previous_persisted_choice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "data"
    state = workspace_api.WorkspaceState(data)
    previous = tmp_path / "previous"
    replacement = tmp_path / "replacement"
    previous.mkdir()
    replacement.mkdir()
    state.select(previous)
    settings_before = (data / "workspace_settings.json").read_bytes()

    def fail_write(_path: Path, _payload: dict[str, object]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(workspace_api, "_write_json_atomic", fail_write)

    with pytest.raises(OSError, match="disk full"):
        state.select(replacement)

    assert state.selected_path() == previous.resolve()
    assert state.path() == previous.resolve()
    assert (data / "workspace_settings.json").read_bytes() == settings_before


@pytest.mark.parametrize("path", ["../escape.frd", "hor/../../escape.frd"])
def test_write_export_rejects_parent_traversal(tmp_path: Path, path: str) -> None:
    state, workspace = selected_state(tmp_path)
    with pytest.raises(HTTPException, match=r"\.\."):
        call(state, request("horn_1", [(path, "bad")]))
    assert list(workspace.iterdir()) == []


@pytest.mark.parametrize("path", ["/tmp/escape.frd", r"C:\\escape.frd", r"\\server\\share\\escape.frd"])
def test_write_export_rejects_absolute_paths(tmp_path: Path, path: str) -> None:
    state, workspace = selected_state(tmp_path)
    with pytest.raises(HTTPException, match="relative path"):
        call(state, request("horn_1", [(path, "bad")]))
    assert list(workspace.iterdir()) == []


def test_write_export_rejects_symlink_escape(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(HTTPException, match="outside the selected workspace"):
        call(state, request("linked/export", [("a.frd", "bad")]))
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("path", ["CON", "aux.txt", "hor/COM1.frd", "ver/NUL.frd"])
def test_write_export_rejects_reserved_device_names(tmp_path: Path, path: str) -> None:
    state, workspace = selected_state(tmp_path)
    with pytest.raises(HTTPException, match="reserved Windows device"):
        call(state, request("horn_1", [(path, "bad")]))
    assert list(workspace.iterdir()) == []


def test_write_export_rejects_oversize_before_writing(tmp_path: Path, monkeypatch) -> None:
    state, workspace = selected_state(tmp_path)
    monkeypatch.setattr(workspace_api, "MAX_EXPORT_BYTES", 5)
    with pytest.raises(HTTPException, match="size limit"):
        call(state, request("horn_1", [("first.frd", "1234"), ("second.frd", "56")]))
    assert list(workspace.iterdir()) == []


def test_workspace_export_request_envelope_accommodates_base64_expansion() -> None:
    encoded_binary_limit = 4 * ((workspace_api.MAX_EXPORT_BYTES + 2) // 3)

    assert workspace_api.MAX_EXPORT_REQUEST_BODY_BYTES > encoded_binary_limit


def test_request_body_middleware_uses_the_workspace_route_limit() -> None:
    async def downstream(scope, receive, send) -> None:
        await receive()
        await JSONResponse({"status": "accepted"})(scope, receive, send)

    middleware = app_module._RequestBodyLimitMiddleware(
        downstream,
        max_body_bytes=5,
        path_limits={"/api/workspace/write-export": 10},
    )

    async def post(path: str, body: bytes) -> tuple[int, dict[str, str]]:
        messages: list[dict] = []

        async def receive() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict) -> None:
            messages.append(message)

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "headers": [(b"content-length", str(len(body)).encode("ascii"))],
            },
            receive,
            send,
        )
        start = next(item for item in messages if item["type"] == "http.response.start")
        raw = b"".join(
            item.get("body", b"")
            for item in messages
            if item["type"] == "http.response.body"
        )
        return start["status"], json.loads(raw)

    workspace_status, _workspace_body = asyncio.run(
        post("/api/workspace/write-export", b"123456")
    )
    default_status, default_body = asyncio.run(post("/api/design/symmetry", b"123456"))
    oversized_status, oversized_body = asyncio.run(
        post("/api/workspace/write-export", b"12345678901")
    )

    assert workspace_status == 200
    assert default_status == 413
    assert "5 bytes" in default_body["detail"]
    assert oversized_status == 413
    assert "10 bytes" in oversized_body["detail"]


def test_invalid_member_count_is_rejected_without_writing(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    with pytest.raises(ValidationError):
        request("horn_1", [])
    assert list(workspace.iterdir()) == []


@pytest.mark.parametrize(
    "members",
    [
        [
            ("hor/\N{LATIN SMALL LETTER E WITH ACUTE}.frd", "first"),
            ("hor/e\N{COMBINING ACUTE ACCENT}.frd", "second"),
        ],
        [("hor/Angle.frd", "first"), ("hor/angle.frd", "second")],
    ],
)
def test_write_export_rejects_portably_equivalent_member_paths(
    tmp_path: Path, members: list[tuple[str, str]]
) -> None:
    state, workspace = selected_state(tmp_path)

    with pytest.raises(HTTPException, match="duplicates another member path"):
        call(state, request("horn_1", members))

    assert list(workspace.iterdir()) == []


def test_write_export_rejects_oversize_path_segment_before_writing(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)

    with pytest.raises(HTTPException, match="255-byte"):
        call(state, request(f"parent/{'x' * 256}", [("a.frd", "bad")]))

    assert list(workspace.iterdir()) == []


def test_run_exports_default_to_the_visible_documents_folder() -> None:
    home = Path("/home/example")
    root = paths.documents_root(system="Linux", environ={}, home=home)
    expected_root = (home / "Documents" / "Waveguide Generator").absolute()

    assert root == expected_root
    assert paths.default_runs_dir(system="Linux", environ={}, home=home) == root / "runs"
    assert (
        paths.proposed_cadlink_dir(system="Linux", environ={}, home=home)
        == root / "cadlink"
    )


def test_documents_root_follows_the_platform_convention() -> None:
    windows = paths.documents_root(
        system="Windows", environ={"USERPROFILE": "C:\\Users\\example"}, home=Path("/ignored")
    )
    assert windows.parts[-2:] == ("Documents", "Waveguide Generator")

    xdg = paths.documents_root(
        system="Linux", environ={"XDG_DOCUMENTS_DIR": "/home/example/Documenten"}, home=Path("/home/example")
    )
    assert xdg == Path("/home/example/Documenten/Waveguide Generator").absolute()


def test_a_legacy_default_holding_runs_is_adopted_not_abandoned(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    legacy = tmp_path / "checkout" / "output"
    (legacy / "horn_1").mkdir(parents=True)

    state = workspace_api.WorkspaceState(
        data, default_path=tmp_path / "documents" / "runs", legacy_defaults=(legacy,)
    )

    assert state.selected_path() == legacy.resolve()
    assert json.loads((data / "workspace_settings.json").read_text()) == {
        "schemaVersion": 1,
        "workspacePath": str(legacy.resolve()),
    }


def test_an_empty_legacy_default_is_left_behind(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    legacy = tmp_path / "checkout" / "output"
    legacy.mkdir(parents=True)
    (legacy / ".DS_Store").write_text("", encoding="utf-8")
    documents = tmp_path / "documents" / "runs"

    state = workspace_api.WorkspaceState(
        data, default_path=documents, legacy_defaults=(legacy,)
    )

    assert state.selected_path() is None
    assert state.path() == documents.resolve()
    assert not (data / "workspace_settings.json").exists()


def test_an_explicit_selection_survives_the_default_move(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    legacy = tmp_path / "checkout" / "output"
    (legacy / "horn_1").mkdir(parents=True)
    (data / "workspace_settings.json").write_text(
        json.dumps({"schemaVersion": 1, "workspacePath": str(chosen)}), encoding="utf-8"
    )

    state = workspace_api.WorkspaceState(
        data, default_path=tmp_path / "documents" / "runs", legacy_defaults=(legacy,)
    )

    assert state.path() == chosen.resolve()


def test_accepting_the_proposed_cad_folder_creates_only_that_folder(tmp_path: Path) -> None:
    data = tmp_path / "data"
    proposed = tmp_path / "documents" / "Waveguide Generator" / "cadlink"
    state = workspace_api.CadWorkspaceState(data, proposed_path=proposed)

    result = asyncio.run(
        cad_select_endpoint(state)(
            workspace_api.SelectCadWorkspaceRequest(path=str(proposed))
        )
    )

    assert result == {"selected": True, "path": str(proposed.resolve())}
    assert proposed.is_dir()


def test_a_mistyped_cad_folder_is_refused_rather_than_created(tmp_path: Path) -> None:
    data = tmp_path / "data"
    proposed = tmp_path / "documents" / "cadlink"
    state = workspace_api.CadWorkspaceState(data, proposed_path=proposed)
    typo = tmp_path / "documents" / "cadlnik"

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            cad_select_endpoint(state)(
                workspace_api.SelectCadWorkspaceRequest(path=str(typo))
            )
        )

    assert excinfo.value.status_code == 400
    assert not typo.exists()
    assert state.selected_path() is None


def test_the_picker_only_starts_where_it_can_safely_be_pointed(tmp_path: Path) -> None:
    existing = tmp_path / "documents"
    existing.mkdir()

    assert workspace_api._picker_start_directory(existing / "runs") == existing
    assert workspace_api._picker_start_directory(None) is None
    # A quote would break the AppleScript and PowerShell strings the path is
    # embedded in, and the root is not a useful place to open a dialog.
    assert workspace_api._picker_start_directory(tmp_path / "it's here") is None
    assert workspace_api._picker_start_directory(Path("/nonexistent/deep/path")) is None


def capture_endpoint(state: workspace_api.CadWorkspaceState):
    router = workspace_api.create_cad_workspace_router(state)
    return next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/cad-workspace/capture-document"
    )


def test_capturing_the_cad_document_is_on_by_default_and_can_be_declined(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    state = workspace_api.CadWorkspaceState(data, proposed_path=tmp_path / "proposed")
    assert state.capture_document is True
    assert state.capture_mode == "run"

    result = asyncio.run(
        capture_endpoint(state)(workspace_api.CaptureDocumentRequest(enabled=False))
    )

    assert result == {"captureDocument": False, "captureMode": "off"}
    # The Fusion add-in reads this same file, so the choice has to be in it.
    assert json.loads((data / "cadlink_settings.json").read_text()) == {
        "schemaVersion": 1,
        "captureDocument": False,
        "captureMode": "off",
    }
    assert workspace_api.CadWorkspaceState(data).capture_document is False


def test_filing_the_cad_document_per_project_still_asks_the_addin_to_capture(
    tmp_path: Path,
) -> None:
    """The add-in's switch is the boolean; the mode is only where WG files it.

    An add-in that predates the mode key reads ``captureDocument`` alone, so
    every mode other than ``off`` must keep writing it true.
    """

    data = tmp_path / "data"
    state = workspace_api.CadWorkspaceState(data, proposed_path=tmp_path / "proposed")

    result = asyncio.run(
        capture_endpoint(state)(workspace_api.CaptureDocumentRequest(mode="project"))
    )

    assert result == {"captureDocument": True, "captureMode": "project"}
    assert json.loads((data / "cadlink_settings.json").read_text()) == {
        "schemaVersion": 1,
        "captureDocument": True,
        "captureMode": "project",
    }
    assert workspace_api.CadWorkspaceState(data).capture_mode == "project"


def test_a_settings_file_that_only_knew_the_boolean_reads_as_a_mode(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "cadlink_settings.json").write_text(
        json.dumps({"schemaVersion": 1, "captureDocument": True}), encoding="utf-8"
    )
    assert workspace_api.CadWorkspaceState(data).capture_mode == "run"

    (data / "cadlink_settings.json").write_text(
        json.dumps({"schemaVersion": 1, "captureDocument": False}), encoding="utf-8"
    )
    assert workspace_api.CadWorkspaceState(data).capture_mode == "off"


def test_an_unknown_capture_mode_is_refused(tmp_path: Path) -> None:
    state = workspace_api.CadWorkspaceState(tmp_path / "data")
    with pytest.raises(ValueError):
        state.set_capture_mode("everywhere")  # type: ignore[arg-type]


def test_choosing_a_folder_does_not_erase_the_capture_choice(tmp_path: Path) -> None:
    data = tmp_path / "data"
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    state = workspace_api.CadWorkspaceState(data)
    state.set_capture_mode("off")

    # Selecting used to rewrite the whole file, which dropped the other setting.
    state.select(exchange)

    reloaded = workspace_api.CadWorkspaceState(data)
    assert reloaded.selected_path() == exchange.resolve()
    assert reloaded.capture_document is False


def test_a_folder_chosen_before_the_setting_existed_still_captures(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (data / "cadlink_settings.json").write_text(
        json.dumps({"schemaVersion": 1, "cadLinkPath": str(exchange)}), encoding="utf-8"
    )

    state = workspace_api.CadWorkspaceState(data)

    assert state.selected_path() == exchange.resolve()
    assert state.capture_document is True


def test_v1_task_scratch_is_never_adopted_as_an_export_folder(
    tmp_path: Path, monkeypatch
) -> None:
    """The data directory's ``workspace`` is migrated v1 job scratch.

    It holds one UUID folder per task, so the "has run folders" evidence test
    matches it on hundreds of directories that are not exports at all. It is not
    offered as a legacy default for that reason; this pins the reason.
    """

    # Whether the checkout's ignored legacy output/ happens to contain runs is
    # unrelated to the v1 data-directory scratch classification under test.
    monkeypatch.setattr(app_module, "LEGACY_WORKSPACE_DIR", tmp_path / "legacy-output")
    data = tmp_path / "data"
    scratch = data / "workspace" / "05446457-3c9d-4d53-9723-dc019ff9e4c3"
    scratch.mkdir(parents=True)
    (scratch / "task.manifest.json").write_text("{}", encoding="utf-8")
    documents = tmp_path / "documents" / "runs"

    application = app_module.create_app(
        data_dir=data, workspace_dir=documents, solver_warmup=False
    )

    assert application.state.workspace.selected_path() is None
    assert application.state.workspace.path() == documents.resolve()
