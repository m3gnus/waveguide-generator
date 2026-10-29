"""Wire bytes and interrupted publication across the JSON owner's consumers."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from fastapi import HTTPException
import pytest

from server.platform import json_file

PAYLOAD = {"z": "räv", "a": {"y": 2, "b": [True, None]}}


def legacy_bytes(payload, *, indent=None, sort_keys=False, ensure_ascii=True,
                 trailing_newline=True, native=True):
    text = json.dumps(payload, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii)
    if trailing_newline:
        text += "\n"
    return text.replace("\n", os.linesep if native else "\n").encode("utf-8")


def observe_creation_modes(monkeypatch):
    modes = {}
    real_open = os.open

    def open_stage(path, flags, mode=0o777, *args, **kwargs):
        if flags & os.O_CREAT:
            modes[Path(path)] = mode
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(json_file.os, "open", open_stage)
    return modes


def caller(name):
    if name == "settings":
        from server.settings.store import _write_json_atomic
        return _write_json_atomic, legacy_bytes(PAYLOAD, indent=2, sort_keys=True), True
    if name == "fusion":
        from server.cadlink.fusion.fusion_delivery import _write_json
        return _write_json, legacy_bytes(PAYLOAD, indent=2, sort_keys=True), True
    if name == "pending-addin":
        from server.cadlink.fusion.addin_update import _write_json
        return _write_json, legacy_bytes(PAYLOAD, indent=2, sort_keys=True, native=False), True
    if name == "beat-state":
        from server.solver.beat_runtime.state import _atomic_write_json
        return _atomic_write_json, legacy_bytes(PAYLOAD, indent=2), True
    if name == "beat-registry":
        from server.solver.beat_runtime.registry import _atomic_json
        return _atomic_json, legacy_bytes(PAYLOAD, sort_keys=True, trailing_newline=False, native=False), True
    if name == "publication":
        from server.exports.wglink_publication import atomic_json
        return atomic_json, legacy_bytes(PAYLOAD, ensure_ascii=False), True
    if name == "archive":
        from server.workspace.archive import _write_json_atomic
        return _write_json_atomic, legacy_bytes(PAYLOAD, indent=2, sort_keys=True, native=False), False
    if name == "intake-result":
        from server.cadlink.intake.child_main import _write_result
        return _write_result, legacy_bytes(PAYLOAD, trailing_newline=False), False
    if name == "opencl-result":
        from server.solver.bempp_opencl import _write_probe_result
        return _write_probe_result, legacy_bytes(PAYLOAD, trailing_newline=False), False
    if name == "retirement":
        from launchers.backend_retire_request import write_request, REQUEST_FILENAME
        def write(path, payload):
            assert path.name == REQUEST_FILENAME
            write_request(path.parent, pid=42, mode="release", reason="räv", now=lambda: 100)
        payload = {"schema": 1, "pid": 42, "mode": "release", "reason": "räv", "requested_at": 100.0}
        return write, legacy_bytes(payload, trailing_newline=False), True
    if name == "release-notice":
        from launchers.statusapp.controller import StatusController
        return (lambda path, _: StatusController._write_release_notice(None, path, "räv"),
                legacy_bytes({"reason": "räv"}), False)
    if name == "install-transaction":
        from scripts.install_wglink import _write_transaction
        return _write_transaction, legacy_bytes(PAYLOAD, indent=2, sort_keys=True, native=False), True
    if name == "sphere-report":
        from scripts.beat_conformance.reference_sphere import write_report
        return (lambda path, payload: write_report(payload, path),
                legacy_bytes(PAYLOAD, indent=2, native=False), False)
    raise AssertionError(name)


@pytest.mark.parametrize("name", ["settings", "fusion", "pending-addin", "beat-state",
                                  "beat-registry", "publication", "archive", "intake-result", "opencl-result",
                                  "retirement", "release-notice", "install-transaction", "sphere-report"])
@pytest.mark.parametrize("error", [OSError, PermissionError])
def test_caller_bytes_and_failed_replace(tmp_path, monkeypatch, name, error):
    write, expected, sync_file = caller(name)
    modes = observe_creation_modes(monkeypatch)
    expected_mode = 0o666 if name in {"publication", "intake-result", "opencl-result", "release-notice"} else 0o600
    path = tmp_path / ("backend-retire.json" if name == "retirement" else "record.json")
    path.write_bytes(b'old record\n')
    real_replace, real_fsync = os.replace, os.fsync
    fsynced = []
    monkeypatch.setattr(json_file.time, "sleep", lambda _: None)

    def fsync(fd):
        fsynced.append(fd)
        real_fsync(fd)

    def fail(source, destination):
        assert modes[Path(source)] == expected_mode
        assert Path(source).read_bytes() == expected
        assert Path(destination).read_bytes() == b'old record\n'
        raise error("replace refused")

    monkeypatch.setattr(json_file.os, "fsync", fsync)
    monkeypatch.setattr(json_file.os, "replace", fail)
    with pytest.raises(error, match="replace refused"):
        write(path, PAYLOAD)
    assert bool(fsynced) is sync_file
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == b'old record\n'
    monkeypatch.setattr(json_file.os, "replace", real_replace)
    write(path, PAYLOAD)
    assert path.read_bytes() == expected
    assert list(tmp_path.iterdir()) == [path]


def test_usage_bytes_and_strict_directory_durability(tmp_path, monkeypatch):
    from server.cadlink import usage

    modes = observe_creation_modes(monkeypatch)
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    class Clock:
        @staticmethod
        def now(zone):
            return fixed

    monkeypatch.setattr(usage, "datetime", Clock)
    usage.record_usage(tmp_path, "setup-task")
    path = usage.usage_path(tmp_path)
    expected = {"schemaVersion": 1, "reason": "setup-task", "recordedAt": "2026-01-02T03:04:05Z"}
    assert path.read_bytes() == legacy_bytes(expected, sort_keys=True)
    assert modes and set(modes.values()) == {0o600}
    if os.name == "nt":
        return
    real_sync = os.fsync
    def fail_directory(fd):
        import stat
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory flush failed")
        real_sync(fd)
    monkeypatch.setattr(json_file.os, "fsync", fail_directory)
    with pytest.raises(OSError, match="directory flush failed"):
        usage.record_usage(tmp_path, "install-action")
    assert json.loads(path.read_bytes())["reason"] == "install-action"
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("viewport", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_cache_metadata_bytes_and_replace_failure(tmp_path, monkeypatch, viewport, fail):
    from server.cadlink.intake import ingest
    from server.contracts.canonical import canonical_normalized_bytes
    from server.mesh.artifact import mesh_text_sha256

    modes = observe_creation_modes(monkeypatch)
    mesh, metadata = tmp_path / "mesh.msh", tmp_path / "mesh.json"
    metadata.write_bytes(b'old metadata')
    content = {"msh_text": "mesh\n", "stats": {"z": 2}, "metadata": {"label": "räv"}}
    expected = {"content_sha256": mesh_text_sha256(content["msh_text"]),
                "stats": content["stats"], "metadata": content["metadata"]}
    if viewport:
        expected.update(transformed_geometry_hash="geometry", healing_mode="none", healing_options=[])
    real_replace = os.replace
    def replace(source, target):
        if Path(target) == metadata:
            assert modes[Path(source)] == 0o666
            assert Path(source).read_bytes() == canonical_normalized_bytes(expected) + b"\n"
            if fail:
                raise PermissionError("metadata locked")
        real_replace(source, target)
    monkeypatch.setattr(json_file.os, "replace", replace)
    def write():
        if viewport:
            ingest._write_viewport_cache(mesh, metadata, content,
                                         transformed_geometry_hash="geometry", healing={})
        else:
            ingest._write_cache(mesh, metadata, content)
    if fail:
        with pytest.raises(PermissionError, match="metadata locked"):
            write()
        assert metadata.read_bytes() == b'old metadata'
    else:
        write()
        assert metadata.read_bytes() == canonical_normalized_bytes(expected) + b"\n"
    assert set(tmp_path.iterdir()) == {mesh, metadata}


@pytest.mark.parametrize("failed_target", ["recovery", "authoring", "library", None])
def test_presets_pair_bytes_order_and_failure(tmp_path, monkeypatch, failed_target):
    from server.source_editor import PresetStore

    modes = observe_creation_modes(monkeypatch)
    store = PresetStore(tmp_path / "presets.json")
    store.path.write_bytes(b'old library')
    store.authoring_path.write_bytes(b'old authoring')
    presets = {"z": {"revision": "r", "label": "räv"}}
    authoring = {"z": {"revision": "r", "authoring": {"x": 1}}}
    library_bytes = legacy_bytes({"version": 1, "presets": presets}, trailing_newline=False, native=False)
    authoring_bytes = legacy_bytes(authoring, trailing_newline=False, native=False)
    real_replace = os.replace
    replaced = []
    def replace(source, target):
        assert modes[Path(source)] == 0o600
        raw = Path(source).read_bytes()
        if Path(target) == store.recovery_path:
            kind = "recovery"
            assert json.loads(raw) == {
                "library": library_bytes.decode(), "authoring": b'old authoring'.hex(),
            }
        elif Path(target) == store.path:
            kind = "library"
            assert raw == library_bytes
        else:
            assert Path(target) == store.authoring_path
            kind = "rollback" if raw == b'old authoring' else "authoring"
            assert raw == (b'old authoring' if kind == "rollback" else authoring_bytes)
        if failed_target == kind:
            raise PermissionError("preset locked")
        replaced.append(kind)
        real_replace(source, target)
    monkeypatch.setattr(json_file.os, "replace", replace)
    if failed_target:
        with pytest.raises(HTTPException) as exc:
            store._write(presets, authoring)
        assert exc.value.status_code == 503
        assert isinstance(exc.value.__cause__, PermissionError)
        assert store.path.read_bytes() == b'old library'
        assert store.authoring_path.read_bytes() == b'old authoring'
        assert replaced == {
            "recovery": [], "authoring": ["recovery"],
            "library": ["recovery", "authoring", "rollback"],
        }[failed_target]
    else:
        store._write(presets, authoring)
        assert replaced == ["recovery", "authoring", "library"]
        assert store.path.read_bytes() == library_bytes
        assert store.authoring_path.read_bytes() == authoring_bytes
    assert set(tmp_path.iterdir()) == {store.path, store.authoring_path}


def test_archive_keeps_captured_json_bytes(tmp_path, monkeypatch):
    from server.workspace.archive import _write_json_atomic

    raw = b'{ "z": "\\u00e4", "a": 1 }\r\n'
    path = tmp_path / "capture.json"
    _write_json_atomic(path, raw)
    assert path.read_bytes() == raw
    monkeypatch.setattr(json_file.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("locked")))
    with pytest.raises(OSError):
        _write_json_atomic(path, b'{}')
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]


def test_fusion_hooks_run_after_fsync_and_can_cancel(tmp_path, monkeypatch):
    from server.cadlink.fusion import fusion_delivery

    path = tmp_path / "request.json"
    path.write_bytes(b'old')
    events = []
    monkeypatch.setattr(json_file.os, "fsync", lambda _: events.append("fsync"))
    def before(payload):
        assert payload is PAYLOAD
        assert events == ["fsync"]
        events.append("before")
        return "receipt"
    def should(receipt):
        assert receipt == "receipt"
        events.append("should")
        return False
    assert fusion_delivery._write_json(path, PAYLOAD, before_replace=before, should_replace=should) == ("receipt", False)
    assert events == ["fsync", "before", "should"]
    assert path.read_bytes() == b'old'
    assert list(tmp_path.iterdir()) == [path]


def test_default_bytes_retry_and_success_never_unlinks_reused_staging_name(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    real_replace = os.replace
    calls = []
    def replace(source, target):
        calls.append(source)
        if len(calls) == 1:
            raise PermissionError("reader holds file")
        real_replace(source, target)
        Path(source).write_bytes(b'another writer')
    delays = []
    monkeypatch.setattr(json_file.os, "replace", replace)
    monkeypatch.setattr(json_file.time, "sleep", delays.append)
    json_file.write_json_atomic(path, PAYLOAD, replace_attempts=2, retry_seconds=0.01, durable_directory=True)
    assert path.read_bytes() == legacy_bytes(PAYLOAD, indent=2, native=False)
    assert delays == [0.01] and calls[0] == calls[1]
    assert Path(calls[-1]).read_bytes() == b'another writer'


def test_native_newlines_are_explicit_and_checksum_default_stays_lf(monkeypatch):
    monkeypatch.setattr(json_file.os, "linesep", "\r\n")
    assert b"\r\n" in json_file.json_bytes(PAYLOAD, newline=None)
    assert b"\r" not in json_file.json_bytes(PAYLOAD)


def test_nonprivate_stage_uses_binary_and_nofollow_flags(tmp_path, monkeypatch):
    # Exercise the Windows CRT flag even on POSIX, where O_BINARY is absent.
    binary = getattr(os, "O_BINARY", 0x8000)
    real_open = os.open
    seen = []
    monkeypatch.setattr(os, "O_BINARY", binary, raising=False)

    def open_stage(path, flags, mode=0o777, *args, **kwargs):
        seen.append(flags)
        # Strip the synthetic flag only on hosts without a Windows CRT.
        return real_open(path, flags if os.name == "nt" else flags & ~binary, mode, *args, **kwargs)

    monkeypatch.setattr(json_file.os, "open", open_stage)
    path = tmp_path / "record.json"
    raw = b'{"label": "r\\u00e4v"}\r\n'
    json_file.write_json_atomic(path, raw, creation_mode=0o666)
    assert len(seen) == 1
    assert seen[0] & binary
    assert seen[0] & getattr(os, "O_NOFOLLOW", 0) == getattr(os, "O_NOFOLLOW", 0)
    assert path.read_bytes() == raw


def test_release_cache_bytes_and_failed_replace(tmp_path, monkeypatch):
    from server.updates.installer_client import InstallerClient

    modes = observe_creation_modes(monkeypatch)
    client = InstallerClient.__new__(InstallerClient)
    client.data_dir = tmp_path
    client._channel, client._release, client._proof = "stable", {"name": "räv"}, None
    client._checked, client._responses = 123, {}
    payload = {"schemaVersion": 1, "channel": client._channel, "release": client._release,
               "proof": None, "checked": 123, "responses": {}}
    client._save_cache()
    path = client._cache_path
    expected = legacy_bytes(payload, trailing_newline=False)
    assert path.read_bytes() == expected
    assert modes and set(modes.values()) == {0o666}
    monkeypatch.setattr(json_file.os, "replace", lambda *_: (_ for _ in ()).throw(PermissionError("locked")))
    client._checked = 456
    with pytest.raises(PermissionError):
        client._save_cache()
    assert path.read_bytes() == expected
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.skipif(os.name == "nt", reason="POSIX creation permissions")
@pytest.mark.parametrize("mode", [0o600, 0o666])
def test_creation_permissions_match_legacy_open(tmp_path, mode):
    import stat

    reference = tmp_path / "reference"
    fd = os.open(reference, os.O_CREAT | os.O_WRONLY | os.O_EXCL, mode)
    os.close(fd)
    path = tmp_path / "record.json"
    json_file.write_json_atomic(path, PAYLOAD, creation_mode=mode)
    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(reference.stat().st_mode)


@pytest.mark.parametrize("viewport", [False, True])
def test_cache_stage_failure_changes_neither_file(tmp_path, monkeypatch, viewport):
    from server.cadlink.intake import ingest

    mesh, metadata = tmp_path / "mesh.msh", tmp_path / "mesh.json"
    mesh.write_bytes(b'old mesh')
    metadata.write_bytes(b'old metadata')
    real_open = os.open
    def fail_stage(path, *args, **kwargs):
        if Path(path).name.startswith(".mesh.json."):
            raise OSError("cannot stage metadata")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(json_file.os, "open", fail_stage)
    content = {"msh_text": "mesh\n", "stats": {}, "metadata": {}}
    with pytest.raises(OSError, match="cannot stage metadata"):
        if viewport:
            ingest._write_viewport_cache(mesh, metadata, content,
                                         transformed_geometry_hash="geometry", healing={})
        else:
            ingest._write_cache(mesh, metadata, content)
    assert mesh.read_bytes() == b'old mesh'
    assert metadata.read_bytes() == b'old metadata'
    assert set(tmp_path.iterdir()) == {mesh, metadata}


@pytest.mark.parametrize("compensation_raises", [False, True])
def test_failed_compensation_retains_stage_and_original_error(tmp_path, monkeypatch, compensation_raises):
    path = tmp_path / "record.json"
    path.write_bytes(b"old")
    original = PermissionError("publication failed")
    compensation = RuntimeError("compensation failed")

    def fail(*_):
        raise original

    def compensate(error):
        assert error is original
        if compensation_raises:
            raise compensation
        return True

    monkeypatch.setattr(json_file.os, "replace", fail)
    with pytest.raises(RuntimeError if compensation_raises else PermissionError) as caught:
        json_file.write_json_atomic(path, PAYLOAD, replace_failed=compensate)
    assert caught.value is (compensation if compensation_raises else original)
    if compensation_raises:
        assert caught.value.__cause__ is original
    assert path.read_bytes() == b"old"
    stages = list(tmp_path.glob(".record.json.*"))
    assert len(stages) == 1
    assert stages[0].read_bytes() == json_file.json_bytes(PAYLOAD)


@pytest.mark.parametrize("name,attempts,delays", [
    ("fusion", 10, [0.02] * 9),
    ("beat-registry", 4, [0.01] * 3),
    ("beat-state", 5, [0.025, 0.05, 0.075, 0.1]),
])
@pytest.mark.parametrize("exhausted", [False, True])
def test_callers_windows_replace_retry_budget(tmp_path, monkeypatch, name, attempts, delays, exhausted):
    from types import SimpleNamespace
    from server.solver.beat_runtime import registry, state

    # Patch the registry's platform view without changing pathlib's host OS.
    monkeypatch.setattr(registry, "os", SimpleNamespace(**(vars(os) | {"name": "nt"})))
    monkeypatch.setattr(registry, "private_directory", lambda path: path.mkdir(exist_ok=True))
    monkeypatch.setattr(registry, "_check_path", lambda *_: None)
    monkeypatch.setattr(state, "_WINDOWS", True)
    write, expected, _ = caller(name)
    path = tmp_path / "record.json"
    path.write_bytes(b"old")
    real_replace = os.replace
    calls, sleeps = [], []

    def replace(source, target):
        calls.append(source)
        if exhausted or len(calls) < attempts:
            raise PermissionError("reader holds file")
        real_replace(source, target)

    monkeypatch.setattr(json_file.os, "replace", replace)
    monkeypatch.setattr(json_file.time, "sleep", sleeps.append)
    if exhausted:
        with pytest.raises(PermissionError):
            write(path, PAYLOAD)
    else:
        write(path, PAYLOAD)
    assert len(calls) == attempts
    assert len(set(calls)) == 1
    assert sleeps == delays
    assert path.read_bytes() == (b"old" if exhausted else expected)
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("cleanup_failure", [None, PermissionError, ValueError])
def test_registry_cleanup_checks_root_retries_and_preserves_write_error(tmp_path, monkeypatch, cleanup_failure):
    from types import SimpleNamespace
    from server.solver.beat_runtime import registry

    monkeypatch.setattr(registry, "os", SimpleNamespace(**(vars(os) | {"name": "nt"})))
    monkeypatch.setattr(registry, "private_directory", lambda path: path.mkdir(exist_ok=True))
    monkeypatch.setattr(registry, "_check_path", lambda *_: None)
    roots, unlinks, delays = [], [], []

    def check_root(root):
        roots.append(root)
        if cleanup_failure is ValueError:
            raise ValueError("unsafe root")
        return root

    original = OSError("write failed")
    def fail_replace(*_):
        raise original

    real_unlink = Path.unlink
    def unlink(path, **kwargs):
        assert roots == [tmp_path]
        unlinks.append(path)
        if cleanup_failure is PermissionError or len(unlinks) < 4:
            raise PermissionError("scanner holds stage")
        return real_unlink(path, **kwargs)

    monkeypatch.setattr(registry.paths, "checked_root", check_root)
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(json_file.os, "replace", fail_replace)
    monkeypatch.setattr(registry.time, "sleep", delays.append)
    with pytest.raises(OSError) as caught:
        registry._atomic_json(tmp_path / "record.json", PAYLOAD)
    assert caught.value is original
    assert roots == [tmp_path]
    assert len(unlinks) == (0 if cleanup_failure is ValueError else 4)
    assert delays == ([] if cleanup_failure is ValueError else [0.01] * 3)
    assert bool(list(tmp_path.glob(".record.json.*.tmp"))) is (cleanup_failure is not None)


@pytest.mark.parametrize("viewport", [False, True])
def test_cache_sweeps_old_json_stages_but_keeps_recent_and_unrelated_files(tmp_path, monkeypatch, viewport):
    from server.cadlink.intake import ingest

    stale = tmp_path / ".mesh.json.abandoned"
    stale.write_bytes(b"partial")
    old = ingest.time.time() - ingest._ABANDONED_STAGING_AGE - 60
    os.utime(stale, (old, old))
    recent = tmp_path / ".mesh.json.active"
    recent.write_bytes(b"active")
    unrelated = tmp_path / ".other.txt.abandoned"
    unrelated.write_bytes(b"other")
    os.utime(unrelated, (old, old))
    stage = tmp_path / ".wg2-import-mesh-abandoned"
    stage.mkdir()
    (stage / "mesh.msh").write_bytes(b"partial")
    os.utime(stage, (old, old))
    content = {"msh_text": "mesh\n", "stats": {}, "metadata": {}}
    if viewport:
        ingest._write_viewport_cache(tmp_path / "mesh.msh", tmp_path / "mesh.json", content,
                                     transformed_geometry_hash="geometry", healing={})
    else:
        ingest._write_cache(tmp_path / "mesh.msh", tmp_path / "mesh.json", content)
    assert not stale.exists() and not stage.exists()
    assert recent.read_bytes() == b"active"
    assert unrelated.read_bytes() == b"other"


def test_existing_parent_policy_does_not_recreate_a_deleted_private_directory(tmp_path):
    path = tmp_path / "gone" / "record.json"
    with pytest.raises(FileNotFoundError):
        json_file.write_json_atomic(path, PAYLOAD, create_parent=False)
    assert not path.parent.exists()


def test_presets_library_stage_failure_changes_neither_record(tmp_path, monkeypatch):
    from server.source_editor import PresetStore

    store = PresetStore(tmp_path / "presets.json")
    store.path.write_bytes(b"old library")
    store.authoring_path.write_bytes(b"old authoring")
    real_open = os.open

    def open_stage(path, *args, **kwargs):
        if Path(path).name.startswith(".presets.json."):
            raise OSError("cannot stage library")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(json_file.os, "open", open_stage)
    with pytest.raises(HTTPException) as caught:
        store._write({"z": {"revision": "r"}}, {"z": {"revision": "r", "authoring": {}}})
    assert caught.value.status_code == 503
    assert isinstance(caught.value.__cause__, OSError)
    assert store.path.read_bytes() == b"old library"
    assert store.authoring_path.read_bytes() == b"old authoring"
    assert set(tmp_path.iterdir()) == {store.path, store.authoring_path}
