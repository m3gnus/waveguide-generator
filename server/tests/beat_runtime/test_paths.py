from pathlib import Path
import os
from types import SimpleNamespace

import pytest

from server.solver.beat_runtime import paths


@pytest.fixture(autouse=True)
def simulated_user(monkeypatch):
    # Platform arguments exercise POSIX layouts even on Windows. Supply the
    # simulated user's uid instead of calling a host-only POSIX API.
    monkeypatch.setattr(paths, "os", SimpleNamespace(**{**vars(os), "getuid": lambda: 123}))


@pytest.mark.parametrize(("system", "env", "expected"), [
    ("darwin", {}, "/home/test/Library/Application Support"),
    ("win32", {"LOCALAPPDATA": "/local"}, "/local"),
    ("win32", {}, "/home/test/AppData/Local"),
    ("linux", {"XDG_DATA_HOME": "/data"}, "/data"),
    ("linux", {}, "/home/test/.local/share"),
])
def test_runtime_os_layouts(system, env, expected):
    assert paths.runtime_dir(system=system, environ=env, home=Path("/home/test")) == (
        Path(expected) / ("WaveguideGenerator/beat" if system == "win32" else "WaveguideGenerator/beat-runtime/wg-beat-engine")
    )


@pytest.mark.parametrize(("system", "env", "expected"), [
    ("darwin", {}, "/temp/wg-beat-engine-123"),
    ("linux", {}, "/temp/wg-beat-engine-123"),
    ("linux", {"XDG_RUNTIME_DIR": "/run/user/123"}, "/run/user/123/wg-beat-engine"),
    ("darwin", {"XDG_RUNTIME_DIR": "/run"}, "/run/wg-beat-engine"),
    ("win32", {"LOCALAPPDATA": "/local"}, "/local/WaveguideGenerator/beat-workers/wg-beat-engine"),
])
def test_worker_os_layouts(system, env, expected):
    assert paths.worker_dir(system=system, environ=env, temp_dir=Path("/temp"), uid=123) == Path(expected)


@pytest.mark.parametrize("system", ["darwin", "win32", "linux"])
def test_overrides_are_bases_and_do_not_create_directories(tmp_path, system):
    env = {paths.RUNTIME_DIR_ENV: str(tmp_path / "r"), paths.WORKER_DIR_ENV: str(tmp_path / "s")}
    assert paths.runtime_dir(system=system, environ=env) == tmp_path / "r/wg-beat-engine"
    assert paths.worker_dir(system=system, environ=env) == tmp_path / "s/wg-beat-engine"
    assert list(tmp_path.iterdir()) == []


def test_hbb_roots_are_ignored_and_untouched(tmp_path):
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    record = legacy / "state-cpu.json"
    record.write_text('{"status":"ready"}')
    env = {"HORNLAB_BEAT_RUNTIME_DIR": str(legacy), "HORNLAB_BEAT_WORKER_DIR": str(legacy)}
    args = dict(environ=env, system="linux", home=tmp_path)
    assert paths.runtime_dir(**args) == tmp_path / ".local/share/WaveguideGenerator/beat-runtime/wg-beat-engine"
    assert paths.worker_dir(**args, temp_dir=tmp_path, uid=123) == tmp_path / "wg-beat-engine-123"
    assert record.read_text() == '{"status":"ready"}'
    assert list(legacy.iterdir()) == [record]
    assert (paths.PROVIDER_ID, paths.HOST_PROTOCOL, paths.HOST_PROTOCOL_VERSION, paths.STATE_SCHEMA) == (
        "wg-beat-engine", "wg-beat-host", 2, 1,
    )


def test_separate_hbb_override_is_allowed(tmp_path):
    env = {paths.WORKER_DIR_ENV: str(tmp_path / "wg"), "HORNLAB_BEAT_WORKER_DIR": str(tmp_path / "hbb")}
    assert paths.worker_dir(environ=env, system="linux", home=tmp_path) == tmp_path / "wg" / paths.PROVIDER_ID


@pytest.mark.parametrize("system", ["linux", "darwin", "win32"])
def test_default_roots_never_overlap_hbb_defaults(tmp_path, system):
    env = {"LOCALAPPDATA": str(tmp_path / "local")} if system == "win32" else {}
    paths.runtime_dir(environ=env, system=system, home=tmp_path)
    paths.worker_dir(environ=env, system=system, home=tmp_path, temp_dir=tmp_path, uid=7)


def test_sibling_of_hbb_default_root_is_allowed(tmp_path):
    env = {"XDG_DATA_HOME": str(tmp_path / "data"), paths.RUNTIME_DIR_ENV: str(tmp_path / "data")}
    root = paths.runtime_dir(environ=env, system="linux", home=tmp_path)
    assert root == tmp_path / "data" / paths.PROVIDER_ID


def test_is_link_with_file_ancestor_treats_not_a_directory_as_absent(tmp_path):
    regular = tmp_path / "file"
    regular.write_bytes(b"regular")
    assert paths.is_link(regular / "child") is False
