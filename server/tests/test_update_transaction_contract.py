"""Tests for ``docs/reference/UPDATE-TRANSACTION-CONTRACT.md``.

Two kinds of test live here.

**Strict expected failures** (contract §2 and §4). Each one encodes a
requirement the updater does not meet yet, and is marked
``xfail(strict=True, raises=AssertionError)``:

* While the behaviour is missing, the contract assertion fails and pytest
  reports an expected failure, so the suite stays green and nothing broken is
  committed as passing.
* When a change implements the behaviour but leaves the marker in place, the
  test passes unexpectedly and ``strict`` turns that into a failure. Remove the
  marker in the change that implements the behaviour.
* Any exception other than ``AssertionError`` is an ordinary failure. Set-up
  checks therefore use ``pytest.fail``, never ``assert``: a fixture that broke
  must not pass itself off as the behaviour that is missing.

**Regression tests** (contract §3). An old release's launcher runs the
candidate's helper with the old command line, and that already works. These
keep it working.
"""

from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import textwrap
import time
from typing import Any, NamedTuple
import zipfile

import pytest

from launch import serve
from launchers import apply_update as apply_update_module
from launchers import desktop
from launchers import update_lock
from launchers.apply_update import (
    begin_update_transaction,
    commit_transaction,
    installation_key,
    plan_layer_swap,
    read_journal,
    set_journal_state,
    swap_staged_layers,
)
from launchers.statusapp import __main__ as statusapp_main
from launchers.statusapp.controller import (
    LampStatus,
    ServiceState,
    StatusController,
    StatusSnapshot,
)
from server.app import create_app
from server.platform.paths import ensure_data_layout


