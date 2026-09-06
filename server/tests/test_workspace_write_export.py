from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
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


def select_endpoint(state: workspace_api.WorkspaceState):
    router = workspace_api.create_workspace_router(state)
    return next(route.endpoint for route in router.routes if route.path == "/api/workspace/select")


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


def _windows_acl(path: Path) -> list[str]:
    """The file's access-control entries, as `icacls` reports them."""

    output = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, check=True
    ).stdout
    # icacls prints `<path> <entry>` then one indented entry per line, then a
    # blank line and a summary. Keep the entries, drop the path they hang off.
    entries = []
    for line in output.splitlines():
        if not line.strip() or line.startswith("Successfully"):
            break
        entries.append(line.replace(str(path), "").strip())
    return sorted(entries)


def test_write_export_happy_path(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    response = call(state, request("horn_1", [("hor/a.frd", "one"), ("ver/b.frd", "two")]))

    assert response == {
        "directory": str(workspace / "horn_1"),
        "files": [str(workspace / "horn_1/hor/a.frd"), str(workspace / "horn_1/ver/b.frd")],
        "replaced": [],
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
    read_threads: set[str] = set()
    write_threads: set[str] = set()

    class RecordingUploadStream:
        """A disk-backed upload source that reports which thread reads it.

        No ``_rolled`` attribute, so Starlette classifies it exactly like the
        real rolled-to-disk upload the route receives and routes ``read()``
        through its threadpool -- unless someone puts the read back on the loop,
        which is what the recorded thread names are here to catch.
        """

        def __init__(self) -> None:
            self._file = tempfile.TemporaryFile()
            self._file.write(content)
            self._file.seek(0)

        def read(self, size: int = -1) -> bytes:
            read_threads.add(threading.current_thread().name)
            return self._file.read(size)

        def close(self) -> None:
            self._file.close()

    original_write_export_sync = workspace_api._write_export_sync

    def recording_write_export_sync(*args: object, **kwargs: object) -> dict[str, object]:
        write_threads.add(threading.current_thread().name)
        return original_write_export_sync(*args, **kwargs)

    monkeypatch.setattr(workspace_api, "_write_export_sync", recording_write_export_sync)

    def multipart_request() -> tuple[SimpleNamespace, object]:
        stream = RecordingUploadStream()
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
    original_staging = workspace_api.publish_staging_directory

    def counted_staging(*args, **kwargs):
        staging_calls.append((args, kwargs))
        return original_staging(*args, **kwargs)

    monkeypatch.setattr(workspace_api, "publish_staging_directory", counted_staging)
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
    # What this test is really about: reading a 64 MiB multipart body or
    # validating/hashing/writing it on the event loop stalled the loop. Assert
    # the two structural properties the fix put in place -- Starlette's
    # threadpool performs the upload read, and _write_export_sync runs via
    # asyncio.to_thread rather than on the loop -- and keep only a coarse
    # responsiveness bound. A wall-clock threshold tight enough to catch the
    # regression is not separable from scheduling jitter on a shared CI
    # runner, where this ticker missed the old 30 ms bound by 2.6 ms on
    # Windows with the loop never blocked at all.
    assert read_threads and "MainThread" not in read_threads
    assert write_threads and "MainThread" not in write_threads
    assert largest_gap < 1.0


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

    Manual exports cannot merge: the JSON and summary builders stamp the
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


def test_an_unreadable_pointer_is_refused_as_unreadable_not_as_another_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two refusals are different problems and must not share a message.

    Reported as one, the message sent everyone looking for colliding lineage
    identifiers when the file was simply unreadable -- and `lineageId: null`
    for an Untitled design compares equal to `lineageId: null`, so a lineage
    collision was never what was happening.
    """

    state, workspace = selected_state(tmp_path)
    pointer = json.dumps({"schemaVersion": 1, "folder": "Horn_A", "lineageId": None})
    call(
        state,
        workspace_api.WriteExportRequest(
            subdirectory="Horn_A",
            existing="merge_identical",
            members=[{"relative_path": "design.json", "text": pointer}],
        ),
    )
    published = workspace / "Horn_A/design.json"

    # Standing in for a file this account cannot open: on Windows that came
    # from an earlier run under another owner, and reproducing *that* needs a
    # second account. The refusal under test is driven by the read failing, so
    # the read is what the test controls.
    real_read_bytes = Path.read_bytes

    def refuse_the_pointer(self: Path, *args: object, **kwargs: object) -> bytes:
        if self == published:
            raise PermissionError(13, "Permission denied")
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", refuse_the_pointer)

    with pytest.raises(HTTPException) as caught:
        call(
            state,
            workspace_api.WriteExportRequest(
                subdirectory="Horn_A",
                existing="overwrite",
                members=[{"relative_path": "design.json", "text": pointer}],
            ),
        )

    assert caught.value.status_code == 409
    detail = str(caught.value.detail)
    assert "Cannot read" in detail
    assert "another lineage" not in detail
    # Actionable: which file, why, and what to do about it.
    assert str(published) in detail
    assert "Permission denied" in detail
    assert "Delete that file" in detail
    # Still a refusal: the guard is right, only its explanation was wrong.
    assert real_read_bytes(published).decode() == pointer


def test_published_files_are_readable_by_more_than_their_writer() -> None:
    """A published file inherits the destination's permissions, not staging's.

    `tempfile.mkdtemp` gives its directory mode 0o700, Windows implements that
    as a DACL naming only SYSTEM, Administrators and OWNER RIGHTS, and
    `os.replace` carries a file's DACL to the destination instead of letting
    the destination directory's inheritable entries apply. Every file the app
    published therefore landed unreadable to anyone but its writer -- including
    to the app itself once the owner changed, which is the refusal the previous
    test describes.

    Deliberately not `tmp_path`: pytest builds its base directory with the very
    mode this is about, so everything underneath already carries that ACL and a
    published file is indistinguishable from a correct one there. The workspace
    has to sit somewhere with ordinary permissions -- as a user's Documents
    folder does -- for the comparison to mean anything.
    """

    root = Path(tempfile.gettempdir()) / f"wg2-acl-{os.getpid()}-{id(object())}"
    root.mkdir()
    try:
        state = workspace_api.WorkspaceState(root / "data")
        workspace = root / "chosen"
        workspace.mkdir()
        state.select(workspace)
        workspace = workspace.resolve()

        # An ordinary file in the workspace, to compare the published one
        # against. It sits outside the export folder because with
        # `existing="reject"` the whole staging directory is moved into place,
        # so the export folder itself carried the private ACL and anything
        # created inside it inherited that rather than the workspace's.
        reference = workspace / "written-in-place.json"
        reference.write_bytes(b"{}")

        # `reject` moves the staging directory; `overwrite` moves each file.
        call(state, request("Horn_A", [("run.json", "{}")]))
        call(
            state,
            workspace_api.WriteExportRequest(
                subdirectory="Horn_A",
                existing="overwrite",
                members=[{"relative_path": "run.json", "text": "{ }"}],
            ),
        )

        if os.name == "nt":
            assert _windows_acl(workspace / "Horn_A/run.json") == _windows_acl(reference)
            assert _windows_acl(workspace / "Horn_A") == _windows_acl(workspace)
        else:
            assert (workspace / "Horn_A/run.json").stat().st_mode == reference.stat().st_mode
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
        "replaced": [],
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
        "replaced": [],
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


def test_workspace_select_accepts_a_typed_folder_without_the_native_picker(
    tmp_path: Path, monkeypatch
) -> None:
    """A browser away from the server must still be able to move the folder.

    The picker opens on the machine running WG, which is no help to someone
    reaching it from another machine -- and the CAD project archive lives in
    this folder, so being unable to change it there is being unable to choose
    where projects are kept.
    """

    state = workspace_api.WorkspaceState(tmp_path / "data")
    chosen = tmp_path / "typed"
    chosen.mkdir()

    def refuse(*args: object, **kwargs: object) -> str:
        raise AssertionError("a typed path must not open the native picker")

    monkeypatch.setattr(workspace_api, "_select_workspace_folder", refuse)

    result = asyncio.run(
        select_endpoint(state)(workspace_api.SelectWorkspaceRequest(path=str(chosen)))
    )

    assert result == {"selected": True, "path": str(chosen.resolve())}
    assert state.selected_path() == chosen.resolve()
    # Persisted, so the next process archives projects in the same place.
    assert workspace_api.WorkspaceState(tmp_path / "data").path() == chosen.resolve()


def test_workspace_select_refuses_a_typed_path_that_is_not_a_folder(tmp_path: Path) -> None:
    state = workspace_api.WorkspaceState(tmp_path / "data")
    missing = tmp_path / "not-there"

    with pytest.raises(HTTPException) as refusal:
        asyncio.run(
            select_endpoint(state)(workspace_api.SelectWorkspaceRequest(path=str(missing)))
        )

    assert refusal.value.status_code == 400
    assert state.selected_path() is None


def test_an_unreadable_export_file_is_refused_as_unreadable_not_as_different(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The results export path needs the same split the archive pointer got.

    This is the flow the Windows report actually came in on. Results exports
    are written with ``existing="merge_identical"`` (chart CSVs, plot PNGs,
    the report), and that branch compared the incoming bytes against the
    existing file to decide whether a retry was a no-op. When the existing file
    could not be opened, the comparison raised, the handler swallowed it as
    ``identical = False``, and the user was told the export "already exists
    with different content" -- a claim about bytes nobody had managed to read.

    Every file the app published carried the private staging ACL, so this is
    the message an installed 0.3.1 produced for an entire export set once the
    owner changed. The companion archive-pointer case is covered by
    ``test_an_unreadable_pointer_is_refused_as_unreadable_not_as_another_lineage``;
    only that one path was given the honest message when the ACL bug was fixed.
    """

    state, workspace = selected_state(tmp_path)
    call(state, request("Horn_A", [("Horn_A.csv", "frequency,level\n100,90\n")]))
    published = workspace / "Horn_A/Horn_A.csv"

    # Stands in for a file this account cannot open. Reproducing the real cause
    # needs a second Windows account, and the refusal under test is driven by
    # the read failing, so the read is what the test controls.
    real_open = Path.open

    def refuse_the_export(self: Path, *args: object, **kwargs: object):
        if self == published:
            raise PermissionError(13, "Permission denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_the_export)

    with pytest.raises(HTTPException) as caught:
        call(
            state,
            workspace_api.WriteExportRequest(
                subdirectory="Horn_A",
                existing="merge_identical",
                members=[
                    {"relative_path": "Horn_A.csv", "text": "frequency,level\n100,90\n"}
                ],
            ),
        )

    assert caught.value.status_code == 409
    detail = str(caught.value.detail)
    assert "Cannot read" in detail
    # The old message, and the reason this was misdiagnosed for so long.
    assert "different content" not in detail
    assert str(published) in detail
    assert "Permission denied" in detail

    # Still a refusal, and the file is untouched: the guard was always right.
    monkeypatch.undo()
    assert published.read_text() == "frequency,level\n100,90\n"


def _ordinary_root() -> Path:
    """A directory whose permissions inherit normally.

    Not `tmp_path`, for the reason recorded in
    `test_published_files_are_readable_by_more_than_their_writer` and again in
    `test_acl_repair.py`: pytest's base directory carries the very descriptor
    this is about, so a file published underneath it inherits correctly and the
    defect cannot be reproduced there.
    """

    root = Path(tempfile.gettempdir()) / f"wg2-repair-{os.getpid()}-{id(object())}"
    root.mkdir()
    return root


def _publish_via_staging(parent: Path, name: str, text: str) -> Path:
    """Reproduce the defect exactly: stage under mkdtemp, then `os.replace`."""

    staging = Path(tempfile.mkdtemp(prefix=".wg2-test-staging-", dir=parent))
    staged = staging / name
    staged.write_text(text, encoding="utf-8")
    published = parent / name
    os.replace(staged, published)
    os.rmdir(staging)
    return published


@pytest.mark.skipif(os.name != "nt", reason="Security descriptors are a Windows concept")
def test_an_unreadable_legacy_file_is_repaired_and_read_rather_than_refused() -> None:
    """The 409 is a last resort now, not a first response.

    The app is what made these files unreadable. Where the descriptor is still
    reachable, the honest thing is to put it back and carry on, so the user
    never sees a refusal for damage this application did and can undo.

    The read is failed once deliberately. A poisoned file whose owner has not
    changed is still readable -- OWNER RIGHTS grants its writer full control --
    so nothing but a forced failure exercises the retry, while the descriptor
    being repaired underneath it is entirely real.
    """

    from server.platform.acl_repair import descriptor_is_poisoned

    root = _ordinary_root()
    try:
        published = _publish_via_staging(root, "design.json", '{"schemaVersion": 1}')
        assert descriptor_is_poisoned(published), "test setup failed to reproduce it"

        attempts: list[int] = []

        def read_once_failing() -> bytes:
            attempts.append(1)
            if len(attempts) == 1:
                raise PermissionError(13, "Permission denied")
            return published.read_bytes()

        value = workspace_api._retry_after_acl_repair(published, read_once_failing)

        assert value == b'{"schemaVersion": 1}'
        assert len(attempts) == 2, "the read must be retried exactly once"
        assert not descriptor_is_poisoned(published), "the descriptor must be repaired"
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.skipif(os.name != "nt", reason="Security descriptors are a Windows concept")
def test_a_file_this_app_did_not_break_is_never_touched(tmp_path: Path) -> None:
    """No repair, no retry, and the caller's refusal stands.

    A descriptor that does not match staging's is somebody's deliberate choice.
    Widening it would be a worse failure than the refusal it replaces.
    """

    ordinary = tmp_path / "restricted.json"
    ordinary.write_text("{}", encoding="utf-8")
    attempts: list[int] = []

    def always_failing() -> bytes:
        attempts.append(1)
        raise PermissionError(13, "Permission denied")

    with pytest.raises(PermissionError):
        workspace_api._retry_after_acl_repair(ordinary, always_failing)

    assert len(attempts) == 1, "an unrepairable file must not be read twice"


def test_a_read_that_fails_after_a_repair_reports_the_original_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Repaired and still unreadable is a different fault, and stays a refusal."""

    target = tmp_path / "design.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        workspace_api,
        "repair_path",
        lambda _path, **_kwargs: workspace_api.Outcome.REPAIRED,
    )
    attempts: list[int] = []

    def always_failing() -> bytes:
        attempts.append(1)
        raise PermissionError(13, "Permission denied")

    with pytest.raises(PermissionError, match="Permission denied"):
        workspace_api._retry_after_acl_repair(target, always_failing)

    assert len(attempts) == 2


def test_a_successful_read_never_looks_at_the_descriptor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ordinary path must not pay for this at all.

    Every export member goes through here, so a descriptor read per file would
    be a cost on every export in exchange for nothing.
    """

    target = tmp_path / "design.json"

    def refuse_to_repair(_path, **_kwargs):
        raise AssertionError("repair must not be attempted when the read succeeds")

    monkeypatch.setattr(workspace_api, "repair_path", refuse_to_repair)

    assert workspace_api._retry_after_acl_repair(target, lambda: b"ok") == b"ok"


def destination_router(state: workspace_api.WorkspaceState, tmp_path: Path):
    """A router plus the store behind it, as `mount_workspace` wires them."""

    destinations = workspace_api.ExportDestinationStore(state.settings_path.parent)
    return destinations, workspace_api.create_workspace_router(state, destinations)


def route_endpoint(router, path: str, method: str):
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"no {method} {path}")


def test_export_destination_suggests_the_workspace_until_one_is_remembered(
    tmp_path: Path,
) -> None:
    """A first manual export defaults where every export went before.

    The suggestion is a path to read plus a handle to use; the handle is the
    only half the write endpoint accepts.
    """

    state, workspace = selected_state(tmp_path)
    destinations, router = destination_router(state, tmp_path)

    offer = asyncio.run(route_endpoint(router, "/api/workspace/export-destination", "GET")())

    assert offer["path"] == str(workspace)
    assert offer["remembered"] is False
    assert offer["selected"] is False
    assert destinations.resolve(offer["token"]) == workspace


def test_choosing_an_export_folder_leaves_the_workspace_where_it_was(
    tmp_path: Path,
) -> None:
    """The destination of one export is not the application's output folder.

    Repointing the workspace would move run archives, automatic exports and CAD
    projects as a side effect of answering "where should this STL go?".
    """

    state, workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    destinations, router = destination_router(state, tmp_path)
    choose = route_endpoint(router, "/api/workspace/export-destination", "POST")

    chosen = asyncio.run(
        choose(workspace_api.ChooseExportDestinationRequest(path=str(elsewhere)))
    )

    assert chosen["selected"] is True
    assert chosen["path"] == str(elsewhere.resolve())
    written = asyncio.run(
        route_endpoint(router, "/api/workspace/write-export", "POST")(
            workspace_api.WriteExportRequest(
                existing="overwrite",
                destination=chosen["token"],
                members=[{"relative_path": "horn.stl", "text": "solid"}],
            )
        )
    )

    # Directly in the folder the user chose: a picker that answers "Desktop"
    # and then writes into Desktop/<design>/ has not honoured the answer.
    assert written["directory"] == str(elsewhere.resolve())
    assert written["files"] == [str(elsewhere.resolve() / "horn.stl")]
    assert (elsewhere / "horn.stl").read_text() == "solid"
    assert state.selected_path() == workspace
    assert json.loads((tmp_path / "data" / "workspace_settings.json").read_text()) == {
        "schemaVersion": 1,
        "workspacePath": str(workspace),
    }
    assert not list(workspace.iterdir())


def test_the_next_export_defaults_to_the_folder_the_last_one_used(
    tmp_path: Path,
) -> None:
    state, _workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    destinations, router = destination_router(state, tmp_path)
    chosen = asyncio.run(
        route_endpoint(router, "/api/workspace/export-destination", "POST")(
            workspace_api.ChooseExportDestinationRequest(path=str(elsewhere))
        )
    )
    asyncio.run(
        route_endpoint(router, "/api/workspace/write-export", "POST")(
            workspace_api.WriteExportRequest(
                existing="overwrite",
                destination=chosen["token"],
                members=[{"relative_path": "horn.stl", "text": "solid"}],
            )
        )
    )

    offer = asyncio.run(route_endpoint(router, "/api/workspace/export-destination", "GET")())

    assert offer["path"] == str(elsewhere.resolve())
    assert offer["remembered"] is True
    assert destinations.resolve(offer["token"]) == elsewhere.resolve()


def test_a_chosen_folder_is_remembered_only_once_an_export_reaches_it(
    tmp_path: Path,
) -> None:
    """"Last used", not "last opened in a dialog".

    Picking a folder and then cancelling, or an export that fails, must leave
    the next dialog on the folder that actually received files.
    """

    state, workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    destinations, router = destination_router(state, tmp_path)

    asyncio.run(
        route_endpoint(router, "/api/workspace/export-destination", "POST")(
            workspace_api.ChooseExportDestinationRequest(path=str(elsewhere))
        )
    )

    offer = asyncio.run(route_endpoint(router, "/api/workspace/export-destination", "GET")())
    assert offer["path"] == str(workspace)
    assert offer["remembered"] is False
    assert not (tmp_path / "data" / "export_settings.json").exists()


def test_confirm_writes_what_is_new_and_skips_what_is_identical(tmp_path: Path) -> None:
    """No question when nothing the user has would change."""

    state, _workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    (elsewhere / "same.txt").write_text("unchanged")
    destinations, router = destination_router(state, tmp_path)
    token = destinations.issue(elsewhere)

    written = asyncio.run(
        route_endpoint(router, "/api/workspace/write-export", "POST")(
            workspace_api.WriteExportRequest(
                existing="confirm",
                destination=token,
                members=[
                    {"relative_path": "same.txt", "text": "unchanged"},
                    {"relative_path": "new.txt", "text": "fresh"},
                ],
            )
        )
    )

    assert not isinstance(written, JSONResponse)
    assert (elsewhere / "new.txt").read_text() == "fresh"
    assert (elsewhere / "same.txt").read_text() == "unchanged"
    assert written["replaced"] == []


def test_confirm_refuses_and_names_every_file_it_would_replace(tmp_path: Path) -> None:
    """The question is asked once, about the whole export, before any write.

    A manual export lands in a folder the user chose, which may hold files WG
    never wrote. Reporting the replacement afterwards is not consent, and asking
    per file would be the prompt storm that makes people stop reading.
    """

    state, _workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    (elsewhere / "one.txt").write_text("mine")
    (elsewhere / "two.txt").write_text("also mine")
    (elsewhere / "same.txt").write_text("unchanged")
    destinations, router = destination_router(state, tmp_path)
    token = destinations.issue(elsewhere)

    refused = asyncio.run(
        route_endpoint(router, "/api/workspace/write-export", "POST")(
            workspace_api.WriteExportRequest(
                existing="confirm",
                destination=token,
                members=[
                    {"relative_path": "one.txt", "text": "theirs"},
                    {"relative_path": "same.txt", "text": "unchanged"},
                    {"relative_path": "two.txt", "text": "theirs"},
                    {"relative_path": "new.txt", "text": "fresh"},
                ],
            )
        )
    )

    assert isinstance(refused, JSONResponse)
    assert refused.status_code == 409
    body = json.loads(refused.body)
    assert body["code"] == "export_collision"
    assert body["directory"] == str(elsewhere.resolve())
    # Every one of them, and only the ones that differ.
    assert body["paths"] == [
        str(elsewhere.resolve() / "one.txt"),
        str(elsewhere.resolve() / "two.txt"),
    ]
    # Nothing at all: not the new file either, so declining leaves the folder
    # exactly as it was.
    assert not (elsewhere / "new.txt").exists()
    assert (elsewhere / "one.txt").read_text() == "mine"
    assert (elsewhere / "two.txt").read_text() == "also mine"


def test_the_answered_question_is_repeated_as_an_overwrite(tmp_path: Path) -> None:
    """The handle survives the refusal, so the retry needs no second dialog."""

    state, _workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    (elsewhere / "one.txt").write_text("mine")
    destinations, router = destination_router(state, tmp_path)
    token = destinations.issue(elsewhere)
    write = route_endpoint(router, "/api/workspace/write-export", "POST")
    members = [{"relative_path": "one.txt", "text": "theirs"}]

    assert isinstance(
        asyncio.run(write(workspace_api.WriteExportRequest(
            existing="confirm", destination=token, members=members,
        ))),
        JSONResponse,
    )
    written = asyncio.run(write(workspace_api.WriteExportRequest(
        existing="overwrite", destination=token, members=members,
    )))

    assert written["replaced"] == [str(elsewhere.resolve() / "one.txt")]
    assert (elsewhere / "one.txt").read_text() == "theirs"


def test_confirm_refuses_a_directory_in_the_way_rather_than_listing_it(
    tmp_path: Path,
) -> None:
    state, _workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    (elsewhere / "one.txt").mkdir()
    destinations, router = destination_router(state, tmp_path)
    token = destinations.issue(elsewhere)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            route_endpoint(router, "/api/workspace/write-export", "POST")(
                workspace_api.WriteExportRequest(
                    existing="confirm",
                    destination=token,
                    members=[{"relative_path": "one.txt", "text": "theirs"}],
                )
            )
        )

    assert caught.value.status_code == 409
    assert "not a file" in caught.value.detail
    assert (elsewhere / "one.txt").is_dir()


def test_confirm_into_an_empty_folder_asks_nothing(tmp_path: Path) -> None:
    state, _workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    destinations, router = destination_router(state, tmp_path)

    written = asyncio.run(
        route_endpoint(router, "/api/workspace/write-export", "POST")(
            workspace_api.WriteExportRequest(
                existing="confirm",
                destination=destinations.issue(elsewhere),
                members=[{"relative_path": "horn.stl", "text": "solid"}],
            )
        )
    )

    assert written["files"] == [str(elsewhere.resolve() / "horn.stl")]


def test_the_automatic_policy_still_refuses_on_the_first_difference(
    tmp_path: Path,
) -> None:
    """`merge_identical` is unchanged: a background write asks nobody anything."""

    state, workspace = selected_state(tmp_path)
    call(
        state,
        workspace_api.WriteExportRequest(
            subdirectory="horn_1",
            existing="merge_identical",
            members=[{"relative_path": "a.csv", "text": "original"}],
        ),
    )

    with pytest.raises(HTTPException, match="different content"):
        call(
            state,
            workspace_api.WriteExportRequest(
                subdirectory="horn_1",
                existing="merge_identical",
                members=[{"relative_path": "a.csv", "text": "replacement"}],
            ),
        )

    assert (workspace / "horn_1/a.csv").read_text() == "original"


def test_cancelling_the_destination_dialog_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, workspace = selected_state(tmp_path)
    destinations, router = destination_router(state, tmp_path)
    monkeypatch.setattr(workspace_api, "_select_workspace_folder", lambda *a, **k: None)

    answer = asyncio.run(
        route_endpoint(router, "/api/workspace/export-destination", "POST")()
    )

    assert answer["selected"] is False
    # The standing suggestion is still named, so a cancelled dialog leaves the
    # user exactly where it found them -- but no handle is minted for it. The
    # client keeps the one it already has, and a cancelled picker must not evict
    # a live handle to hand back a folder nobody asked for.
    assert answer["path"] == str(workspace)
    assert answer["token"] is None
    assert not list(workspace.iterdir())
    assert not (tmp_path / "data" / "export_settings.json").exists()


def test_the_picker_opens_in_the_folder_the_last_export_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remembering the folder is only useful if the dialog starts there."""

    state, _workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    destinations, router = destination_router(state, tmp_path)
    destinations.remember(elsewhere.resolve())
    opened: list[tuple[str, Path | None]] = []

    def picker(prompt: str = "", start_in: Path | None = None) -> str | None:
        opened.append((prompt, start_in))
        return str(elsewhere)

    monkeypatch.setattr(workspace_api, "_select_workspace_folder", picker)
    asyncio.run(route_endpoint(router, "/api/workspace/export-destination", "POST")())

    assert opened == [("Choose export folder", elsewhere.resolve())]


def test_an_unknown_export_destination_handle_is_refused(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    _destinations, router = destination_router(state, tmp_path)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            route_endpoint(router, "/api/workspace/write-export", "POST")(
                workspace_api.WriteExportRequest(
                    existing="overwrite",
                    destination="not-a-handle",
                    members=[{"relative_path": "horn.stl", "text": "solid"}],
                )
            )
        )

    assert caught.value.status_code == 409
    assert "Choose the folder again" in caught.value.detail
    assert not list(workspace.iterdir())


def test_an_expired_handle_stops_naming_its_folder(tmp_path: Path) -> None:
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    store = workspace_api.ExportDestinationStore(tmp_path / "data")
    token = store.issue(elsewhere, now=0.0)

    assert store.resolve(token, now=workspace_api.EXPORT_DESTINATION_TTL_SECONDS) == (
        elsewhere.resolve()
    )
    with pytest.raises(KeyError):
        store.resolve(token, now=workspace_api.EXPORT_DESTINATION_TTL_SECONDS + 1)


def test_only_the_newest_handles_are_kept(tmp_path: Path) -> None:
    """A long-lived server does not accumulate handles nobody spent."""

    store = workspace_api.ExportDestinationStore(tmp_path / "data")
    folders = []
    for index in range(workspace_api.MAX_EXPORT_DESTINATIONS + 1):
        folder = tmp_path / f"folder_{index}"
        folder.mkdir()
        folders.append((folder, store.issue(folder)))

    with pytest.raises(KeyError):
        store.resolve(folders[0][1])
    assert store.resolve(folders[-1][1]) == folders[-1][0].resolve()


def test_a_handle_to_a_deleted_folder_is_refused_rather_than_recreating_it(
    tmp_path: Path,
) -> None:
    state, _workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "removable"
    elsewhere.mkdir()
    destinations, router = destination_router(state, tmp_path)
    token = destinations.issue(elsewhere)
    elsewhere.rmdir()

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            route_endpoint(router, "/api/workspace/write-export", "POST")(
                workspace_api.WriteExportRequest(
                    existing="overwrite",
                    destination=token,
                    members=[{"relative_path": "horn.stl", "text": "solid"}],
                )
            )
        )

    assert caught.value.status_code == 409
    assert not elsewhere.exists()


def test_a_chosen_destination_cannot_be_escaped_by_a_member_path(
    tmp_path: Path,
) -> None:
    state, _workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    destinations, router = destination_router(state, tmp_path)
    token = destinations.issue(elsewhere)

    with pytest.raises((HTTPException, ValidationError)):
        asyncio.run(
            route_endpoint(router, "/api/workspace/write-export", "POST")(
                workspace_api.WriteExportRequest(
                    existing="overwrite",
                    destination=token,
                    members=[{"relative_path": "../escaped.stl", "text": "solid"}],
                )
            )
        )

    assert not (tmp_path / "escaped.stl").exists()


def test_an_export_into_the_chosen_folder_never_replaces_the_folder(
    tmp_path: Path,
) -> None:
    """`reject` publishes by renaming a directory over the destination.

    Aimed at a folder the user already owns, that would delete everything in it,
    so the combination is refused before anything is staged.
    """

    state, _workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("mine")
    destinations, router = destination_router(state, tmp_path)
    token = destinations.issue(elsewhere)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            route_endpoint(router, "/api/workspace/write-export", "POST")(
                workspace_api.WriteExportRequest(
                    existing="reject",
                    destination=token,
                    members=[{"relative_path": "horn.stl", "text": "solid"}],
                )
            )
        )

    assert caught.value.status_code == 422
    assert (elsewhere / "keep.txt").read_text() == "mine"


def test_a_repeat_export_names_the_files_it_replaced(tmp_path: Path) -> None:
    state, _workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    (elsewhere / "horn.stl").write_text("older")
    destinations, router = destination_router(state, tmp_path)
    token = destinations.issue(elsewhere)

    written = asyncio.run(
        route_endpoint(router, "/api/workspace/write-export", "POST")(
            workspace_api.WriteExportRequest(
                existing="overwrite",
                destination=token,
                members=[{"relative_path": "horn.stl", "text": "newer"}],
            )
        )
    )

    assert written["replaced"] == [str(elsewhere.resolve() / "horn.stl")]
    assert (elsewhere / "horn.stl").read_text() == "newer"


def test_a_workspace_export_still_needs_a_subdirectory(tmp_path: Path) -> None:
    """Only a chosen folder may be written into directly.

    Every export shares the workspace, so one that named no folder would drop
    its files among the run archives.
    """

    with pytest.raises(ValidationError):
        workspace_api.WriteExportRequest(
            members=[{"relative_path": "horn.stl", "text": "solid"}],
        )


def test_a_chosen_destination_works_while_the_workspace_is_unavailable(
    tmp_path: Path,
) -> None:
    """The two folders are independent, including when one is gone.

    An unplugged workspace volume refuses automatic export, and must not also
    refuse an export the user is aiming somewhere that exists.
    """

    data = tmp_path / "data"
    state = workspace_api.WorkspaceState(data, default_path=tmp_path / "default")
    absent = tmp_path / "chosen"
    absent.mkdir()
    state.select(absent)
    absent.rmdir()
    state = workspace_api.WorkspaceState(data, default_path=tmp_path / "default")
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    destinations, router = destination_router(state, tmp_path)
    token = destinations.issue(elsewhere)

    written = asyncio.run(
        route_endpoint(router, "/api/workspace/write-export", "POST")(
            workspace_api.WriteExportRequest(
                existing="overwrite",
                destination=token,
                members=[{"relative_path": "horn.stl", "text": "solid"}],
            )
        )
    )

    assert written["files"] == [str(elsewhere.resolve() / "horn.stl")]


def test_multipart_transport_carries_the_destination_handle(tmp_path: Path) -> None:
    """The shape the browser actually sends, not only the legacy JSON model."""

    state, workspace = selected_state(tmp_path)
    elsewhere = tmp_path / "desktop"
    elsewhere.mkdir()
    destinations, router = destination_router(state, tmp_path)
    token = destinations.issue(elsewhere)
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
            field("subdirectory", b""),
            field("existing", b"overwrite"),
            field("destination", token.encode("ascii")),
            field("relative_path", b"horn.stl"),
            field("file", b"solid", "horn.stl"),
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
    written = asyncio.run(
        route_endpoint(router, "/api/workspace/write-export", "POST")(request_value)
    )

    assert written["files"] == [str(elsewhere.resolve() / "horn.stl")]
    assert (elsewhere / "horn.stl").read_bytes() == b"solid"
    assert not list(workspace.iterdir())


def test_multipart_without_a_subdirectory_or_a_destination_is_refused(
    tmp_path: Path,
) -> None:
    state, workspace = selected_state(tmp_path)
    _destinations, router = destination_router(state, tmp_path)
    boundary = b"wg-boundary"
    body = b"".join(
        [
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="relative_path"\r\n\r\nhorn.stl\r\n',
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="file"; filename="horn.stl"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\nsolid\r\n",
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

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            route_endpoint(router, "/api/workspace/write-export", "POST")(request_value)
        )

    assert caught.value.status_code == 422
    assert not list(workspace.iterdir())
