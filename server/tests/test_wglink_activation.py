"""WGLink activation: after a confirmed start, never while Fusion is open, decided under the lock.

The updater review §2.5 and ``docs/architecture/CAD-OPERATIONS.md``, "WGLink
activation". Fusion itself cannot run here: every test answers "is Fusion
running" and, where it matters, "is this start confirmed" explicitly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import functools
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from server.cadlink import addin_update


PIN_A = "a" * 40
PIN_B = "b" * 40
PIN_C = "c" * 40
OLD = "d" * 40
BUILD_A = {"version": "0.3.3", "commit": "1" * 40, "runtimeId": "rt-a"}
BUILD_B = {"version": "0.3.4", "commit": "2" * 40, "runtimeId": "rt-b"}
BUILD_C = {"version": "0.3.5", "commit": "3" * 40, "runtimeId": "rt-c"}
REAL_ROOT = Path(addin_update.app_root())


def CONFIRMED() -> tuple[bool, str]:
    return True, "no update transaction is open for this installation"


def FUSION_OPEN() -> bool:
    return True


def FUSION_CLOSED() -> bool:
    return False


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(addin_update, "fusion_process_state", lambda: "closed")
    monkeypatch.setattr(addin_update, "_running_builds", {})
    monkeypatch.setattr(addin_update, "_report", None)
    monkeypatch.setattr(addin_update, "_retry_not_before", 0.0)
    monkeypatch.setattr(addin_update, "_retry_task", None)
    monkeypatch.delenv("WG2_BUNDLE", raising=False)
    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path / "data"))


def _spec(pin: str) -> dict[str, object]:
    return {
        "schema": 1,
        "repository": "https://example.invalid/hornlab-fusion-addin.git",
        "commit": pin,
        "license": "AGPL-3.0-or-later",
        "addinVersion": "0.1.1",
    }


def _build(root: Path, build: dict[str, str], pin: str) -> None:
    """Make ``root`` the app layer of one build: its manifest, version and WGLink pin."""

    (root / "APP-MANIFEST.json").write_text(json.dumps(build), encoding="utf-8")
    (root / "shared" / "version.json").write_text(
        json.dumps({"version": build["version"]}), encoding="utf-8"
    )
    (root / "integrations" / "wglink" / "source.json").write_text(
        json.dumps(_spec(pin)), encoding="utf-8"
    )


def _populate(root: Path, build: dict[str, str] = BUILD_A, pin: str = PIN_A) -> Path:
    (root / "integrations" / "wglink").mkdir(parents=True, exist_ok=True)
    (root / "shared").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in ("install_wglink.py", "build_wglink_package.py"):
        shutil.copyfile(REAL_ROOT / "scripts" / name, root / "scripts" / name)
    _build(root, build, pin)
    return root.resolve()


def _marker(target: Path, *, root: Path, commit: str) -> None:
    (target / "wglink_install.json").write_text(
        json.dumps({
            "schema": 1,
            "managedBy": "waveguide-generator",
            "waveguideGeneratorRoot": str(root),
            "waveguideGeneratorVersion": "0.3.1",
            "sourceCommit": commit,
            "addinVersion": "0.1.1",
        }),
        encoding="utf-8",
    )


def _installed(addins: Path, *, commit: str, root: Path) -> Path:
    target = addins / "WGLink"
    target.mkdir(parents=True)
    (target / "WGLink.py").write_text("# add-in\n", encoding="utf-8")
    _marker(target, root=root, commit=commit)
    return target.resolve()


def _fake_installer(
    monkeypatch,
    calls: list[dict[str, object]],
    *,
    addins: Path | None = None,
    held: dict[str, bool] | None = None,
    on_lock=None,
    before_swap=None,
) -> None:
    """The real installer, except that a replacement only records what it was asked.

    ``held`` tracks the real ``.WGLink-install.lock``; ``on_lock`` runs the
    moment it is acquired, before anything is decided under it. Like the real
    one, the fake asks ``should_proceed`` before it moves anything, and
    ``before_swap`` runs just before that question.
    """

    real = addin_update._installer

    def loaded(root_path: Path):
        module = real(root_path)
        if addins is not None:
            module.default_addins_dir = lambda _platform: addins
        if held is not None:
            real_lock = module._operation_lock

            @contextmanager
            def spied(addins_dir: Path, **kwargs):
                with real_lock(addins_dir, **kwargs):
                    held["now"] = True
                    try:
                        if on_lock is not None:
                            on_lock()
                        yield
                    finally:
                        held["now"] = False

            module._operation_lock = spied

        def install_unlocked(**kwargs):
            if before_swap is not None:
                before_swap()
            proceed = kwargs.get("should_proceed")
            if proceed is not None and not proceed():
                return "deferred", Path(kwargs["addins_dir"]) / "WGLink"
            pin = addin_update.pinned_commit(Path(kwargs["root"]))
            calls.append({**kwargs, "pin": pin, "locked": None if held is None else held["now"]})
            target = Path(kwargs["addins_dir"]) / "WGLink"
            target.mkdir(parents=True, exist_ok=True)
            (target / "WGLink.py").write_text("# installed\n", encoding="utf-8")
            _marker(target, root=Path(kwargs["root"]), commit=str(pin))
            return "installed", target

        module._install_unlocked = install_unlocked
        return module

    monkeypatch.setattr(addin_update, "_installer", loaded)


def _pending(data: Path, root: Path) -> dict[str, object]:
    return json.loads(addin_update.pending_path(data, root).read_text(encoding="utf-8"))


def _shipped(root: Path, pin: str, content: bytes = b"verified WGLink package") -> Path:
    archive = root / "integrations" / "wglink" / "packages" / f"wglink-0.3.3-{pin}.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(content)
    return archive


# -- 1. Only after this start is confirmed ------------------------------------


def test_nothing_changes_before_this_start_is_confirmed(tmp_path: Path, monkeypatch) -> None:
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)

    activation = addin_update.activate_wglink(
        root=root,
        addins_dir=addins,
        data_dir=data,
        fusion_running=FUSION_CLOSED,
        confirmed=lambda: (False, "update transaction t1 (state 'installed') is open"),
    )

    assert activation.verdict == "awaiting-startup"
    assert "only once this start is confirmed" in activation.detail
    assert "t1" in activation.detail
    assert calls == []
    assert addin_update.installed_commit(target) == OLD
    assert not addin_update.pending_path(data, root).exists()


def _bundle_layout(tmp_path: Path) -> tuple[Path, Path]:
    """An installed bundle's app layer, and its data directory."""

    if sys.platform == "darwin":
        root = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources" / "app"
    else:
        root = tmp_path / "Waveguide Generator" / "app"
    data = tmp_path / "data"
    data.mkdir()
    return _populate(root), data.resolve()