REPOSITORY_ROOT = Path(apply_update_module.__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _updater_state_stays_in_this_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No updater dialog, and no claim or grant outside this test's directory.

    The same two isolations ``test_apply_update.py`` applies, for the same
    reasons: the updater's failure channel is a modal dialog on a desktop, and
    ``update_lock`` keeps its claim and relaunch grants under the host's own
    cache directory.
    """

    monkeypatch.setattr(
        apply_update_module,
        "_show_update_failure_dialog",
        lambda message, platform_name: None,
    )
    monkeypatch.setattr(update_lock, "cache_root", lambda **_kwargs: tmp_path / "cache")


# ---------------------------------------------------------------------------
# An installed copy with one update staged, and that update swapped in
# ---------------------------------------------------------------------------


class Installation(NamedTuple):
    bundle: Path
    resources: Path
    data_dir: Path
    staged_app: Path
    staged_runtime: Path


def _write_layer(layer: Path, generation: str) -> None:
    layer.mkdir(parents=True)
    (layer / "marker.txt").write_text(generation, encoding="utf-8")
    manifest = "APP-MANIFEST.json" if layer.name == "app" else "RUNTIME-MANIFEST.json"
    (layer / manifest).write_text(
        json.dumps({"schemaVersion": 1, "version": "9.9.9", "runtimeId": generation}),
        encoding="utf-8",
    )


def _installation(tmp_path: Path, platform_name: str = "linux") -> Installation:
    """One installed copy, with an update staged where every release stages it.

    Staging is ``<data>/updates/<version>/staged/<layer>`` (``bundle.py:866``).
    The bundle is laid out for ``platform_name`` the way ``resources_directory``
    expects it.
    """

    root = tmp_path.resolve()
    if platform_name == "darwin":
        bundle = root / "Waveguide Generator.app"
        resources = bundle / "Contents" / "Resources"
    else:
        bundle = root / "Waveguide Generator"
        resources = bundle
    data_dir = root / "data"
    staged = data_dir / "updates" / "9.9.9" / "staged"
    for layer, generation in (
        (resources / "app", "old0"),
        (resources / "runtime", "old0"),
        (staged / "app", "new1"),
        (staged / "runtime", "new1"),
    ):
        _write_layer(layer, generation)
    (data_dir / "logs").mkdir(parents=True)
    return Installation(bundle, resources, data_dir, staged / "app", staged / "runtime")


def _decided_update(installation: Installation, platform_name: str = "linux") -> str:
    """Swap the staged layers in and record the transaction as installed.

    This is the state a relaunched new version starts in: the renames are done
    and decided, and only a healthy start may close the transaction and reclaim
    ``.previous``. Returns the transaction id.
    """

    planned = plan_layer_swap(
        installation.resources, installation.staged_app, installation.staged_runtime
    )
    journal = begin_update_transaction(
        data_dir=installation.data_dir,
        bundle=installation.bundle,
        resources=installation.resources,
        layers=planned,
        platform_name=platform_name,
    )
    swap_staged_layers(
        installation.resources,
        installation.staged_app,
        installation.staged_runtime,
        journal_dir=installation.data_dir,
    )
    set_journal_state(installation.data_dir, installation.resources, "installed")
    recorded = read_journal(installation.data_dir, installation.resources) or {}
    if (
        recorded.get("state") != "installed"
        or not (installation.resources / "app.previous").is_dir()
    ):
        pytest.fail(f"set-up: expected a decided update with rollback material: {recorded!r}")
    return str(journal["transaction"])


def _with_interface(app_layer: Path) -> None:
    """The built interface every launch mode checks for before it starts."""

    dist = app_layer / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")


def _update_log(installation: Installation) -> str:
    log = installation.data_dir / "logs" / "update.log"
    return log.read_text(encoding="utf-8") if log.is_file() else ""


def _healthy_snapshot() -> StatusSnapshot:
    return StatusSnapshot(
        backend=LampStatus(ServiceState.OK, "Healthy"),
        frontend=LampStatus(ServiceState.OK, "Serving the interface"),
        url="http://127.0.0.1:3199/",
        pid=123,
        exit_code=None,
    )


# ---------------------------------------------------------------------------
# Contract §2: records and cleanup
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="contract §2.2: commit_transaction deletes the journal and records no outcome",
)
def test_a_committed_update_leaves_a_completion_record_when_its_journal_goes(
    tmp_path: Path,
) -> None:
    """The journal is the only record of an outcome, and a commit deletes it.

    ``commit_transaction`` removes the journal as soon as its state is terminal
    (``apply_update.py:2049``). Nothing else holds the outcome, so WG can
    neither explain it later nor suppress a build that failed.
    """

    installation = _installation(tmp_path)
    transaction = _decided_update(installation)

    allowed, detail = commit_transaction(installation.data_dir, resources=installation.resources)
    if not allowed or read_journal(installation.data_dir, installation.resources) is not None:
        pytest.fail(f"set-up: the healthy-start commit did not close the transaction: {detail}")

    key = installation_key(installation.resources)
    record = installation.data_dir / f"update-result-{key}.json"
    assert record.is_file(), (
        f"transaction {transaction} was committed and its journal deleted, and no "
        f"completion record exists at <data>/{record.name}"
    )
    payload = json.loads(record.read_text(encoding="utf-8"))
    assert payload.get("transaction") == transaction
    assert payload.get("outcome") == "installed"
    assert payload.get("installation") == key


class _UnusedController:
    """The cleanup path never talks to the controller; ``_bundle_paths`` is replaced."""

    url = "http://127.0.0.1:3199/"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="contract §2.5: healthy-start cleanup removes the whole <data>/updates folder",
)
def test_healthy_start_cleanup_removes_only_the_committed_transactions_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``<data>/updates`` is shared, and healthy-start cleanup deletes all of it.

    Both paths do: ``desktop.py:705-713`` off macOS, ``desktop.py:822-836`` on
    it. Whichever one this host takes, another transaction's download must
    survive, and the committed transaction's own staging must not.
    """

    installation = _installation(tmp_path)
    _decided_update(installation)
    other = installation.data_dir / "updates" / "9.9.10" / "downloads" / "update-app-9.9.10.zip"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"another transaction's download")

    window = desktop.DesktopWindow(
        _UnusedController(),  # type: ignore[arg-type]
        pythonnet_loader=lambda: object(),
        webview2_probe=lambda: True,
    )
    monkeypatch.setattr(
        window,
        "_bundle_paths",
        lambda: (installation.bundle, installation.resources, installation.data_dir),
    )
    # The macOS path reseals with codesign. What is under test here is which
    # files the cleanup removes, not the seal.
    monkeypatch.setattr(desktop, "repair_bundle", lambda *_args, **_kwargs: None)

    window._finish_healthy_bundle_update(_healthy_snapshot())

    if (installation.resources / "app.previous").exists():
        pytest.fail(
            "set-up: the healthy start did not commit and reclaim; update.log: "
            + _update_log(installation)
        )
    assert other.is_file(), (
        "healthy-start cleanup deleted a download that belongs to another transaction: "
        f"{other.relative_to(installation.data_dir).as_posix()}"
    )
    assert not (installation.data_dir / "updates" / "9.9.9").exists(), (
        "healthy-start cleanup kept the committed transaction's own staging"
    )


