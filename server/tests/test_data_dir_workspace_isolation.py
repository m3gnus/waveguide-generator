"""Data-root overrides must isolate implicit run exports, including startup."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from launch import serve
from server import app as app_module
from server.platform.paths import DATA_DIR_ENV, default_runs_dir, resolve_data_dir


@contextmanager
def forbid_access(*roots: Path):
    """Catch enumeration and writes even if startup swallows the error."""
    active = True
    touches = []
    normalized = [os.path.normcase(str(root.resolve())) for root in roots]

    def audit(event, args):
        if not active or (event != "open" and not event.startswith(("os.", "shutil."))):
            return
        for arg in args:
            if not isinstance(arg, (str, bytes, os.PathLike)):
                continue
            candidate = os.path.normcase(os.path.abspath(os.fsdecode(arg)))
            if any(candidate == root or candidate.startswith(root + os.sep) for root in normalized):
                touches.append((event, candidate))
                raise AssertionError(f"startup accessed an implicit external root: {event}")

    sys.addaudithook(audit)
    try:
        yield
    finally:
        active = False
    assert touches == []


@pytest.fixture(params=["missing", "populated"])
def isolated_startup(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_DOCUMENTS_DIR", str(home / "Documents"))
    documents = default_runs_dir()
    legacy = tmp_path / "checkout" / "output"
    if request.param == "populated":
        (legacy / "old-run").mkdir(parents=True)
    monkeypatch.setattr(app_module, "LEGACY_WORKSPACE_DIR", legacy)
    # Exercise the Windows startup registration without changing pathlib's OS.
    monkeypatch.setattr(app_module, "os", SimpleNamespace(name="nt", environ=os.environ))
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    monkeypatch.setattr(app_module, "FRONTEND_DIST", frontend)

    enumerated: list[Path] = []
    real_iterdir = Path.iterdir
    real_scandir = os.scandir

    def record_iterdir(path: Path):
        enumerated.append(path)
        return real_iterdir(path)

    def record_scandir(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            enumerated.append(Path(os.fsdecode(path)))
        return real_scandir(path)

    monkeypatch.setattr(Path, "iterdir", record_iterdir)
    monkeypatch.setattr(os, "scandir", record_scandir)
    repaired: list[tuple[Path, Path]] = []

    def repair(data: Path, workspace: Path):
        repaired.append((data, workspace))
        return {}

    monkeypatch.setattr(app_module, "repair_legacy_acls", repair)
    monkeypatch.setattr(app_module, "legacy_acl_repair_feedback", lambda *_args: {})
    return SimpleNamespace(
        documents=documents, home=home, legacy=legacy,
        enumerated=enumerated, repaired=repaired,
    )


def run_workspace_startup(application) -> None:
    # Run the real registered ACL startup hook; native solver prewarming is
    # unrelated to workspace selection and would start expensive workers.
    handler = next(
        hook for hook in application.router.on_startup
        if hook.__name__ == "_repair_legacy_acls"
    )
    asyncio.run(handler())


def assert_isolated(application, probe, data: Path) -> None:
    expected = data / "workspace"
    assert application.state.workspace.path() == expected
    assert application.state.workspace.selected_path() is None
    assert expected.is_dir()
    assert not (probe.home / "Documents").exists()
    assert probe.repaired == [(data, expected)]
    assert not any(
        path == probe.legacy or path.is_relative_to(probe.home / "Documents")
        for path in probe.enumerated
    )
    assert not (data / "workspace_settings.json").exists()


@pytest.mark.parametrize("source", ["argument", "environment"])
def test_create_app_data_override_isolates_workspace_startup(
    source, isolated_startup, tmp_path, monkeypatch,
) -> None:
    data = tmp_path / "data"
    if source == "argument":
        monkeypatch.delenv(DATA_DIR_ENV)
    else:
        monkeypatch.setenv(DATA_DIR_ENV, str(data))
    with forbid_access(isolated_startup.home / "Documents", isolated_startup.legacy):
        application = app_module.create_app(**({"data_dir": data} if source == "argument" else {}))
        run_workspace_startup(application)
    assert_isolated(application, isolated_startup, data)


@pytest.mark.parametrize("source", ["argument", "environment"])
@pytest.mark.parametrize("isolated_startup", [
    "missing",
    pytest.param("populated", marks=pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="A data override with an explicit workspace default still adopts checkout output",
    )),
], indirect=True)
def test_data_override_with_explicit_workspace_default_skips_legacy_adoption(
    source, isolated_startup, tmp_path, monkeypatch,
) -> None:
    data = tmp_path / "data"
    if source == "argument":
        monkeypatch.delenv(DATA_DIR_ENV)
    else:
        monkeypatch.setenv(DATA_DIR_ENV, str(data))
    expected = data / "exports"
    with forbid_access(isolated_startup.home / "Documents", isolated_startup.legacy):
        application = app_module.create_app(
            workspace_dir=expected, **({"data_dir": data} if source == "argument" else {}),
        )
        run_workspace_startup(application)
    assert application.state.workspace.path() == expected
    assert application.state.workspace.selected_path() is None
    assert isolated_startup.repaired == [(data, expected)]
    assert not (data / "workspace_settings.json").exists()


@pytest.mark.parametrize("source", ["argument", "environment", "absent", "empty"])
def test_launcher_startup_preserves_data_override_intent(
    source, isolated_startup, tmp_path, monkeypatch,
) -> None:
    data = tmp_path / "data"
    isolated = source in {"argument", "environment"}
    if source in {"argument", "absent"}:
        monkeypatch.delenv(DATA_DIR_ENV)
    elif source == "empty":
        monkeypatch.setenv(DATA_DIR_ENV, "")
    else:
        monkeypatch.setenv(DATA_DIR_ENV, str(data))
    monkeypatch.delenv("WG2_PORT", raising=False)
    monkeypatch.setattr(serve, "setup_logging", lambda *_args: None)
    monkeypatch.setattr(serve, "flush_logs", lambda: None)
    monkeypatch.setattr(serve, "_release_interface_error", lambda: None)
    monkeypatch.setattr(serve, "_no_gui_healthy_start", lambda *_args: None)
    monkeypatch.setattr(serve, "_start_temporary_session", lambda: None)
    monkeypatch.setattr(serve, "_start_beat_cpu_provisioning", lambda: None)
    monkeypatch.setattr(serve, "harden_console", lambda *_args: None)
    monkeypatch.setattr(serve, "InstanceLock", lambda *_args: SimpleNamespace(
        acquire=lambda *_args: None, update_port=lambda *_args: None, release=lambda: None,
    ))
    listener = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(serve, "reserve_port", lambda *_args, **_kwargs: (listener, 3100))
    applications = []

    class StartupServer:
        def __init__(self, config):
            applications.append(config.app)

        def run(self, *, sockets):
            assert sockets == [listener]
            run_workspace_startup(applications[-1])

    monkeypatch.setattr(serve.uvicorn, "Server", StartupServer)
    args = ["--no-browser"]
    if source == "argument":
        args += ["--data-dir", str(data)]
    guard = (
        forbid_access(isolated_startup.home / "Documents", isolated_startup.legacy)
        if isolated else nullcontext()
    )
    with guard:
        assert serve.main(args) == 0
    if isolated:
        assert_isolated(applications[0], isolated_startup, data)
    else:
        workspace = applications[0].state.workspace
        expected = (
            isolated_startup.legacy if isolated_startup.legacy.exists()
            else isolated_startup.documents
        )
        assert workspace.default_path == isolated_startup.documents
        assert workspace.path() == expected
        assert workspace.selected_path() == (
            isolated_startup.legacy if isolated_startup.legacy.exists() else None
        )
        assert isolated_startup.repaired == [(resolve_data_dir(), expected)]


@pytest.mark.parametrize("value", [None, ""])
def test_no_data_override_keeps_documents_and_legacy_adoption(
    value, isolated_startup, monkeypatch,
) -> None:
    if value is None:
        monkeypatch.delenv(DATA_DIR_ENV)
    else:
        monkeypatch.setenv(DATA_DIR_ENV, value)
    # Keep the normal platform data path inside the sandbox too.
    monkeypatch.setenv("APPDATA", str(isolated_startup.home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_DATA_HOME", str(isolated_startup.home / ".local" / "share"))
    application = app_module.create_app()
    run_workspace_startup(application)
    assert application.state.workspace.default_path == isolated_startup.documents
    expected = (
        isolated_startup.legacy if isolated_startup.legacy.exists()
        else isolated_startup.documents
    )
    assert application.state.workspace.path() == expected
    assert isolated_startup.repaired == [(resolve_data_dir(), expected)]


@pytest.mark.parametrize("source", ["argument", "environment"])
def test_data_override_keeps_saved_external_workspace(
    source, isolated_startup, tmp_path, monkeypatch,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    selected = tmp_path / "selected"
    selected.mkdir()
    settings = data / "workspace_settings.json"
    settings.write_text(json.dumps({"schemaVersion": 1, "workspacePath": str(selected)}))
    before = settings.read_bytes()
    if source == "argument":
        monkeypatch.delenv(DATA_DIR_ENV)
    else:
        monkeypatch.setenv(DATA_DIR_ENV, str(data))
    with forbid_access(isolated_startup.home / "Documents", isolated_startup.legacy):
        application = app_module.create_app(**({"data_dir": data} if source == "argument" else {}))
        run_workspace_startup(application)
    assert application.state.workspace.path() == selected
    assert isolated_startup.repaired == [(data, selected)]
    assert settings.read_bytes() == before
    assert not (isolated_startup.home / "Documents").exists()