def _open_update_transaction(root: Path, data: Path) -> tuple[Path, Path, Path]:
    """Record an update transaction the way the helper leaves it for the new build."""

    from launchers.apply_update import journal_path
    from launchers.statusapp.healthy_start import resolve_bundle_paths

    paths = resolve_bundle_paths({"WG2_BUNDLE": "1"}, root, data)
    assert paths is not None
    bundle, resources, data_dir = paths
    journal_path(data_dir, resources).write_text(
        json.dumps({
            "schema": 1,
            "operation": "update",
            "state": "installed",
            "transaction": "t-activation",
            "resources": str(resources),
            "bundle": str(bundle),
            "layers": [],
        }),
        encoding="utf-8",
    )
    return paths


def test_a_bundle_waits_for_its_healthy_start_settlement(tmp_path: Path, monkeypatch) -> None:
    """The signal is the same settlement every launch mode uses.

    ``HealthyStartSettlement`` is what the window, ``--browser`` and
    ``--no-gui`` all call. Until it commits, the add-in is not touched; a
    launch that cannot confirm its build declines and still touches nothing.
    """

    from launchers.statusapp.healthy_start import HealthyStartSettlement

    monkeypatch.setenv("WG2_BUNDLE", "1")
    root, data = _bundle_layout(tmp_path)
    paths = _open_update_transaction(root, data)
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)

    ready, why = addin_update.startup_confirmed(root, data)
    assert not ready and "t-activation" in why
    first = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data, fusion_running=FUSION_CLOSED
    )
    assert first.verdict == "awaiting-startup"

    settlement = HealthyStartSettlement(lambda: paths)
    reports: list[str] = []
    assert not settlement.settle(ready=False, evidence="the interface never answered", report=reports.append)
    assert addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data, fusion_running=FUSION_CLOSED
    ).verdict == "awaiting-startup"
    assert calls == []
    assert addin_update.installed_commit(target) == OLD

    assert settlement.settle(ready=True, evidence="a healthy interface", report=reports.append)
    assert addin_update.startup_confirmed(root, data)[0]
    after = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data, fusion_running=FUSION_CLOSED
    )
    assert after.verdict == "updated"
    assert [call["pin"] for call in calls] == [PIN_A]


def test_a_no_gui_start_confirms_through_its_own_self_probe(tmp_path: Path, monkeypatch) -> None:
    """``--no-gui`` has no controller; its self-probe settles, and that is the signal."""

    from launch import serve

    monkeypatch.setenv("WG2_BUNDLE", "1")
    root, data = _bundle_layout(tmp_path)
    paths = _open_update_transaction(root, data)
    assert not addin_update.startup_confirmed(root, data)[0]

    monkeypatch.setattr(serve, "_probe_healthy_start", lambda *_args: None)
    check = serve._NoGuiHealthyStart(paths)
    check._server = SimpleNamespace(started=True)
    check._run()

    assert addin_update.startup_confirmed(root, data)[0]