# ---------------------------------------------------------------------------
# Contract §4.5: every launch mode settles its transaction
# ---------------------------------------------------------------------------


_FAKE_SERVER = textwrap.dedent(
    r'''
    """Answer the status controller the way launch/serve.py does, and no more."""

    import argparse
    from pathlib import Path
    import time

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--status-control", type=Path, required=True)
    args, _unknown = parser.parse_known_args()
    args.status_control.with_name("ready.json").write_text(
        '{"host":"127.0.0.1","port":%d}\n' % args.port, encoding="utf-8"
    )
    print("fake server ready", flush=True)
    while not args.status_control.is_file():
        time.sleep(0.02)
    '''
)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="contract §4.5: only the desktop window commits the update transaction",
)
def test_a_browser_mode_start_settles_the_update_transaction(tmp_path: Path) -> None:
    """Browser mode reaches a healthy start and leaves the transaction open.

    ``commit_transaction`` has one caller, the desktop window
    (``desktop.py:671``). Browser mode runs the same ``StatusController``
    without that window, and Linux lands in it on every relaunch when Qt
    cannot open a window (``desktop.py:1778-1780``). The open transaction keeps
    ``.previous``, and the next update is then refused
    (``apply_update.py:948-950``).
    """

    installation = _installation(tmp_path, sys.platform)
    transaction = _decided_update(installation, sys.platform)
    app_layer = installation.resources / "app"
    _with_interface(app_layer)
    fake_server = tmp_path / "fake_server.py"
    fake_server.write_text(_FAKE_SERVER, encoding="utf-8")

    controller = StatusController(
        repo_root=app_layer,
        server_command=(sys.executable, str(fake_server)),
        server_args=("--data-dir", str(installation.data_dir)),
        environ={**os.environ, "WG2_BUNDLE": "1", "WG2_APP_ROOT": str(app_layer)},
        request_timeout=0.2,
        shutdown_timeout=1.0,
        request_probe=lambda url, _timeout: (
            (200, b'{"version":"test"}')
            if url.endswith("/health")
            else (200, b"<!doctype html><html><body>fake SPA</body></html>")
        ),
    )
    try:
        controller.start()
        deadline = time.monotonic() + 20.0
        snapshot = controller.poll()
        while not (
            snapshot.backend.state is ServiceState.OK
            and snapshot.frontend.state in {ServiceState.OK, ServiceState.WARNING}
        ):
            if time.monotonic() > deadline:
                pytest.fail(f"set-up: the controller never reported a healthy start: {snapshot}")
            time.sleep(0.05)
            snapshot = controller.poll()
        # One more look, as the status window's own loop takes after startup.
        controller.poll()
    finally:
        controller.close()

    journal = read_journal(installation.data_dir, installation.resources)
    assert journal is None, (
        "a browser-mode start reached a healthy backend and interface, and update "
        f"transaction {transaction} is still open in state {journal and journal.get('state')!r}"
    )


class _FakeLock:
    def acquire(self, _port: int) -> None:
        pass

    def update_port(self, _port: int) -> None:
        pass

    def release(self) -> None:
        pass


class _FakeListener:
    def close(self) -> None:
        pass


