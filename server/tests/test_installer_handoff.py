"""The new sending facade and full-installer receiving boundary."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import tarfile
import threading
import time

import pytest

from launchers.full_installer import _extract_linux, consume_full_installer_request, launch_full_installer
from launchers.statusapp.updater import UpdateHandoffError
from server.updates.installer_client import VerifiedInstaller
from server.updates.installer_service import InstallerUpdateService
from server.updates.restart import RestartApproval
from server.tests._symlinks import requires_symlinks


def installed(tmp_path):
    app = tmp_path / "waveguide-generator" / "app"
    app.mkdir(parents=True)
    data = tmp_path / "data"
    folder = data / "update-install" / "0.3.4"
    folder.mkdir(parents=True)
    asset = folder / "Waveguide.Generator-0.3.4-linux-x86_64.tar.gz"
    asset.write_bytes(b"verified test installer")
    ready = VerifiedInstaller("v0.3.4", "0.3.4", asset, hashlib.sha256(asset.read_bytes()).hexdigest(), asset.stat().st_size, app.parent, "linux-x86_64")
    return app, data, ready


class Client:
    def __init__(self, ready, approval):
        self.ready, self.approval = ready, approval
        self.cancelled = []
        self.error = None

    def verified_installer(self):
        assert self.approval.pending == self.ready.version
        if self.error:
            raise self.error
        return self.ready

    def cancel_download(self, reason):
        self.cancelled.append(reason)

    def close(self):
        pass

    def get_status(self, **_kwargs):
        return {"canInstall": True}


def facade(tmp_path):
    app, data, ready = installed(tmp_path)
    approval = RestartApproval()
    client = Client(ready, approval)
    request = tmp_path / "request.json"
    service = InstallerUpdateService(running_version="0.3.3", data_dir=data,
        repo_root=app, update_request_path=request, client=client, restart_approval=approval)
    return service, client, request, ready, app, data


def target_posix_durability(monkeypatch, module):
    """Model a POSIX handoff on Windows; retain native sync calls on POSIX."""
    sync_file, sync_directory = module._sync_file, module._sync_directory
    calls = []
    def file(path):
        if os.name == "nt":
            # Windows _commit requires a writable handle; the target is POSIX.
            with path.open("r+b") as stream:
                os.fsync(stream.fileno())
        else:
            sync_file(path)
        calls.append(("file", path))
    def directory(path):
        assert path.is_dir() and not path.is_symlink()
        if os.name != "nt":
            sync_directory(path)
        calls.append(("directory", path))
    monkeypatch.setattr(module, "_sync_file", file)
    monkeypatch.setattr(module, "_sync_directory", directory)
    return calls


def test_approval_precedes_reverification_and_atomic_publication(tmp_path):
    service, _, path, ready, app, data = facade(tmp_path)
    service._publish(ready)
    payload = json.loads(path.read_text())
    assert payload["kind"] == "full_installer" and payload["schemaVersion"] == 2
    assert service.restart_approval.pending == "0.3.4"
    handled, request = consume_full_installer_request(path, repo_root=app, data_dir=data, now=json.loads(path.read_text())["readyAtEpoch"] + 1)
    assert handled and request.installer == ready.path
    assert not path.exists()
    assert service.restart_approval.pending == "0.3.4"  # consumption cannot admit solves


def test_installed_bridge_transaction_does_not_gate_windows_installer_handoff(tmp_path, monkeypatch):
    from dataclasses import replace

    from launchers import apply_update
    from shared.release_assets import WINDOWS_PLATFORM, windows_setup_name

    service, client, path, ready, app, data = facade(tmp_path)
    asset = ready.path.with_name(windows_setup_name(ready.version))
    ready.path.rename(asset)
    ready = replace(ready, path=asset, platform=WINDOWS_PLATFORM)
    client.ready = ready
    apply_update.begin_update_transaction(data_dir=data, bundle=app.parent,
        resources=app.parent, layers=[], platform_name="win32")
    apply_update.set_journal_state(data, app.parent, "installed")
    for name in ("app.previous", "runtime.previous"):
        (app.parent / name).mkdir()
    before = apply_update.read_journal(data, app.parent)
    marker = app.parent / ".update-transaction-open.json"
    marker_before = marker.read_bytes()

    def download(callback):
        callback(ready)
        return {"accepted": True}

    monkeypatch.setattr(client, "request_download", download, raising=False)
    assert service.get_status(force=True)["canInstall"] is True
    assert service.request_install()["accepted"] is True
    assert service.get_status()["restartPending"] == ready.version
    handled, request = consume_full_installer_request(path, repo_root=app,
        data_dir=data, now=json.loads(path.read_text())["readyAtEpoch"] + 1)
    assert handled and request is not None
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    launch_full_installer(app, request, 12345, data_dir=data)
    assert len(launched) == 1
    assert "/WAITPID=12345" in launched[0] and "/RELAUNCH" in launched[0]
    assert apply_update.read_journal(data, app.parent) == before
    assert marker.read_bytes() == marker_before
    assert (app.parent / "app.previous").is_dir()


def test_reverification_failure_releases_exact_approval_without_a_request(tmp_path):
    service, client, path, ready, _, _ = facade(tmp_path)
    client.error = ValueError("changed bytes")
    with pytest.raises(ValueError, match="changed bytes"):
        service._publish(ready)
    assert service.restart_approval.pending is None
    assert not path.exists() and client.cancelled


def test_publication_failure_does_not_revoke_an_earlier_foreign_request(tmp_path):
    service, _, path, ready, _, _ = facade(tmp_path)
    path.write_text("earlier request")
    with pytest.raises(Exception, match="earlier update request"):
        service._publish(ready)
    assert path.read_text() == "earlier request"
    assert service.restart_approval.pending is None


def test_release_revokes_only_its_own_request(tmp_path):
    service, client, path, ready, _, _ = facade(tmp_path)
    service._publish(ready)
    service.restart_approval.release("launcher refused")
    assert not path.exists()
    assert path.with_name(path.name + ".revoked").exists()
    assert client.cancelled == ["launcher refused"]


@pytest.mark.parametrize("settle", ["pending", "expired", "discarded", "write-failure"])
def test_default_app_full_installer_lifecycle_and_shared_restart_latch(tmp_path, monkeypatch, settle):
    """Default create_app routes use the new facade and their actual shared latch."""
    import asyncio

    from launch import serve
    from server.app import create_app
    from server.tests.test_update_transaction_contract import _post
    from server.updates.installer_checker import InstallerClientError

    _app, data, ready = installed(tmp_path)
    request = tmp_path / "control/update.json"
    request.parent.mkdir()
    if settle == "write-failure":
        request.mkdir(parents=True)

    class AppClient(Client):
        def __init__(self):
            super().__init__(ready, None)
            self.started = self.closed = 0
            self.state = "idle"

        def start_checker(self):
            self.started += 1

        def close(self):
            self.closed += 1

        def request_download(self, callback):
            try:
                callback(self.ready)
                self.state = "ready"
            except Exception as exc:
                self.state = "failed"
                raise InstallerClientError(str(exc)) from exc
            return self.get_status()

        def cancel_download(self, reason):
            super().cancel_download(reason)
            self.state = "failed"

        def get_status(self, **kwargs):
            return {"canInstall": self.state != "failed", "installState": self.state}

    client = AppClient()
    monkeypatch.setattr("server.updates.installer_service.InstallerClient", lambda **kwargs: client)
    application = create_app(data_dir=data, update_request_path=request, solver_warmup=False)
    service = application.state.update_service
    assert isinstance(service, InstallerUpdateService)
    client.approval = application.state.update_restart
    assert service.restart_approval is client.approval
    assert service.start_checker in application.router.on_startup
    assert service.close in application.router.on_shutdown

    async def scenario():
        # Invoke the actual registered updater lifecycle, avoiding unrelated
        # native solver prewarm work in this API/latch integration test.
        service.start_checker()
        try:
            status, _ = await _post(application, "/api/updates/install", None,
                headers=((b"x-wg-update", b"install"),))
            assert status == (409 if settle == "write-failure" else 202)
            if settle == "write-failure":
                assert client.state == "failed" and client.cancelled
                assert request.is_dir() and client.approval.pending is None
            else:
                assert json.loads(request.read_text())["kind"] == "full_installer"
                denied, raw = await _post(application, "/api/cadlink/ingest", {"bundlePath": "returns/test.wgreturn"})
                assert denied == 409 and json.loads(raw)["error"]["code"] == "update_restart_pending"
                if settle == "expired":
                    monkeypatch.setattr(client.approval, "_ttl", 0.0)
                    shown_status, raw = await _post(application, "/api/updates/status", None, method="GET")
                    assert shown_status == 200 and json.loads(raw)["restartPending"] is None
                    assert json.loads(raw)["installState"] == "failed"
                elif settle == "discarded":
                    request.unlink()
                    serve._release_update_restart(application, "Discarded an invalid full installer request.")
                if settle != "pending":
                    assert client.state == "failed" and client.cancelled and not request.exists()
                    assert client.approval.pending is None
                    if settle == "expired":
                        assert request.with_name(request.name + ".revoked").is_file()
        finally:
            service.close()
            await application.state.jobs_runtime.wait_idle()
            await application.state.jobs_runtime.shutdown()

    asyncio.run(scenario())
    assert client.started == client.closed == 1
    if settle == "pending":
        # Shutdown for the approved handoff must preserve the request and latch.
        assert request.is_file() and client.approval.pending == ready.version


@pytest.mark.parametrize("action", ["release", "close"])
def test_status_close_and_release_do_not_wait_for_facade_rehash(tmp_path, action):
    service, client, path, ready, _, _ = facade(tmp_path)
    entered, finish = threading.Event(), threading.Event()
    def blocked():
        assert service.restart_approval.pending == ready.version
        entered.set()
        assert finish.wait(5)
        return ready
    client.verified_installer = blocked
    failures = []
    def publish():
        try:
            service._publish(ready)
        except Exception as exc:
            failures.append(exc)
    worker = threading.Thread(target=publish)
    worker.start()
    try:
        assert entered.wait(2)
        started = time.monotonic()
        assert service.get_status()["restartPending"] == "0.3.4"
        if action == "release":
            service.restart_approval.release("aborted while hashing")
        else:
            service.close()
        assert time.monotonic() - started < 0.5
        assert service.restart_approval.pending is None
        assert not path.exists()
    finally:
        finish.set()
        worker.join(2)
    assert not worker.is_alive() and failures
    assert not path.exists()


@pytest.mark.parametrize(
    "mutation",
    ["root", pytest.param("asset-link", marks=requires_symlinks), "digest", "size", "control", "platform"],
)
def test_reader_rejects_untrusted_destinations_and_proofs(tmp_path, mutation):
    service, _, path, ready, app, data = facade(tmp_path)
    service._publish(ready)
    value = json.loads(path.read_text())
    if mutation == "root":
        value["installRoot"] = str(tmp_path)
    elif mutation == "asset-link":
        ready.path.rename(ready.path.with_suffix(".real"))
        ready.path.symlink_to(ready.path.with_suffix(".real"))
    elif mutation == "digest":
        value["sha256"] = "not a checksum"
    elif mutation == "size":
        value["size"] = True
    elif mutation == "control":
        value["fromVersion"] = "0.3.3\n"
    else:
        value["platform"] = "elsewhere"
    path.write_text(json.dumps(value))
    with pytest.raises(UpdateHandoffError, match="invalid"):
        consume_full_installer_request(path, repo_root=app, data_dir=data, now=json.loads(path.read_text())["readyAtEpoch"] + 1)
    assert not path.exists()


def test_reader_keeps_delayed_new_requests_out_of_legacy_dispatch(tmp_path):
    service, _, path, ready, app, data = facade(tmp_path)
    service._publish(ready)
    assert consume_full_installer_request(path, repo_root=app, data_dir=data, now=0) == (True, None)
    assert path.exists()


def test_launcher_rehashes_before_starting_any_helper(tmp_path, monkeypatch):
    service, _, path, ready, app, data = facade(tmp_path)
    service._publish(ready)
    _, request = consume_full_installer_request(path, repo_root=app, data_dir=data, now=json.loads(path.read_text())["readyAtEpoch"] + 1)
    ready.path.write_bytes(b"x" * ready.size)
    called = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: called.append(a))
    with pytest.raises(UpdateHandoffError, match="checksum changed"):
        launch_full_installer(app, request, 123, data_dir=data)
    assert not called


@pytest.mark.parametrize("name,link", [("../outside", None), ("/outside", None), ("safe-link", "../../outside")])
@pytest.mark.parametrize("host_path", ["native", "windows"])
def test_linux_tar_rejects_traversal_before_extraction(tmp_path, monkeypatch, name, link, host_path):
    import launchers.full_installer as module
    if host_path == "windows":
        # The old host-dependent member parser must fail this on every host.
        monkeypatch.setattr(module, "Path", PureWindowsPath)
    asset = tmp_path / "bad.tar.gz"
    with tarfile.open(asset, "w:gz") as archive:
        preceding = tarfile.TarInfo("benign-first")
        preceding.size = 1
        archive.addfile(preceding, io.BytesIO(b"x"))
        entry = tarfile.TarInfo(name)
        if link:
            entry.type, entry.linkname = tarfile.SYMTYPE, link
            archive.addfile(entry)
        else:
            entry.size = 1
            archive.addfile(entry, io.BytesIO(b"x"))
    destination = tmp_path / "payload"
    destination.mkdir()
    def refuse_extract(*args, **kwargs):
        raise AssertionError("unsafe archive reached extraction")
    monkeypatch.setattr(tarfile.TarFile, "extractall", refuse_extract)
    with pytest.raises((ValueError, tarfile.TarError)):
        _extract_linux(asset, destination)
    assert list(destination.iterdir()) == []


def test_linux_tar_accepts_confined_runtime_links(tmp_path):
    asset = tmp_path / "good.tar.gz"
    with tarfile.open(asset, "w:gz") as archive:
        for name in ["waveguide-generator", "waveguide-generator/runtime"]:
            entry = tarfile.TarInfo(name)
            entry.type = tarfile.DIRTYPE
            archive.addfile(entry)
        for name in ["install.sh", "waveguide-generator/runtime/python3.13"]:
            entry = tarfile.TarInfo(name)
            entry.size = 1
            archive.addfile(entry, io.BytesIO(b"x"))
        entry = tarfile.TarInfo("waveguide-generator/runtime/python3")
        entry.type, entry.linkname = tarfile.SYMTYPE, "python3.13"
        archive.addfile(entry)
        entry = tarfile.TarInfo("waveguide-generator/runtime/python")
        entry.type, entry.linkname = tarfile.SYMTYPE, "python3"
        archive.addfile(entry)
        for name, target in [("hard-one", "waveguide-generator/runtime/python3.13"),
                             ("hard-two", "waveguide-generator/runtime/hard-one")]:
            entry = tarfile.TarInfo("waveguide-generator/runtime/" + name)
            entry.type, entry.linkname = tarfile.LNKTYPE, target
            archive.addfile(entry)
    destination = tmp_path / "payload"
    destination.mkdir()
    _extract_linux(asset, destination)
    assert (destination / "waveguide-generator/runtime/python3").read_bytes() == b"x"
    assert (destination / "waveguide-generator/runtime/python").read_bytes() == b"x"
    for name in ("hard-one", "hard-two"):
        assert (destination / "waveguide-generator/runtime" / name).read_bytes() == b"x"


def graph_member(name, kind, target=""):
    member = tarfile.TarInfo(name)
    member.type = {"directory": tarfile.DIRTYPE, "file": tarfile.REGTYPE,
                   "symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE}[kind]
    member.linkname = target
    if kind == "file":
        member.size = 1
    return member


@pytest.mark.parametrize("graph", [
    [("a", "symlink", "."), ("a/link", "symlink", "../outside")],
    [("a", "symlink", "."), ("x", "symlink", "a/../outside")],
    [("a", "symlink", "b"), ("b", "symlink", "a")],
    [("a", "hardlink", "b"), ("b", "hardlink", "a")],
    [("a", "file", ""), ("a/b", "file", "")],
    [("a", "symlink", "."), ("x", "hardlink", "a/../outside")],
    [("a", "directory", ""), ("x", "symlink", "a"),
     ("x/file", "file", ""), ("a/file", "file", "")],
    [("a", "directory", ""), ("x", "hardlink", "a")],
    [("a", "symlink", "b"), ("a/file", "file", "")],
    [("a", "symlink", "b"), ("a/file", "file", ""), ("b", "directory", "")],
    [("b", "directory", ""), ("a", "symlink", "missing/../b"), ("a/file", "file", "")],
])
def test_linux_tar_preflights_interacting_links_before_any_extraction(tmp_path, monkeypatch, graph):
    asset = tmp_path / "graph.tar.gz"
    with tarfile.open(asset, "w:gz") as archive:
        archive.addfile(graph_member("benign-first", "file"), io.BytesIO(b"x"))
        for name, kind, target in graph:
            member = graph_member(name, kind, target)
            archive.addfile(member, io.BytesIO(b"x") if member.isfile() else None)
    destination = tmp_path / "payload"
    destination.mkdir()
    def refuse_extract(*args, **kwargs):
        raise AssertionError("unsafe graph reached extraction")
    monkeypatch.setattr(tarfile.TarFile, "extractall", refuse_extract)
    with pytest.raises((ValueError, tarfile.TarError)):
        _extract_linux(asset, destination)
    assert list(destination.iterdir()) == []
    assert not (tmp_path / "outside").exists()


def test_linux_graph_accepts_confined_directory_aliases_and_parent_relative_links():
    import launchers.full_installer as module
    members = [graph_member(*row) for row in [
        ("bundle", "directory", ""),
        ("bundle/lib", "directory", ""),
        ("bundle/lib/real", "file", ""),
        ("bundle/lib64", "symlink", "lib"),
        ("bundle/lib64/extra", "file", ""),
        ("bundle/lib64/new/sub/file", "file", ""),
        ("bundle/lib/link", "symlink", "../lib64/real"),
        ("bundle/hard-one", "hardlink", "bundle/lib/real"),
        ("bundle/hard-two", "hardlink", "bundle/hard-one"),
    ]]
    module._validate_linux_members(members)


@pytest.mark.parametrize("bound", ["path", "links", "nodes", "components", "work"])
def test_linux_archive_graph_resolution_is_bounded_before_extraction(tmp_path, monkeypatch, bound):
    import launchers.full_installer as module
    asset = tmp_path / "bounded.tar.gz"
    with tarfile.open(asset, "w:gz") as archive:
        archive.addfile(graph_member("benign-first", "file"), io.BytesIO(b"x"))
        if bound == "path":
            monkeypatch.setattr(module, "MAX_ARCHIVE_PATH_LENGTH", 16)
            archive.addfile(graph_member("a", "symlink", "x" * 17))
        elif bound == "links":
            monkeypatch.setattr(module, "MAX_ARCHIVE_LINK_HOPS", 2)
            for name, target in [("a", "b"), ("b", "c"), ("c", "d")]:
                archive.addfile(graph_member(name, "symlink", target))
            archive.addfile(graph_member("d", "file"), io.BytesIO(b"x"))
        else:
            attribute, limit = {"nodes": ("MAX_ARCHIVE_VIRTUAL_NODES", 2),
                                "components": ("MAX_ARCHIVE_VIRTUAL_COMPONENTS", 1),
                                "work": ("MAX_ARCHIVE_GRAPH_WORK", 32)}[bound]
            monkeypatch.setattr(module, attribute, limit)
            archive.addfile(graph_member("a/b/c/d/e/file", "file"), io.BytesIO(b"x"))
    destination = tmp_path / "payload"
    destination.mkdir()
    def refuse_extract(*args, **kwargs):
        raise AssertionError("unbounded graph reached extraction")
    monkeypatch.setattr(tarfile.TarFile, "extractall", refuse_extract)
    with pytest.raises(ValueError, match="unsafe|excessive|graph budget"):
        _extract_linux(asset, destination)
    assert list(destination.iterdir()) == []


@requires_symlinks
def test_real_linux_archive_builder_runtime_link_layout_is_accepted(tmp_path):
    from scripts.build_bundle import deterministic_tar_gz
    source = tmp_path / "waveguide-generator"
    binary = source / "runtime/bin"
    binary.mkdir(parents=True)
    interpreter = binary / "python3.13"
    interpreter.write_bytes(b"representative interpreter")
    (binary / "python3").symlink_to("python3.13")
    os.link(interpreter, binary / "python")
    package = source / "runtime/lib/python3.13/site-packages/numpy"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# representative installed package\n")
    archive = tmp_path / "installer.tar.gz"
    deterministic_tar_gz(source, archive, archive_root="waveguide-generator",
        extra_root_files={"install.sh": ("#!/bin/sh\nexit 0\n", 0o755)})
    destination = tmp_path / "payload"
    destination.mkdir()
    _extract_linux(archive, destination)
    for name in ("python", "python3", "python3.13"):
        assert (destination / "waveguide-generator/runtime/bin" / name).read_bytes() == interpreter.read_bytes()
    assert (destination / "waveguide-generator/runtime/lib/python3.13/site-packages/numpy/__init__.py").is_file()


def test_release_and_atomic_link_are_serialized(tmp_path, monkeypatch):
    service, _, path, ready, _, _ = facade(tmp_path)
    entered, finish, released = threading.Event(), threading.Event(), threading.Event()
    real_link = os.link
    def blocked(source, destination, **kwargs):
        entered.set()
        assert finish.wait(5)
        return real_link(source, destination, **kwargs)
    monkeypatch.setattr(os, "link", blocked)
    worker = threading.Thread(target=service._publish, args=(ready,))
    worker.start()
    assert entered.wait(2)
    releaser = threading.Thread(target=lambda: (service.restart_approval.release("cancelled at link"), released.set()))
    releaser.start()
    try:
        assert not released.wait(.05)
    finally:
        finish.set()
        worker.join(2)
        releaser.join(2)
    assert not worker.is_alive() and not releaser.is_alive()
    assert released.is_set() and service.restart_approval.pending is None
    assert not path.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO race")
@pytest.mark.parametrize("part", ["request", "asset"])
def test_raced_fifo_open_cannot_block_request_or_hash(tmp_path, monkeypatch, part):
    service, _, path, ready, app, data = facade(tmp_path)
    service._publish(ready)
    now = json.loads(path.read_text())["readyAtEpoch"] + 1
    _, request = consume_full_installer_request(path, repo_root=app, data_dir=data, now=now) if part == "asset" else (None, None)
    target = ready.path if part == "asset" else path
    real_open = os.open
    changed = False
    def replace(candidate, flags, *args, **kwargs):
        nonlocal changed
        if not changed and str(candidate) == str(target):
            changed = True
            target.unlink()
            os.mkfifo(target)
        return real_open(candidate, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", replace)
    started = time.monotonic()
    with pytest.raises(UpdateHandoffError):
        if part == "asset":
            launch_full_installer(app, request, 123, data_dir=data)
        else:
            consume_full_installer_request(path, repo_root=app, data_dir=data, now=now)
    assert time.monotonic() - started < .5 and changed
    assert target.exists()


def test_approval_expiring_during_rehash_never_spawns_helper(tmp_path, monkeypatch):
    import launchers.full_installer as module
    service, _, path, ready, app, data = facade(tmp_path)
    service._publish(ready)
    _, request = consume_full_installer_request(path, repo_root=app, data_dir=data, now=json.loads(path.read_text())["readyAtEpoch"] + 1)
    packaged = app.parent / "runtime/bin"
    packaged.mkdir(parents=True)
    (packaged / "wg-installer-log").write_bytes(b"test logger")
    clock = [request.expires_at_epoch - 1]
    real_verify = module._verify
    def verify(candidate):
        real_verify(candidate)
        clock[0] = request.expires_at_epoch
    monkeypatch.setattr(module, "_verify", verify)
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    called = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: called.append(args))
    with pytest.raises(UpdateHandoffError, match="expired"):
        launch_full_installer(app, request, 123, data_dir=data)
    assert not called


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO race")
def test_diagnostic_log_replaced_by_fifo_cannot_block_status(tmp_path, monkeypatch):
    service, _, _, _, _, data = facade(tmp_path)
    path = data / "update-install/install.log"
    path.write_text("log")
    real_open = os.open
    def replace(candidate, flags, *args, **kwargs):
        if str(candidate) == str(path):
            path.unlink()
            os.mkfifo(path)
        return real_open(candidate, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", replace)
    assert service.diagnostic_logs()["logs"]["install.log"] is None
    assert path.exists()


def test_approval_expiring_during_extraction_cleans_only_prepared_objects(tmp_path, monkeypatch):
    import launchers.full_installer as module
    service, _, path, ready, app, data = facade(tmp_path)
    service._publish(ready)
    _, request = consume_full_installer_request(path, repo_root=app, data_dir=data, now=json.loads(path.read_text())["readyAtEpoch"] + 1)
    launcher_files = app / "launchers"
    launcher_files.mkdir()
    for name in ("installer-helper.sh", "installer-recovery.sh"):
        (launcher_files / name).write_text("#!/bin/sh\nexit 0\n")
    packaged = app.parent / "runtime/bin"
    packaged.mkdir(parents=True)
    (packaged / "wg-installer-log").write_bytes(b"test logger")
    clock = [request.expires_at_epoch - 1]
    def extract(_installer, _destination):
        clock[0] = request.expires_at_epoch
    monkeypatch.setattr(module, "_extract_linux", extract)
    target_posix_durability(monkeypatch, module)
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    called = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: called.append(args))
    with pytest.raises(UpdateHandoffError, match="expired"):
        launch_full_installer(app, request, 123, data_dir=data)
    assert not called
    assert not list((data / "update-install").glob("helper-*"))
    assert not (request.install_root.parent / ("." + request.install_root.name + ".installer-recovery")).exists()


def test_preparation_does_not_remove_foreign_recovery_entry(tmp_path, monkeypatch):
    import launchers.full_installer as module
    service, _, path, ready, app, data = facade(tmp_path)
    service._publish(ready)
    _, request = consume_full_installer_request(path, repo_root=app, data_dir=data, now=json.loads(path.read_text())["readyAtEpoch"] + 1)
    launcher_files = app / "launchers"
    launcher_files.mkdir()
    (launcher_files / "installer-helper.sh").write_text("#!/bin/sh\nexit 0\n")
    recovery = request.install_root.parent / ("." + request.install_root.name + ".installer-recovery")
    recovery.mkdir()
    retained = recovery / "config"
    retained.write_text("foreign recovery record")
    monkeypatch.setattr(module, "_extract_linux", lambda *args: None)
    with pytest.raises(UpdateHandoffError):
        launch_full_installer(app, request, 123, data_dir=data)
    assert retained.read_text() == "foreign recovery record"
    assert list(recovery.iterdir()) == [retained]


@pytest.mark.parametrize("limit", ["members", "bytes"])
def test_archive_limits_are_checked_during_iteration_before_any_extraction(tmp_path, monkeypatch, limit):
    import launchers.full_installer as module
    asset = tmp_path / "many.tar.gz"
    with tarfile.open(asset, "w:gz") as archive:
        for index in range(3):
            member = tarfile.TarInfo(f"file-{index}")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    if limit == "members":
        monkeypatch.setattr(module, "MAX_ARCHIVE_MEMBERS", 2)
    else:
        monkeypatch.setattr(module, "MAX_UNPACKED_BYTES", 2)
    def eager(_archive):
        raise AssertionError("archive must not eagerly collect all members")
    monkeypatch.setattr(tarfile.TarFile, "getmembers", eager)
    destination = tmp_path / "payload"
    destination.mkdir()
    with pytest.raises(ValueError, match="too many|too large"):
        _extract_linux(asset, destination)
    assert not list(destination.iterdir())


def test_tar_alias_paths_cannot_replace_a_previous_member(tmp_path):
    asset = tmp_path / "duplicate.tar.gz"
    with tarfile.open(asset, "w:gz") as archive:
        for name in ("file", "./file"):
            member = tarfile.TarInfo(name)
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    destination = tmp_path / "payload"
    destination.mkdir()
    with pytest.raises(ValueError, match="repeats a path"):
        _extract_linux(asset, destination)
    assert not list(destination.iterdir())


def test_handoff_copies_and_syncs_packaged_logger_before_detaching(tmp_path, monkeypatch):
    import launchers.full_installer as module
    service, _, path, ready, app, data = facade(tmp_path)
    service._publish(ready)
    _, request = consume_full_installer_request(path, repo_root=app, data_dir=data,
        now=json.loads(path.read_text())["readyAtEpoch"] + 1)
    sources = app / "launchers"
    sources.mkdir()
    for name in ("installer-helper.sh", "installer-recovery.sh"):
        (sources / name).write_text("#!/bin/sh\nexit 0\n")
    packaged = app.parent / "runtime/bin"
    packaged.mkdir(parents=True)
    (packaged / "wg-installer-log").write_bytes(b"packaged independent native logger")
    monkeypatch.setattr(module, "_extract_linux", lambda *args: None)
    durable = target_posix_durability(monkeypatch, module)
    called = []
    real_fsync = os.fsync
    synced = []
    def sync(fd):
        synced.append(os.fstat(fd).st_ino)
        return real_fsync(fd)
    monkeypatch.setattr(os, "fsync", sync)
    modes = []
    real_chmod = Path.chmod
    def chmod(path, mode, **kwargs):
        modes.append((path, mode))
        return real_chmod(path, mode, **kwargs)
    monkeypatch.setattr(Path, "chmod", chmod)
    def detach(command, **_kwargs):
        external = Path(command[-1]) / "log"
        assert external.read_bytes() == (packaged / "wg-installer-log").read_bytes()
        assert external.stat().st_ino in synced and (external, 0o700) in modes
        if os.name != "nt":
            assert external.stat().st_mode & 0o111
        assert durable == [("file", external.parent / "recover.sh"),
                           ("file", external), ("directory", external.parent)]
        called.append(command)
    monkeypatch.setattr(subprocess, "Popen", detach)
    launch_full_installer(app, request, 123, data_dir=data)
    assert len(called) == 1


@pytest.mark.parametrize("failed", ["recover.sh", "log", "directory"])
def test_recovery_sync_failure_refuses_detachment_and_cleans_only_prepared_objects(tmp_path, monkeypatch, failed):
    import launchers.full_installer as module
    service, _, path, ready, app, data = facade(tmp_path)
    service._publish(ready)
    _, request = consume_full_installer_request(path, repo_root=app, data_dir=data,
        now=json.loads(path.read_text())["readyAtEpoch"] + 1)
    sources = app / "launchers"
    sources.mkdir()
    for name in ("installer-helper.sh", "installer-recovery.sh"):
        (sources / name).write_text("#!/bin/sh\nexit 0\n")
    packaged = app.parent / "runtime/bin"
    packaged.mkdir(parents=True)
    logger = packaged / "wg-installer-log"
    logger.write_bytes(b"retained packaged logger")
    monkeypatch.setattr(module, "_extract_linux", lambda *args: None)
    target_posix_durability(monkeypatch, module)
    sync_file, sync_directory = module._sync_file, module._sync_directory
    def file(candidate):
        if candidate.name == failed:
            raise OSError("injected durability failure")
        sync_file(candidate)
    def directory(candidate):
        if failed == "directory":
            raise OSError("injected durability failure")
        sync_directory(candidate)
    monkeypatch.setattr(module, "_sync_file", file)
    monkeypatch.setattr(module, "_sync_directory", directory)
    called = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: called.append(args))
    with pytest.raises(UpdateHandoffError, match="durability failure"):
        launch_full_installer(app, request, 123, data_dir=data)
    assert not called
    assert not list((data / "update-install").glob("helper-*"))
    assert not (request.install_root.parent / ("." + request.install_root.name + ".installer-recovery")).exists()
    assert logger.read_bytes() == b"retained packaged logger"


def test_windows_full_installer_uses_detached_native_bounded_log_switch(tmp_path, monkeypatch):
    from dataclasses import replace
    from shared.release_assets import WINDOWS_PLATFORM
    service, _, path, ready, app, data = facade(tmp_path)
    service._publish(ready)
    _, request = consume_full_installer_request(path, repo_root=app, data_dir=data,
        now=json.loads(path.read_text())["readyAtEpoch"] + 1)
    request = replace(request, platform=WINDOWS_PLATFORM)
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: calls.append((command, kwargs)))
    launch_full_installer(app, request, 123, data_dir=data)
    command, options = calls.pop()
    assert command == [str(request.installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        f"/DIR={request.install_root}", "/WAITPID=123", f"/OUTCOME={data / 'update-install/outcome.json'}",
        f"/WGLOG={data / 'update-install/install.log'}", "/RELAUNCH"]
    assert options["creationflags"] == 0x00000008 | 0x00000200 | 0x08000000
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert options["close_fds"] is True and not calls