def test_the_window_and_browser_modes_confirm_through_their_controller(
    tmp_path: Path, monkeypatch
) -> None:
    """``--browser`` settles on its controller's first ready snapshot, and the
    desktop window asks the same ``settle_update_transaction`` once its native
    loop runs. The signal flips there and nowhere earlier."""

    from launchers.statusapp.controller import (
        LampStatus,
        ServiceState,
        StatusController,
        StatusSnapshot,
    )

    monkeypatch.setenv("WG2_BUNDLE", "1")
    root, data = _bundle_layout(tmp_path)
    paths = _open_update_transaction(root, data)
    checkout = tmp_path / "checkout"
    (checkout / "frontend" / "dist").mkdir(parents=True)
    (checkout / "frontend" / "dist" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    controller = StatusController(
        repo_root=checkout, server_command=(sys.executable, "-c", "pass"), settle_on_ready=True
    )
    controller.bundle_paths = lambda: paths  # type: ignore[method-assign]

    def snapshot(frontend: ServiceState) -> StatusSnapshot:
        return StatusSnapshot(
            backend=LampStatus(ServiceState.OK, "ok"),
            frontend=LampStatus(frontend, "interface"),
            url="http://127.0.0.1:3199/",
            pid=123,
            exit_code=None,
        )

    assert not controller.settle_update_transaction(snapshot(ServiceState.ERROR), report=lambda _m: None)
    assert not addin_update.startup_confirmed(root, data)[0]
    assert controller.settle_update_transaction(snapshot(ServiceState.OK), report=lambda _m: None)
    assert addin_update.startup_confirmed(root, data)[0]


def test_a_source_checkout_has_no_update_to_confirm(tmp_path: Path) -> None:
    root = _populate(tmp_path / "wg")

    ready, why = addin_update.startup_confirmed(root, tmp_path / "data", environ={})

    assert ready
    assert "source checkout" in why


def test_the_startup_pass_waits_for_a_confirmed_start_then_runs_once(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_WGLINK_REFRESH", "1")
    monkeypatch.setattr(addin_update, "CONFIRMATION_POLL_FIRST", 0.001)
    answers = [
        (False, "update transaction t1 is open"),
        (False, "update transaction t1 is open"),
        (True, "no update transaction is open for this installation"),
    ]
    events: list[object] = []

    def confirmed(_root: Path, _data: Path) -> tuple[bool, str]:
        events.append("check")
        return answers.pop(0)

    monkeypatch.setattr(addin_update, "startup_confirmed", confirmed)
    monkeypatch.setattr(
        addin_update,
        "refresh_and_log",
        lambda data_dir=None: events.append(("pass", data_dir)) or ("current", ""),
    )

    async def drive() -> None:
        await addin_update.start_addin_refresh(data_dir=tmp_path)
        task = addin_update.addin_refresh.task
        assert task is not None
        await asyncio.wait({task})
        await addin_update.shutdown_addin_refresh()

    asyncio.run(drive())

    assert events == ["check", "check", "check", ("pass", tmp_path)]
    report = addin_update.last_refresh()
    assert report is not None and report["verdict"] == "awaiting-startup"
    assert "t1" in report["detail"]


def test_shutdown_does_not_wait_on_a_start_that_is_never_confirmed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_WGLINK_REFRESH", "1")
    passes: list[object] = []
    monkeypatch.setattr(addin_update, "startup_confirmed", lambda *_args: (False, "open"))
    monkeypatch.setattr(addin_update, "refresh_and_log", lambda *_args: passes.append(1))

    async def drive() -> float:
        await addin_update.start_addin_refresh(data_dir=tmp_path)
        await asyncio.sleep(0.05)
        began = time.monotonic()
        await addin_update.shutdown_addin_refresh()
        return time.monotonic() - began

    assert asyncio.run(drive()) < 0.5
    assert passes == []


# -- 2. Pending while Fusion is open ------------------------------------------


def test_fusion_open_stages_a_pending_activation_instead_of_replacing(
    tmp_path: Path, monkeypatch
) -> None:
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_OPEN, confirmed=CONFIRMED,
    )

    assert activation.verdict == "pending"
    assert "WGLink activation is pending until Fusion closes" in activation.detail
    assert calls == []
    assert addin_update.installed_commit(target) == OLD
    record_path = addin_update.pending_path(data, root)
    assert "updates" not in record_path.relative_to(data).parts
    record = _pending(data, root)
    assert record["requiredCommit"] == PIN_A
    assert record["build"] == BUILD_A
    assert record["target"] == str(target)
    assert record["ownership"] == "managed"
    assert record["installedCommit"] == OLD
    assert record["waveguideGeneratorRoot"] == str(root)
    # A source checkout fetches its pin at activation; it ships no package.
    assert record["package"] is None


def test_a_bundled_pending_activation_names_its_verified_shipped_package(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_BUNDLE", "1")
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    archive = _shipped(root, PIN_A)
    monkeypatch.setattr(addin_update, "_verified_shipped_package", lambda *_args: (archive, None))
    _fake_installer(monkeypatch, [])

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_OPEN, confirmed=CONFIRMED,
    )

    assert activation.verdict == "pending"
    assert _pending(data, root)["package"] == {
        "path": f"integrations/wglink/packages/wglink-0.3.3-{PIN_A}.zip",
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }


def test_the_status_poll_finishes_a_pending_activation_once_fusion_closes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_WGLINK_REFRESH", "1")
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)
    fusion = {"open": True}
    monkeypatch.setattr(
        addin_update, "fusion_process_state", lambda: "running" if fusion["open"] else "closed"
    )
    real = addin_update.activate_wglink
    monkeypatch.setattr(
        addin_update,
        "activate_wglink",
        functools.partial(real, root=root, addins_dir=addins, confirmed=CONFIRMED),
    )

    assert addin_update.refresh_and_log(data)[0] == "pending"

    async def drive() -> None:
        # Fusion still open: the poll does nothing at all.
        await addin_update.poll_activation(fusion_open=True, data_dir=data)
        assert addin_update._retry_task is None
        fusion["open"] = False
        await addin_update.poll_activation(fusion_open=False, data_dir=data)
        task = addin_update._retry_task
        assert task is not None
        await task

    asyncio.run(drive())

    report = addin_update.last_refresh()
    assert report is not None and report["verdict"] == "updated"
    assert "Fusion loads it the next time it starts" in report["detail"]
    assert addin_update.installed_commit(target) == PIN_A
    assert [call["pin"] for call in calls] == [PIN_A]
    assert calls[0]["retain_previous"] == addin_update.retained_previous_path(data, root)
    assert not addin_update.pending_path(data, root).exists()


def test_the_fusion_status_route_polls_activation_and_reports_it(
    tmp_path: Path, monkeypatch
) -> None:
    """The route the CAD Link UI already polls is the retry path and the status."""

    from server.app import create_app
    from server.cadlink.api import FusionStatusRequest, fusion_status

    monkeypatch.setattr("server.cadlink.api.fusion_process_running", lambda: False)
    polled: list[tuple[bool, Path | None]] = []

    async def poll(*, fusion_open: bool, data_dir: Path | None = None) -> None:
        polled.append((fusion_open, data_dir))

    monkeypatch.setattr("server.cadlink.api.poll_activation", poll)
    app = create_app(data_dir=tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app.state.cad_workspace.select(workspace)
    payload = FusionStatusRequest.model_validate({"design": {"formula": "OSSE", "L": 120, "a": 45}})

    monkeypatch.setattr(
        addin_update,
        "_report",
        addin_update.Activation("pending", "WGLink activation is pending until Fusion closes").report(),
    )
    response = asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))

    # Not an outdated add-in, and the pending activation is still reported.
    assert response["state"] == "closed"
    assert response["addinRefresh"]["verdict"] == "pending"
    assert polled == [(False, Path(app.state.data_dir))]

    # Activation turned off is said only with an outdated add-in.
    monkeypatch.setattr(
        addin_update, "_report", addin_update.Activation("disabled", "WG2_WGLINK_REFRESH=0").report()
    )
    assert "addinRefresh" not in asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))