class _ServerThatNeverServed:
    """Stands in for uvicorn: it is created, serves nothing, and returns."""

    def __init__(self, _config: object) -> None:
        self.should_exit = False
        self.started = False

    def run(self, *, sockets: list[object]) -> None:
        return None


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="contract §4.5: a --no-gui start neither confirms nor reports the transaction",
)
def test_a_no_gui_start_confirms_or_reports_the_update_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--no-gui`` runs the server in-process, with no controller and no window.

    Nothing on that path (``statusapp/__main__.py:493-518``) commits the
    transaction or says why it did not. The stand-in server never serves, so
    there is no evidence of a healthy start, and the only right answer is the
    report: the transaction stays open and ``update.log`` names it. Committing
    here would be the wrong fix. The positive half, a live server that confirms
    its build, needs a real server and belongs with the implementation.

    The stand-in replaces ``uvicorn.Server`` where ``launch/serve.py`` looks it
    up at call time. The interface check before the server starts reads the
    checkout's own built SPA (``FRONTEND_INDEX`` is resolved at import), which
    every suite run builds first.
    """

    installation = _installation(tmp_path, sys.platform)
    transaction = _decided_update(installation, sys.platform)
    app_layer = installation.resources / "app"
    paths = ensure_data_layout(installation.data_dir)

    monkeypatch.delenv("WG2_PORT", raising=False)
    monkeypatch.setenv("WG2_BUNDLE", "1")
    monkeypatch.setenv("WG2_APP_ROOT", str(app_layer))
    monkeypatch.setenv("WG2_DATA_DIR", str(installation.data_dir))
    monkeypatch.setenv("WG2_NO_BROWSER", "1")
    monkeypatch.setattr(serve, "ensure_data_layout", lambda: paths)
    monkeypatch.setattr(serve, "setup_logging", lambda _paths: None)
    monkeypatch.setattr(serve, "flush_logs", lambda: None)
    monkeypatch.setattr(serve, "InstanceLock", lambda _path: _FakeLock())
    monkeypatch.setattr(serve, "_release_interface_error", lambda: None)
    monkeypatch.setattr(serve, "auto_migrate_v1", lambda *_args: [])
    monkeypatch.setattr(serve, "reserve_port", lambda *_args, **_kwargs: (_FakeListener(), 3100))
    monkeypatch.setattr(serve, "create_app", lambda **_kwargs: object())
    monkeypatch.setattr(serve.uvicorn, "Server", _ServerThatNeverServed)
    monkeypatch.setattr(serve, "harden_console", lambda *_args: None)
    before = _update_log(installation)

    exit_code = statusapp_main.main(["--no-gui", "--data-dir", str(installation.data_dir)])

    if exit_code != 0:
        pytest.fail(
            f"set-up: the --no-gui start did not run (exit {exit_code}); update.log: "
            + _update_log(installation)
        )
    journal = read_journal(installation.data_dir, installation.resources)
    written = _update_log(installation)[len(before) :]
    assert journal is not None and journal.get("state") == "installed", (
        f"a --no-gui start whose server never served settled update transaction "
        f"{transaction} anyway (journal now {journal!r}); nothing proved a healthy start"
    )
    assert transaction in written, (
        f"a --no-gui start ran and exited, left update transaction {transaction} open, "
        "and wrote nothing about it to update.log"
    )


# ---------------------------------------------------------------------------
# Contract §4.2: no new installation-owned work after restart approval
# ---------------------------------------------------------------------------


RUNTIME_ID = "0123456789ab"

SOLVE_BODY: dict[str, Any] = {
    "design": {
        "formula": "OSSE",
        "L": 120,
        "a": 45,
        "simulation": {"f1": 300, "f2": 3000, "num_frequencies": 4},
    },
    "options": {"engine": "dryrun", "stage_delay_ms": 1},
}


def _zip(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, value)
    return output.getvalue()


def _app_archive(version: str) -> bytes:
    manifest = {
        "schemaVersion": 1,
        "version": version,
        "commit": "a" * 40,
        "runtimeId": RUNTIME_ID,
    }
    return _zip(
        {
            "APP-MANIFEST.json": json.dumps(manifest).encode(),
            "launchers/apply_update.py": b"# staged updater\n",
        }
    )


async def _post(app: Any, path: str, body: dict[str, Any]) -> tuple[int, bytes]:
    """One loopback POST straight through the ASGI app, as ``test_jobs_api.py`` sends them."""

    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {
                "type": "http.request",
                "body": json.dumps(body).encode(),
                "more_body": False,
            }
        # A live connection blocks here until the client goes away; answering
        # with a disconnect instead can cancel the response before it starts.
        await asyncio.Event().wait()
        raise RuntimeError("unreachable")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"127.0.0.1:3100"), (b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 3100),
        },
        receive,
        send,
    )
    start = next(item for item in sent if item["type"] == "http.response.start")
    response = b"".join(
        item.get("body", b"") for item in sent if item["type"] == "http.response.body"
    )
    return start["status"], response


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="contract §4.2: nothing refuses new work once a restart has been approved",
)
def test_a_solve_submitted_after_restart_approval_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A solve that starts after approval is ended by a restart it never heard of.

    Approval is the moment the handoff request is written (contract §4.1). The
    install route has no job check (``updates/api.py:70-87``), and neither has
    ``/api/solve`` (``jobs/api.py:351``).
    """

    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    request_path = tmp_path / "control" / "update.json"
    archive = _app_archive("2.0.1")
    digest = hashlib.sha256(archive).hexdigest()
    name = "update-app-2.0.1.zip"
    base = "https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1/"

    def download(_url: str, destination: Path, _limit: int, progress: Any) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive)
        progress(len(archive))

    async def scenario() -> tuple[int, bytes]:
        app = create_app(data_dir=tmp_path / "data", update_request_path=request_path)
        installer = app.state.update_service.bundle_installer
        if installer is None:
            pytest.fail("set-up: an app given an update request path has no bundle installer")
        # The app's own installer, with the network and the disk probes replaced;
        # everything from the checksum to writing the handoff request is real.
        installer.downloader = download
        installer.small_fetcher = lambda _url, _limit: f"{digest}  {name}\n".encode()
        installer.volume_probe = lambda _path: "one volume"
        installer.free_space_probe = lambda _path: 10**12
        installer.start(
            "2.0.1",
            [
                {
                    "name": name,
                    "url": base + name,
                    "sha256Url": base + name + ".sha256",
                    "bytes": len(archive),
                    "layer": "app",
                }
            ],
            expected_runtime_id=RUNTIME_ID,
            installed_runtime_id=RUNTIME_ID,
        )
        deadline = time.monotonic() + 20.0
        while (state := installer.status()["installState"]) not in {"ready", "failed"}:
            if time.monotonic() > deadline:
                pytest.fail(f"set-up: staging never finished: {installer.status()}")
            await asyncio.sleep(0.02)
        if state != "ready" or not request_path.is_file():
            pytest.fail(f"set-up: the restart was never approved: {installer.status()}")

        runtime = app.state.jobs_runtime
        try:
            return await _post(app, "/api/solve", SOLVE_BODY)
        finally:
            await runtime.wait_idle()
            await runtime.shutdown()

    status, raw = asyncio.run(scenario())

    assert status == 409, (
        f"a solve was accepted (HTTP {status}) after the update's restart was approved "
        f"and its handoff request written: {raw[:200]!r}"
    )
    assert json.loads(raw).get("error", {}).get("code") == "update_restart_pending"


