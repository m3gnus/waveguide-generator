from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from server.workspace import api as workspace_api


def endpoint(state: workspace_api.WorkspaceState):
    router = workspace_api.create_workspace_router(state)
    return next(route.endpoint for route in router.routes if route.path == "/api/workspace/write-export")


def path_endpoint(state: workspace_api.WorkspaceState):
    router = workspace_api.create_workspace_router(state)
    return next(route.endpoint for route in router.routes if route.path == "/api/workspace/path")


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


def test_write_export_requires_selected_workspace(tmp_path: Path) -> None:
    state = workspace_api.WorkspaceState(tmp_path / "data")
    with pytest.raises(HTTPException, match="No workspace folder") as caught:
        call(state, request("horn_1", [("a.frd", "one")]))
    assert caught.value.status_code == 409


def test_deleted_workspace_selection_is_stale_and_is_not_recreated(tmp_path: Path) -> None:
    state, workspace = selected_state(tmp_path)
    workspace.rmdir()

    response = asyncio.run(path_endpoint(state)())

    assert response["selected"] is False
    assert response["path"] != str(workspace)
    assert not workspace.exists()


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