def test_a_failed_activation_is_not_retried_by_every_poll(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WG2_WGLINK_REFRESH", "1")
    passes: list[object] = []
    monkeypatch.setattr(
        addin_update,
        "activate_wglink",
        lambda **_kwargs: passes.append(1) or addin_update.Activation("failed", "lock held"),
    )
    addin_update.refresh_and_log(tmp_path)
    assert passes == [1]

    async def drive() -> None:
        await addin_update.poll_activation(fusion_open=False, data_dir=tmp_path)

    asyncio.run(drive())
    assert addin_update._retry_task is None


# -- 3 and 4. Re-checked under the lock; stale work superseded -----------------


def test_stale_pending_work_from_another_build_never_installs(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """The updater review §2.5, step by step."""

    root = _populate(tmp_path / "wg", BUILD_A, PIN_A)
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=PIN_A, root=root)
    data = tmp_path / "data"
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)

    # WG build B prepares its matching WGLink package...
    _build(root, BUILD_B, PIN_B)
    staged = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_OPEN, confirmed=CONFIRMED,
    )
    # ...and activation waits because Fusion is open.
    assert staged.verdict == "pending"
    assert _pending(data, root)["build"] == BUILD_B
    assert _pending(data, root)["requiredCommit"] == PIN_B

    # WG returns to build A (a rollback or a channel switch): a new process.
    _build(root, BUILD_A, PIN_A)
    monkeypatch.setattr(addin_update, "_running_builds", {})

    # Fusion closes.
    with caplog.at_level(logging.INFO, logger="wg.cadlink.addin"):
        activation = addin_update.activate_wglink(
            root=root, addins_dir=addins, data_dir=data,
            fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
        )

    # B's package must NOT install.
    assert calls == []
    assert addin_update.installed_commit(target) == PIN_A
    assert activation.verdict == "current"
    assert activation.superseded is not None
    assert "0.3.4" in activation.superseded and "0.3.3" in activation.superseded
    assert not addin_update.pending_path(data, root).exists()
    assert "WGLink activation superseded" in caplog.text


def test_a_build_that_returned_installs_only_its_own_pin(tmp_path: Path, monkeypatch) -> None:
    root = _populate(tmp_path / "wg", BUILD_A, PIN_A)
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)
    _build(root, BUILD_B, PIN_B)
    assert addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_OPEN, confirmed=CONFIRMED,
    ).verdict == "pending"

    _build(root, BUILD_A, PIN_A)
    monkeypatch.setattr(addin_update, "_running_builds", {})
    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
    )

    assert activation.verdict == "updated"
    assert activation.superseded is not None
    assert [call["pin"] for call in calls] == [PIN_A]
    assert addin_update.installed_commit(target) == PIN_A


def test_a_newer_build_supersedes_pending_work_while_fusion_stays_open(
    tmp_path: Path, monkeypatch
) -> None:
    root = _populate(tmp_path / "wg", BUILD_B, PIN_B)
    addins = tmp_path / "AddIns"
    _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    _fake_installer(monkeypatch, [])
    addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_OPEN, confirmed=CONFIRMED,
    )

    _build(root, BUILD_C, PIN_C)
    monkeypatch.setattr(addin_update, "_running_builds", {})
    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_OPEN, confirmed=CONFIRMED,
    )

    assert activation.verdict == "pending"
    assert activation.superseded is not None and "0.3.4" in activation.superseded
    assert _pending(data, root)["build"] == BUILD_C
    assert _pending(data, root)["requiredCommit"] == PIN_C


def test_the_process_must_still_be_the_installed_build(tmp_path: Path, monkeypatch) -> None:
    root = _populate(tmp_path / "wg", BUILD_A, PIN_A)
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)
    assert addin_update.running_build(root) == BUILD_A

    # The app on disk changed under this process.
    _build(root, BUILD_B, PIN_B)
    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=tmp_path / "data",
        fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
    )

    assert activation.verdict == "superseded"
    assert "its next start decides WGLink" in activation.detail
    assert calls == []
    assert addin_update.installed_commit(target) == OLD


def test_the_decision_is_made_under_the_install_lock(tmp_path: Path, monkeypatch) -> None:
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    _installed(addins, commit=OLD, root=root)
    held = {"now": False}
    events: list[tuple[str, bool]] = []
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls, held=held)
    # This process's own build, captured as the startup pass captures it.
    addin_update.running_build(root)
    real_pin, real_build = addin_update.pinned_commit, addin_update.build_identity
    monkeypatch.setattr(
        addin_update, "pinned_commit", lambda r: events.append(("pin", held["now"])) or real_pin(r)
    )
    monkeypatch.setattr(
        addin_update, "build_identity", lambda r: events.append(("build", held["now"])) or real_build(r)
    )

    def running() -> bool:
        events.append(("fusion", held["now"]))
        return False

    def confirmed() -> tuple[bool, str]:
        events.append(("confirmed", held["now"]))
        return True, "confirmed"

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=tmp_path / "data",
        fusion_running=running, confirmed=confirmed,
    )

    assert activation.verdict == "updated"
    # Fusion, the pin and the build are only ever read under the lock, and the
    # start is asked again there.
    assert ("fusion", True) in events and ("fusion", False) not in events
    assert ("pin", True) in events and ("pin", False) not in events
    assert ("build", True) in events and ("build", False) not in events
    assert ("confirmed", True) in events
    assert calls and calls[0]["locked"] is True
    assert (addins.resolve() / ".WGLink-install.lock").exists()


def test_fusion_opening_before_the_lock_keeps_the_activation_pending(
    tmp_path: Path, monkeypatch
) -> None:
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    fusion = {"open": False}
    held = {"now": False}
    calls: list[dict[str, object]] = []
    # Fusion starts in the moment between the poll and the lock.
    _fake_installer(monkeypatch, calls, held=held, on_lock=lambda: fusion.update(open=True))

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=tmp_path / "data",
        fusion_running=lambda: fusion["open"], confirmed=CONFIRMED,
    )

    assert activation.verdict == "pending"
    assert calls == []
    assert addin_update.installed_commit(target) == OLD


def test_an_ownership_move_discards_pending_work(tmp_path: Path, monkeypatch) -> None:
    """After ownership moves to another installation, old pending work never overrides it."""

    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    calls: list[dict[str, object]] = []
    held = {"now": False}
    _fake_installer(monkeypatch, calls)
    assert addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_OPEN, confirmed=CONFIRMED,
    ).verdict == "pending"

    # Another installation adopts the add-in just before this one takes the lock.
    other = (tmp_path / "another-wg").resolve()
    (other / "scripts").mkdir(parents=True)
    (other / "scripts" / "install_wglink.py").write_text("# installer\n", encoding="utf-8")
    _fake_installer(
        monkeypatch, calls, held=held,
        on_lock=lambda: _marker(target, root=other, commit=PIN_B),
    )
    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
    )

    assert activation.verdict == "external"
    assert activation.superseded is not None and "another Waveguide Generator" in activation.superseded
    assert calls == []
    assert addin_update.installed_commit(target) == PIN_B
    assert not addin_update.pending_path(data, root).exists()