# ---------------------------------------------------------------------------
# Contract §3: an old release's launcher runs this checkout's helper
# ---------------------------------------------------------------------------


def _v03x_handoff_arguments(
    *,
    bundle: Path,
    data_dir: Path,
    staged_app: Path,
    staged_runtime: Path | None,
    parent_pid: int,
    server_args: tuple[str, ...],
) -> list[str]:
    """What the v0.3.1 and v0.3.2 launchers pass the staged helper.

    Frozen from ``launch_bundle_update_handoff`` in
    ``launchers/statusapp/updater.py`` at both tags: the interpreter, then
    ``<staged app>/launchers/apply_update.py``, then these, in this order.
    ``test_a_released_launcher_hands_the_candidate_a_command_it_accepts``
    rebuilds it from the released code wherever the tags are reachable.
    """

    arguments = [
        "--bundle",
        str(bundle),
        "--data-dir",
        str(data_dir),
        "--staged-app-dir",
        str(staged_app),
        "--parent-pid",
        str(parent_pid),
    ]
    if staged_runtime is not None:
        arguments += ["--staged-runtime-dir", str(staged_runtime)]
    arguments += [f"--relaunch-arg={argument}" for argument in server_args]
    return arguments


class ReleasedClientInstall(NamedTuple):
    bundle: Path
    resources: Path
    data_dir: Path
    staged_app: Path
    staged_runtime: Path


