"""The status window's route to a log, which is the only one a dead backend has."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from launchers.statusapp.controller import StatusController


def controller_for(tmp_path: Path) -> StatusController:
    return StatusController(
        repo_root=tmp_path,
        environ={"WG2_DATA_DIR": str(tmp_path / "data")},
    )


def test_the_logs_directory_sits_under_the_data_directory(tmp_path: Path) -> None:
    controller = controller_for(tmp_path)
    assert controller.logs_dir == controller.data_dir / "logs"


def test_opening_the_logs_folder_creates_it_first(tmp_path: Path, monkeypatch) -> None:
    """A user who has never had a log still gets a window, not an error."""

    launched: list[list[str]] = []
    monkeypatch.setattr(
        "launchers.statusapp.controller.subprocess.Popen",
        lambda command, **_kwargs: launched.append(list(command)),
    )
    controller = controller_for(tmp_path)
    assert not controller.logs_dir.exists()

    target = controller.open_logs_folder()

    assert target.is_dir()
    assert launched and str(target) in launched[0]


def test_the_file_manager_command_matches_the_platform(tmp_path: Path, monkeypatch) -> None:
    launched: list[list[str]] = []
    monkeypatch.setattr(
        "launchers.statusapp.controller.subprocess.Popen",
        lambda command, **_kwargs: launched.append(list(command)),
    )
    controller_for(tmp_path).open_logs_folder()

    expected = {"win32": "explorer", "darwin": "open"}.get(sys.platform, "xdg-open")
    assert launched[0][0] == expected


def test_a_missing_file_manager_is_reported_and_not_raised(tmp_path: Path, monkeypatch) -> None:
    """A Linux box with no ``xdg-open`` must not take the status window down."""

    def explode(command, **_kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr("launchers.statusapp.controller.subprocess.Popen", explode)
    with pytest.raises(FileNotFoundError):
        controller_for(tmp_path).open_logs_folder()
    # The controller raises; the view is what converts it into a line of text,
    # which is asserted below without constructing a Tk window.


def test_the_view_reports_a_failure_instead_of_propagating_it() -> None:
    """A raise out of a Tk callback goes to a console this app does not have."""

    from launchers.statusapp.view import StatusView

    class Reason:
        def __init__(self) -> None:
            self.value = ""

        def set(self, value: str) -> None:
            self.value = value

    class Failing:
        def open_logs_folder(self):
            raise OSError("xdg-open is not installed")

    view = StatusView.__new__(StatusView)
    view.controller = Failing()  # type: ignore[assignment]
    view._frontend_reason = Reason()  # type: ignore[attr-defined]

    StatusView.open_logs(view)

    assert "xdg-open is not installed" in view._frontend_reason.value  # type: ignore[attr-defined]