def test_a_developer_sync_started_while_pending_is_left_alone(tmp_path: Path, monkeypatch) -> None:
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)
    addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_OPEN, confirmed=CONFIRMED,
    )
    (target / "wglink_dev.json").write_text('{"sourceCommit": "local"}', encoding="utf-8")

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
    )

    assert activation.verdict == "developer"
    assert activation.superseded is not None and "developer sync" in activation.superseded
    assert calls == []


def test_an_unreadable_pending_record_is_discarded_not_trusted(tmp_path: Path, monkeypatch) -> None:
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    _installed(addins, commit=PIN_A, root=root)
    data = tmp_path / "data"
    record = addin_update.pending_path(data, root)
    record.parent.mkdir(parents=True)
    record.write_text("{ truncated", encoding="utf-8")
    _fake_installer(monkeypatch, [])

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
    )

    assert activation.verdict == "current"
    assert activation.superseded is not None and "could not be read" in activation.superseded
    assert not record.exists()


# -- 6. A first install follows the same rules (D5) ----------------------------


def test_a_first_install_waits_for_a_confirmed_start_and_a_closed_fusion(
    tmp_path: Path, monkeypatch
) -> None:
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "Autodesk Fusion" / "API" / "AddIns"
    addins.parent.mkdir(parents=True)
    data = tmp_path / "data"
    archive = _shipped(root, PIN_A)
    monkeypatch.setattr(addin_update, "_verified_shipped_package", lambda *_args: (archive, None))
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls, addins=addins)

    waiting = addin_update.activate_wglink(
        root=root, data_dir=data, fusion_running=FUSION_CLOSED,
        confirmed=lambda: (False, "update transaction t1 is open"),
    )
    assert waiting.verdict == "awaiting-startup"
    assert not addins.exists()

    pending = addin_update.activate_wglink(
        root=root, data_dir=data, fusion_running=FUSION_OPEN, confirmed=CONFIRMED,
    )
    assert pending.verdict == "pending"
    assert "install WGLink" in pending.detail
    assert _pending(data, root)["ownership"] == "absent"
    assert _pending(data, root)["package"]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    # Nothing was created where Fusion looks: no folder, no lock file.
    assert not addins.exists()
    assert calls == []

    installed = addin_update.activate_wglink(
        root=root, data_dir=data, fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
    )
    assert installed.verdict == "installed"
    assert "Fusion loads it the next time it starts" in installed.detail
    assert calls and calls[0]["archive_path"] == archive
    assert not addin_update.pending_path(data, root).exists()


def test_a_package_that_changed_after_staging_is_refused(tmp_path: Path, monkeypatch) -> None:
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    data = tmp_path / "data"
    archive = _shipped(root, PIN_A)
    monkeypatch.setattr(addin_update, "_verified_shipped_package", lambda *_args: (archive, None))
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)
    assert addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_OPEN, confirmed=CONFIRMED,
    ).verdict == "pending"
    archive.write_bytes(b"a different package")

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
    )

    assert activation.verdict == "failed"
    assert "changed after it was verified" in activation.detail
    assert calls == []


# -- 5. The displaced managed add-in is kept ----------------------------------