def _released_client_install(tmp_path: Path) -> ReleasedClientInstall:
    """A macOS-shaped install with an update staged where v0.3.x stages it.

    macOS-shaped on every host, as in ``test_apply_update.py``: the reseal runs
    through an injected runner, so no codesign runs anywhere.
    """

    root = tmp_path.resolve()
    bundle = root / "Waveguide Generator.app"
    resources = bundle / "Contents" / "Resources"
    data_dir = root / "data"
    staged_app = data_dir / "updates" / "2.0.1" / "staged" / "app"
    staged_runtime = staged_app.parent / "runtime"
    for path, marker in (
        (resources / "app", "old app"),
        (resources / "runtime", "old runtime"),
        (staged_app, "new app"),
        (staged_runtime, "new runtime"),
    ):
        path.mkdir(parents=True)
        (path / "marker.txt").write_text(marker, encoding="utf-8")
    # What an old launcher checks for before it starts anything.
    (staged_app / "launchers").mkdir()
    (staged_app / "launchers" / "apply_update.py").write_text(
        "# the candidate's helper\n", encoding="utf-8"
    )
    (staged_runtime / "bin").mkdir()
    (staged_runtime / "bin" / "python3.13").write_text("python\n", encoding="utf-8")
    return ReleasedClientInstall(bundle, resources, data_dir, staged_app, staged_runtime)


def _run_candidate_helper(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> tuple[int, list[dict[str, Any]], list[list[str]], list[str]]:
    """Run this checkout's ``apply_update.main`` on an old launcher's command line.

    ``main`` parses the arguments exactly as the staged script would. The
    transaction it starts is the real one, with the platform fixed to macOS and
    the process-level effects (waiting for the parent, codesign, the relaunch)
    injected.
    """

    real_apply_update = apply_update_module.apply_update
    calls: list[dict[str, Any]] = []
    relaunched: list[list[str]] = []
    failures: list[str] = []

    def apply_update_without_process_effects(**from_main: Any) -> int:
        calls.append(dict(from_main))
        return real_apply_update(
            **from_main,
            platform_name="darwin",
            runner=lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
            relauncher=lambda command, platform_name, **_kwargs: relaunched.append(
                [str(part) for part in command]
            ),
            waiter=lambda _pid: True,
            failure_reporter=failures.append,
        )

    monkeypatch.setattr(apply_update_module, "apply_update", apply_update_without_process_effects)
    return apply_update_module.main(arguments), calls, relaunched, failures


@pytest.mark.parametrize("with_runtime", [True, False], ids=["app-and-runtime", "app-only"])
def test_the_candidate_helper_installs_from_a_v03x_launchers_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_runtime: bool
) -> None:
    """Contract §3.2: the old launcher runs the candidate's helper.

    So the helper owns compatibility with the old command line and the
    version-keyed staging layout under the data directory.
    """

    install = _released_client_install(tmp_path)
    staged_runtime = install.staged_runtime if with_runtime else None
    arguments = _v03x_handoff_arguments(
        bundle=install.bundle,
        data_dir=install.data_dir,
        staged_app=install.staged_app,
        staged_runtime=staged_runtime,
        parent_pid=4321,
        server_args=("--port", "3110"),
    )

    exit_code, calls, relaunched, failures = _run_candidate_helper(arguments, monkeypatch)

    assert failures == []
    assert exit_code == 0
    # Only what the old command line determines; main may pass more.
    from_command_line = {
        "bundle": install.bundle,
        "data_dir": install.data_dir,
        "staged_app": install.staged_app,
        "staged_runtime": staged_runtime,
        "parent_pid": 4321,
        "relaunch_arguments": ["--port", "3110"],
    }
    assert len(calls) == 1
    assert {key: calls[0].get(key) for key in from_command_line} == from_command_line
    installed = install.resources
    assert (installed / "app" / "marker.txt").read_text(encoding="utf-8") == "new app"
    assert (installed / "app.previous" / "marker.txt").read_text(encoding="utf-8") == "old app"
    runtime_marker = (installed / "runtime" / "marker.txt").read_text(encoding="utf-8")
    assert runtime_marker == ("new runtime" if with_runtime else "old runtime")
    assert len(relaunched) == 1


