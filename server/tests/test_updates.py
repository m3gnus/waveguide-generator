"""Update API contracts over the signed full-installer service."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys
import threading

from fastapi import FastAPI, HTTPException
import pytest

from server.updates.api import mount_updates
from server.updates.installer_service import UpdateInstallUnavailable


class FakeService:
    """The lifecycle hooks mount_updates registers, doing nothing."""

    def start_checker(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_status_endpoint_runs_the_blocking_service_off_loop(tmp_path: Path):
    class Service(FakeService):
        def get_status(self, *, force: bool = False) -> dict[str, object]:
            return {"force": force, "thread": threading.get_ident()}

    app = FastAPI()
    mount_updates(
        app,
        running_version="2.0.0",
        data_dir=tmp_path,
        repo_root=tmp_path,
        service=Service(),  # type: ignore[arg-type]
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/updates/status")
    main_thread = threading.get_ident()
    regular = asyncio.run(endpoint(refresh=False))
    refreshed = asyncio.run(endpoint(refresh=True))
    assert regular["force"] is False and regular["thread"] != main_thread
    assert refreshed["force"] is True and refreshed["thread"] != main_thread


@pytest.mark.parametrize("home,system", [
    ("/home/test-reporter", "Linux"),
    (r"C:\Users\test-reporter", "Windows"),
])
def test_installer_api_scrubs_status_progress_and_logs_without_changing_install_state(
    tmp_path: Path, monkeypatch, home: str, system: str,
) -> None:
    from copy import deepcopy

    from server.diagnostics.scrub import scrub_rules
    from server.updates.installer_service import InstallerUpdateService

    rules = scrub_rules(home=home, environ={}, system=system)
    monkeypatch.setattr("server.diagnostics.scrub.scrub_rules", lambda: rules)
    message = f"Could not read {home}/update-install/installer.part"
    snapshot = {
        "canInstall": True, "installState": "failed", "error": message,
        "lastError": message, "checkout": {"installRoot": f"{home}/WG"},
        "lastOutcome": {"log": f"{home}/logs/install.log", "backupPath": f"{home}/saved.previous",
                        "journalPath": f"{home}/install-journal.json", "error": message},
    }
    progress = {"installState": "failed", "error": message}

    class Client:
        def get_status(self, **_kwargs):
            return deepcopy(snapshot)

        def request_download(self, _callback):
            return progress

        def reset_download(self):
            return progress

    update = InstallerUpdateService(running_version="2.0.0", data_dir=tmp_path,
        repo_root=tmp_path, update_request_path=tmp_path / "request.json", client=Client())
    logs = tmp_path / "update-install"
    logs.mkdir()
    (logs / "install.log").write_text(message, encoding="utf-8")
    app = FastAPI()
    mount_updates(app, running_version="2.0.0", data_dir=tmp_path, repo_root=tmp_path, service=update)

    def call(path, **kwargs):
        endpoint = next(route.endpoint for route in app.routes if route.path == path)
        return asyncio.run(endpoint(**kwargs))

    for refresh in (False, True):
        shown = call("/api/updates/status", refresh=refresh)
        assert shown["lastOutcome"] == {
            "log": "~/logs/install.log", "backupPath": "~/saved.previous",
            "journalPath": "~/install-journal.json", "error": "Could not read ~/update-install/installer.part",
        }
        assert shown["checkout"]["installRoot"] == "~/WG"
        assert shown["lastError"] == shown["error"] == "Could not read ~/update-install/installer.part"
    for path, confirmation in (("/api/updates/install", "install"), ("/api/updates/reset", "reset")):
        assert call(path, confirmation=confirmation)["error"] == "Could not read ~/update-install/installer.part"
    assert call("/api/updates/diagnostics")["logs"]["install.log"] == "Could not read ~/update-install/installer.part"
    assert update.get_status()["lastOutcome"] == snapshot["lastOutcome"]
    assert update.get_status()["checkout"]["installRoot"] == f"{home}/WG"
    assert progress["error"] == message
    assert (logs / "install.log").read_text(encoding="utf-8") == message


def test_install_request_errors_do_not_disclose_the_home_folder(tmp_path: Path) -> None:
    class ConflictingService(FakeService):
        def request_install(self):
            raise UpdateInstallUnavailable(f"Could not write {Path.home() / 'WG/installer.part'}")

    app = FastAPI()
    mount_updates(app, running_version="2.0.0", data_dir=tmp_path, repo_root=tmp_path,
                  service=ConflictingService())
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/updates/install")
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(endpoint(confirmation="install"))
    assert conflict.value.status_code == 409
    assert conflict.value.detail == f"Could not write {Path('~') / 'WG/installer.part'}"


def test_the_update_service_imports_no_cad_link_module(tmp_path: Path):
    """Importing the update service in a fresh interpreter loads nothing from ``server.cadlink``.

    So the two packages can never import each other at module level.
    """

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, server.updates.installer_service; "
            "print(sorted(name for name in sys.modules if name.startswith('server.cadlink')))",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "[]", result.stdout


def test_install_endpoint_requires_confirmation_and_runs_off_loop(tmp_path: Path):
    class Service(FakeService):
        def request_install(self) -> dict[str, object]:
            return {
                "accepted": True,
                "tag": "v2.0.1",
                "thread": threading.get_ident(),
            }

    app = FastAPI()
    mount_updates(
        app,
        running_version="2.0.0",
        data_dir=tmp_path,
        repo_root=tmp_path,
        service=Service(),  # type: ignore[arg-type]
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/updates/install")
    main_thread = threading.get_ident()

    with pytest.raises(HTTPException) as denied:
        asyncio.run(endpoint(confirmation=None))
    assert denied.value.status_code == 403

    accepted = asyncio.run(endpoint(confirmation="install"))
    assert accepted["accepted"] is True
    assert accepted["thread"] != main_thread


def test_install_endpoint_reports_active_job_conflicts_as_409(tmp_path: Path) -> None:
    class ConflictingService(FakeService):
        def request_install(self) -> dict[str, object]:
            raise UpdateInstallUnavailable(
                "Update 2.0.1 is already active; wait before installing 2.0.2."
            )

    app = FastAPI()
    mount_updates(
        app,
        running_version="2.0.0",
        data_dir=tmp_path,
        repo_root=tmp_path,
        service=ConflictingService(),  # type: ignore[arg-type]
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/updates/install")

    with pytest.raises(HTTPException) as conflict:
        asyncio.run(endpoint(confirmation="install"))

    assert conflict.value.status_code == 409
    assert "2.0.1" in conflict.value.detail