@pytest.fixture
def short_tmp_path() -> Iterator[Path]:
    """Short enough to install under on Windows (see test_wglink_package.py)."""

    root = Path(tempfile.mkdtemp(prefix="wg2-"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real_package(base: Path, root: Path, pin: str) -> Path:
    """Build a real, verifiable WGLink package for ``pin`` and pin ``root`` to it."""

    source = base / "hornlab-fusion-addin"
    addin = source / "fusion-addins" / "WGLink"
    (addin / "resources" / "insert").mkdir(parents=True)
    (source / "scripts").mkdir()
    (addin / "WGLink.py").write_text("def run(_context): pass\n", encoding="utf-8")
    (addin / "WGLink.manifest").write_text(json.dumps({"version": "0.1.1"}), encoding="utf-8")
    (addin / "wglink_core.py").write_text(
        'PACKAGED_RUNTIME_FILE = "wglink_runtime.json"\n', encoding="utf-8"
    )
    (addin / "resources" / "insert" / "16x16.png").write_bytes(b"png")
    (source / "scripts" / "wglink_resample.py").write_text("# exact resampler\n", encoding="utf-8")
    (source / "LICENSE").write_text("AGPL test fixture\n", encoding="utf-8")
    (root / "integrations" / "wglink").mkdir(parents=True, exist_ok=True)
    (root / "shared").mkdir(parents=True, exist_ok=True)
    (root / "integrations" / "wglink" / "source.json").write_text(
        json.dumps(_spec(pin)), encoding="utf-8"
    )
    (root / "shared" / "version.json").write_text(json.dumps({"version": "9.8.7"}), encoding="utf-8")
    archive = base / "wglink.zip"
    builder = _load(REAL_ROOT / "scripts" / "build_wglink_package.py", "wglink_builder_activation")
    builder.build_package(source, archive, spec=_spec(pin), version="9.8.7", observed_commit=pin)
    return archive


def test_a_replacement_keeps_the_managed_copy_it_displaced(short_tmp_path: Path) -> None:
    installer = _load(REAL_ROOT / "scripts" / "install_wglink.py", "wglink_installer_activation")
    root = short_tmp_path / "wg"
    addins = short_tmp_path / "AddIns"
    first = _real_package(short_tmp_path / "a", root, PIN_A)
    status, target = installer.install(
        root=root, addins_dir=addins, archive_path=first, python=Path(sys.executable)
    )
    assert status == "installed"

    second = _real_package(short_tmp_path / "b", root, PIN_B)
    kept = short_tmp_path / "data" / "previous" / "WGLink"
    status, target = installer.install(
        root=root, addins_dir=addins, archive_path=second,
        python=Path(sys.executable), retain_previous=kept,
    )

    assert status == "installed"
    assert addin_update.installed_commit(target) == PIN_B
    marker = json.loads((kept / "wglink_install.json").read_text(encoding="utf-8"))
    assert marker["sourceCommit"] == PIN_A
    assert marker["managedBy"] == "waveguide-generator"
    assert Path(marker["waveguideGeneratorRoot"]) == root.resolve()
    assert (kept / "WGLink.py").is_file()
    # The replacement's own journal finished exactly as before.
    assert not (addins / ".WGLink-install-transaction.json").exists()
    assert not [path for path in addins.iterdir() if path.name.startswith(".WGLink-install-") and path.is_dir()]


def test_a_hand_copied_add_in_is_not_kept_as_a_managed_rollback(short_tmp_path: Path) -> None:
    installer = _load(REAL_ROOT / "scripts" / "install_wglink.py", "wglink_installer_activation")
    root = short_tmp_path / "wg"
    addins = short_tmp_path / "AddIns"
    archive = _real_package(short_tmp_path / "a", root, PIN_A)
    hand = addins / "WGLink"
    hand.mkdir(parents=True)
    (hand / "WGLink.py").write_text("# copied by hand\n", encoding="utf-8")
    kept = short_tmp_path / "data" / "previous" / "WGLink"

    status, _target = installer.install(
        root=root, addins_dir=addins, archive_path=archive,
        python=Path(sys.executable), replace_external=True, retain_previous=kept,
    )

    assert status == "installed"
    assert not kept.exists()


# -- 8. Fusion's own registry: reported, never edited --------------------------


def _fusion_target(tmp_path: Path) -> Path:
    target = tmp_path / "Autodesk Fusion 360" / "API" / "AddIns" / "WGLink"
    target.mkdir(parents=True)
    return target.resolve()


def _registry(target: Path, entries: list[dict[str, object]]) -> Path:
    registry = target.parent.parent.parent / "user-1" / "JSLoadedScriptsinfo"
    registry.parent.mkdir(exist_ok=True)
    registry.write_text(json.dumps({"loadedScripts": entries}), encoding="utf-8")
    return registry


def _entry(path: Path, *, run: bool = True, removed: bool = False, name: str = "WGLink") -> dict[str, object]:
    return {
        "name": name,
        "path": str(path),
        "location": 3,
        "isRemoved": removed,
        "isFavorite": False,
        "runOnStartup": run,
    }


@pytest.mark.parametrize(
    ("entries", "state"),
    [
        (lambda t, o: [], "unregistered"),
        (lambda t, o: [_entry(t / "WGLink.py")], "registered"),
        (lambda t, o: [_entry(t / "WGLink.py", run=False)], "manual"),
        (lambda t, o: [_entry(t / "WGLink.py"), _entry(o / "WGLink.py")], "duplicate"),
        (lambda t, o: [_entry(o / "WGLink.py")], "elsewhere"),
        (lambda t, o: [_entry(t / "WGLink.py", removed=True)], "unregistered"),
        (lambda t, o: [_entry(o.parent / "Other" / "Other.py", name="Other")], "unregistered"),
    ],
)
def test_fusions_registry_is_read_for_what_decides_loading(tmp_path: Path, entries, state) -> None:
    target = _fusion_target(tmp_path)
    elsewhere = tmp_path / "checkout" / "fusion-addins" / "WGLink"
    _registry(target, entries(target, elsewhere))

    finding = addin_update.fusion_registration(target)

    assert finding is not None and finding["state"] == state


def test_no_registry_is_no_claim(tmp_path: Path) -> None:
    assert addin_update.fusion_registration(_fusion_target(tmp_path)) is None


def test_activation_reports_the_registry_and_never_edits_it(tmp_path: Path, monkeypatch) -> None:
    root = _populate(tmp_path / "wg")
    target = _fusion_target(tmp_path)
    target.rmdir()
    target = _installed(target.parent, commit=OLD, root=root)
    registry = _registry(target, [_entry(target / "WGLink.py"), _entry(tmp_path / "x" / "WGLink" / "WGLink.py")])
    before = (registry.read_bytes(), registry.stat().st_mtime_ns)
    _fake_installer(monkeypatch, [])

    activation = addin_update.activate_wglink(
        root=root, addins_dir=target.parent, data_dir=tmp_path / "data",
        fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
    )

    assert activation.verdict == "updated"
    assert activation.registration is not None and activation.registration["state"] == "duplicate"
    assert (registry.read_bytes(), registry.stat().st_mtime_ns) == before


# -- Review findings: the lock, Fusion's state, the retry, the swap ------------


def test_a_build_swapped_while_the_pass_waits_for_the_lock_is_superseded(
    tmp_path: Path, monkeypatch
) -> None:
    """Probe P1: the app layer becomes build B while this pass (build A) waits."""

    root = _populate(tmp_path / "wg", BUILD_A, PIN_A)
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    calls: list[dict[str, object]] = []
    held = {"now": False}
    assert addin_update.running_build(root) == BUILD_A
    _fake_installer(monkeypatch, calls, held=held, on_lock=lambda: _build(root, BUILD_B, PIN_B))

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=tmp_path / "data",
        fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
    )

    assert activation.verdict == "superseded"
    assert calls == []
    assert addin_update.installed_commit(target) == OLD


def test_a_pin_that_moves_before_the_installer_runs_is_superseded(
    tmp_path: Path, monkeypatch
) -> None:
    root = _populate(tmp_path / "wg", BUILD_A, PIN_A)
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    asked: list[int] = []
    _fake_installer(monkeypatch, [], before_swap=lambda: asked.append(1))
    real_plan = addin_update._plan

    def plan_then_move(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        _build(root, BUILD_A, PIN_B)
        return plan

    monkeypatch.setattr(addin_update, "_plan", plan_then_move)

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=tmp_path / "data",
        fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
    )

    assert activation.verdict == "superseded"
    assert "pin changed" in activation.detail
    assert asked == []  # the installer never ran
    assert addin_update.installed_commit(target) == OLD


def test_a_pin_that_moves_at_the_swap_is_superseded(tmp_path: Path, monkeypatch) -> None:
    root = _populate(tmp_path / "wg", BUILD_A, PIN_A)
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls, before_swap=lambda: _build(root, BUILD_A, PIN_B))

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=tmp_path / "data",
        fusion_running=FUSION_CLOSED, confirmed=CONFIRMED,
    )

    assert activation.verdict == "superseded"
    assert calls == []
    assert addin_update.installed_commit(target) == OLD