_RELEASED_LAUNCHER_DRIVER = textwrap.dedent(
    '''
    """Run one released launcher's handoff, from that release's own files."""

    import importlib.util
    import json
    from pathlib import Path
    import subprocess
    import sys

    tree, request_path, data_dir, app_layer = (Path(argument) for argument in sys.argv[1:5])
    sys.path.insert(0, str(tree))
    spec = importlib.util.spec_from_file_location(
        "released_statusapp_updater", tree / "launchers" / "statusapp" / "updater.py"
    )
    updater = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = updater
    spec.loader.exec_module(updater)
    for name in ("launchers.apply_update", "server.platform.paths"):
        origin = Path(sys.modules[name].__file__).resolve()
        if not origin.is_relative_to(tree.resolve()):
            raise SystemExit(f"{name} came from {origin}, not from the released tree")

    request = updater.consume_update_request(request_path, data_dir=data_dir)
    if not isinstance(request, updater.BundleUpdateRequest):
        raise SystemExit(f"the released launcher did not accept the request: {request!r}")
    started = []
    subprocess.Popen = lambda command, **_options: started.append([str(p) for p in command])
    updater.launch_bundle_update_handoff(
        app_layer,
        request,
        4321,
        environ={"WG2_DATA_DIR": str(data_dir)},
        server_args=("--port", "3110"),
        platform_name="darwin",
    )
    print(json.dumps(started[0]))
    '''
)


def _released_tree(tag: str, destination: Path) -> Path:
    """The launcher side of a release, extracted from its tag."""

    try:
        archive = subprocess.run(  # noqa: S603 - fixed program and arguments
            [  # noqa: S607 - git from PATH, as every repository script finds it
                "git",
                "-C",
                str(REPOSITORY_ROOT),
                "archive",
                "--format=tar",
                tag,
                "launchers",
                "shared",
                "server/__init__.py",
                "server/platform",
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"git could not read {tag} here ({exc}); the frozen command line is tested")
    if archive.returncode != 0:
        pytest.skip(
            f"{tag} is not reachable from this checkout (a CI checkout has one commit and "
            "no tags); the frozen command line is still tested"
        )
    with tarfile.open(fileobj=BytesIO(archive.stdout)) as released:
        released.extractall(destination, filter="data")
    return destination


@pytest.mark.parametrize("tag", ["v0.3.1", "v0.3.2"])
def test_a_released_launcher_hands_the_candidate_a_command_it_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tag: str
) -> None:
    """Contract §3.1 and §3.2, end to end, with the released launcher's own code.

    The released launcher consumes the schema-1 request its own server wrote
    and builds the helper's command line; this checkout's helper then installs
    from it.
    """

    tree = _released_tree(tag, tmp_path / "released")
    install = _released_client_install(tmp_path / "install")
    request = tmp_path.resolve() / "control" / "update.json"
    request.parent.mkdir(parents=True)
    # What server/updates/bundle.py writes at both tags.
    request.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "apply_bundle",
                "version": "2.0.1",
                "stagedAppDir": str(install.staged_app),
                "stagedRuntimeDir": str(install.staged_runtime),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    driver = tmp_path / "released_launcher_driver.py"
    driver.write_text(_RELEASED_LAUNCHER_DRIVER, encoding="utf-8")

    completed = subprocess.run(  # noqa: S603 - fixed interpreter, fixed script
        [
            sys.executable,
            str(driver),
            str(tree),
            str(request),
            str(install.data_dir),
            str(install.resources / "app"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    command = json.loads(completed.stdout.strip().splitlines()[-1])
    assert command[1] == str(install.staged_app / "launchers" / "apply_update.py")
    assert command[2:] == _v03x_handoff_arguments(
        bundle=install.bundle,
        data_dir=install.data_dir,
        staged_app=install.staged_app,
        staged_runtime=install.staged_runtime,
        parent_pid=4321,
        server_args=("--port", "3110"),
    )
    assert not request.exists()

    exit_code, _calls, relaunched, failures = _run_candidate_helper(command[2:], monkeypatch)

    assert failures == []
    assert exit_code == 0
    installed = install.resources
    assert (installed / "app" / "marker.txt").read_text(encoding="utf-8") == "new app"
    assert (installed / "runtime" / "marker.txt").read_text(encoding="utf-8") == "new runtime"
    assert len(relaunched) == 1