def test_fusion_opening_at_the_swap_leaves_the_activation_pending(
    tmp_path: Path, monkeypatch
) -> None:
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)
    # Closed when the lock is taken; open when the installer asks, just before it moves anything.
    answers = iter(["closed", "running"])

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data,
        fusion_running=lambda: next(answers), confirmed=CONFIRMED,
    )

    assert activation.verdict == "pending"
    assert calls == []
    assert addin_update.installed_commit(target) == OLD
    assert _pending(data, root)["requiredCommit"] == PIN_A


def test_a_fusion_check_that_cannot_tell_keeps_the_activation_pending(
    tmp_path: Path, monkeypatch
) -> None:
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)
    monkeypatch.setattr(addin_update, "fusion_process_state", lambda: "unknown")

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data, confirmed=CONFIRMED,
    )

    assert activation.verdict == "pending"
    assert "could not tell whether Fusion is running" in activation.detail
    assert calls == []
    assert addin_update.installed_commit(target) == OLD


def test_a_first_install_touches_nothing_under_addins_while_fusion_may_be_open(
    tmp_path: Path, monkeypatch
) -> None:
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "Autodesk Fusion" / "API" / "AddIns"
    addins.parent.mkdir(parents=True)
    archive = _shipped(root, PIN_A)
    monkeypatch.setattr(addin_update, "_verified_shipped_package", lambda *_args: (archive, None))
    _fake_installer(monkeypatch, [], addins=addins)

    for state in ("running", "unknown"):
        activation = addin_update.activate_wglink(
            root=root, data_dir=tmp_path / "data",
            fusion_running=lambda state=state: state, confirmed=CONFIRMED,
        )
        assert activation.verdict == "pending"
        assert not addins.exists()


def test_a_fresh_wglink_heartbeat_counts_as_fusion_open(tmp_path: Path, monkeypatch) -> None:
    """Even when the process check says closed: a heartbeat is Fusion running WGLink."""

    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    heartbeat = data / "ipc" / "wglink" / ".fusion-status.json"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text(
        json.dumps({
            "schemaVersion": 1,
            "cadApplication": "fusion360",
            "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }),
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)

    activation = addin_update.activate_wglink(
        root=root, addins_dir=addins, data_dir=data, confirmed=CONFIRMED,
    )

    assert activation.verdict == "pending"
    assert calls == []
    assert addin_update.installed_commit(target) == OLD


def test_the_fusion_process_check_says_unknown_when_it_cannot_tell(monkeypatch) -> None:
    """A missing tool, a timeout and a check that cannot run are unknown, not closed.

    ``fusion_process_running`` keeps reading all three as "not running", as
    its other callers expect.
    """

    from server.cadlink import fusion_status

    monkeypatch.setattr(fusion_status.shutil, "which", lambda _name: None)
    for system in ("Darwin", "Windows"):
        assert fusion_status.fusion_process_state(system=system) == "unknown"
        assert fusion_status.fusion_process_running(system=system) is False

    monkeypatch.setattr(fusion_status.shutil, "which", lambda name: f"/usr/bin/{name}")

    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="check", timeout=2)

    def cannot_run(*_args, **_kwargs):
        raise OSError("permission denied")

    for broken in (timed_out, cannot_run):
        monkeypatch.setattr(fusion_status.subprocess, "run", broken)
        for system in ("Darwin", "Windows"):
            assert fusion_status.fusion_process_state(system=system) == "unknown"
            assert fusion_status.fusion_process_running(system=system) is False

    # Fusion does not run on Linux at all.
    assert fusion_status.fusion_process_state(system="Linux") == "closed"


@pytest.mark.parametrize(
    ("system", "returncode", "stdout", "state"),
    [
        ("Darwin", 0, b"1234\n", "running"),
        ("Darwin", 1, b"", "closed"),
        ("Darwin", 2, b"", "unknown"),
        ("Windows", 0, b"Fusion360.exe   4242 Console   1  900,000 K\r\n", "running"),
        ("Windows", 0, b"INFO: No tasks are running which match the specified criteria.\r\n", "closed"),
        ("Windows", 1, b"", "unknown"),
    ],
)
def test_the_fusion_process_check_reads_its_tool(monkeypatch, system, returncode, stdout, state) -> None:
    from server.cadlink import fusion_status

    monkeypatch.setattr(fusion_status.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        fusion_status.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, returncode, stdout, b""),
    )

    assert fusion_status.fusion_process_state(system=system) == state


def test_a_pending_activation_completes_with_no_status_poll_at_all(
    tmp_path: Path, monkeypatch
) -> None:
    """No CAD folder, a hidden page, or --no-gui: the startup pass still finishes it."""

    monkeypatch.setenv("WG2_WGLINK_REFRESH", "1")
    monkeypatch.setattr(addin_update, "CONFIRMATION_POLL_FIRST", 0.001)
    monkeypatch.setattr(addin_update, "ACTIVATION_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(addin_update, "startup_confirmed", lambda *_args: (True, "confirmed"))
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)
    asked = {"n": 0}

    def fusion() -> str:
        # Open for the first pass, closed from then on.
        asked["n"] += 1
        return "running" if asked["n"] == 1 else "closed"

    monkeypatch.setattr(addin_update, "fusion_process_state", fusion)
    real = addin_update.activate_wglink
    monkeypatch.setattr(
        addin_update,
        "activate_wglink",
        functools.partial(real, root=root, addins_dir=addins, confirmed=CONFIRMED),
    )
    polls: list[object] = []
    monkeypatch.setattr(addin_update, "poll_activation", lambda **kwargs: polls.append(kwargs))

    async def drive() -> None:
        await addin_update.start_addin_refresh(data_dir=data)
        task = addin_update.addin_refresh.task
        assert task is not None
        done, _pending_tasks = await asyncio.wait({task}, timeout=10)
        assert done
        await addin_update.shutdown_addin_refresh()

    asyncio.run(drive())

    report = addin_update.last_refresh()
    assert report is not None and report["verdict"] == "updated"
    assert addin_update.installed_commit(target) == PIN_A
    assert [call["pin"] for call in calls] == [PIN_A]
    assert polls == [] and addin_update._retry_task is None


def test_the_startup_retry_ends_at_shutdown_while_still_pending(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_WGLINK_REFRESH", "1")
    monkeypatch.setattr(addin_update, "CONFIRMATION_POLL_FIRST", 0.001)
    monkeypatch.setattr(addin_update, "startup_confirmed", lambda *_args: (True, "confirmed"))
    monkeypatch.setattr(addin_update, "fusion_process_state", lambda: "running")
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    _installed(addins, commit=OLD, root=root)
    _fake_installer(monkeypatch, [])
    real = addin_update.activate_wglink
    monkeypatch.setattr(
        addin_update,
        "activate_wglink",
        functools.partial(real, root=root, addins_dir=addins, confirmed=CONFIRMED),
    )

    async def drive() -> float:
        await addin_update.start_addin_refresh(data_dir=tmp_path / "data")
        deadline = time.monotonic() + 5
        while (addin_update.last_refresh() or {}).get("verdict") != "pending":
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        began = time.monotonic()
        await addin_update.shutdown_addin_refresh()
        return time.monotonic() - began

    assert asyncio.run(drive()) < 0.5
    assert addin_update.addin_refresh.task is None


def test_the_installer_moves_nothing_when_told_not_to_proceed(short_tmp_path: Path) -> None:
    installer = _load(REAL_ROOT / "scripts" / "install_wglink.py", "wglink_installer_activation")
    root = short_tmp_path / "wg"
    addins = short_tmp_path / "AddIns"
    first = _real_package(short_tmp_path / "a", root, PIN_A)
    status, target = installer.install(
        root=root, addins_dir=addins, archive_path=first, python=Path(sys.executable)
    )
    assert status == "installed"
    second = _real_package(short_tmp_path / "b", root, PIN_B)
    asked: list[int] = []

    status, target = installer.install(
        root=root, addins_dir=addins, archive_path=second, python=Path(sys.executable),
        should_proceed=lambda: asked.append(1) or False,
    )

    assert (status, asked) == ("deferred", [1])
    assert addin_update.installed_commit(target) == PIN_A
    assert not (addins / ".WGLink-install-transaction.json").exists()
    assert not [path for path in addins.iterdir() if path.name.startswith(".WGLink-install-") and path.is_dir()]

    # A first install told not to proceed leaves no add-in at all.
    fresh = short_tmp_path / "Fresh"
    status, target = installer.install(
        root=root, addins_dir=fresh, archive_path=second, python=Path(sys.executable),
        should_proceed=lambda: False,
    )
    assert status == "deferred"
    assert not target.exists()
    assert not (fresh / ".WGLink-install-transaction.json").exists()


def test_a_poll_that_activates_is_not_undone_by_the_startup_retry(
    tmp_path: Path, monkeypatch
) -> None:
    """Probe P5: the poll's pass installs while the startup retry sleeps.

    When the retry wakes, the work is no longer pending. It must not run
    another pass, which would find the pin current and replace "updated" with
    "current" -- and with it the status that Fusion loads the new copy next.
    """

    monkeypatch.setenv("WG2_WGLINK_REFRESH", "1")
    monkeypatch.setattr(addin_update, "CONFIRMATION_POLL_FIRST", 0.001)
    monkeypatch.setattr(addin_update, "ACTIVATION_RETRY_SECONDS", 0.3)
    monkeypatch.setattr(addin_update, "startup_confirmed", lambda *_args: (True, "confirmed"))
    root = _populate(tmp_path / "wg")
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit=OLD, root=root)
    data = tmp_path / "data"
    calls: list[dict[str, object]] = []
    _fake_installer(monkeypatch, calls)
    fusion = {"open": True}
    monkeypatch.setattr(
        addin_update, "fusion_process_state", lambda: "running" if fusion["open"] else "closed"
    )
    passes: list[str] = []
    real = addin_update.activate_wglink

    def counted(**kwargs):
        activation = real(root=root, addins_dir=addins, confirmed=CONFIRMED, **kwargs)
        passes.append(activation.verdict)
        return activation

    monkeypatch.setattr(addin_update, "activate_wglink", counted)

    async def drive() -> None:
        await addin_update.start_addin_refresh(data_dir=data)
        deadline = time.monotonic() + 5
        # "pending" is recorded while the startup pass still holds the pass
        # lock, and a poll that finds the lock held defers to the next poll.
        # Wait for the lock too, or this poll may start no pass at all.
        while (
            (addin_update.last_refresh() or {}).get("verdict") != "pending"
            or addin_update._pass_lock.locked()
        ):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        # Fusion closes with the CAD Link UI open: the poll's pass installs.
        fusion["open"] = False
        await addin_update.poll_activation(fusion_open=False, data_dir=data)
        poll_pass = addin_update._retry_task
        assert poll_pass is not None
        await poll_pass
        assert (addin_update.last_refresh() or {}).get("verdict") == "updated"
        # Well past the moment the startup retry wakes.
        await asyncio.sleep(0.8)
        startup = addin_update.addin_refresh.task
        assert startup is not None and startup.done()
        await addin_update.shutdown_addin_refresh()

    asyncio.run(drive())

    assert passes == ["pending", "updated"]
    report = addin_update.last_refresh()
    assert report is not None and report["verdict"] == "updated"
    assert "Fusion loads it the next time it starts" in report["detail"]
    assert [call["pin"] for call in calls] == [PIN_A]
    assert addin_update.installed_commit(target) == PIN_A


def test_a_retry_runs_only_while_its_verdict_still_holds(tmp_path: Path, monkeypatch) -> None:
    """The check is made under the pass lock, so no pass slips in between."""

    monkeypatch.setenv("WG2_WGLINK_REFRESH", "1")
    passes: list[int] = []
    monkeypatch.setattr(
        addin_update,
        "activate_wglink",
        lambda **_kwargs: passes.append(1) or addin_update.Activation("current", "at the pin"),
    )
    monkeypatch.setattr(
        addin_update, "_report", addin_update.Activation("updated", "done").report()
    )

    assert addin_update._retry_pass(frozenset({"pending"}), tmp_path) is None
    assert passes == []
    assert (addin_update.last_refresh() or {}).get("verdict") == "updated"

    monkeypatch.setattr(
        addin_update, "_report", addin_update.Activation("pending", "waiting").report()
    )
    assert addin_update._retry_pass(frozenset({"pending"}), tmp_path) == ("current", "at the pin")
    assert passes == [1]
