from pathlib import Path

import pytest

from server.solver.beat_runtime import paths


@pytest.mark.parametrize(("system", "env", "expected"), [
    ("darwin", {}, "/home/test/Library/Application Support"),
    ("win32", {"LOCALAPPDATA": "/local"}, "/local"),
    ("win32", {}, "/home/test/AppData/Local"),
    ("linux", {"XDG_DATA_HOME": "/data"}, "/data"),
    ("linux", {}, "/home/test/.local/share"),
])
def test_runtime_os_layouts(system, env, expected):
    assert paths.runtime_dir(system=system, environ=env, home=Path("/home/test")) == (
        Path(expected) / "WaveguideGenerator/beat-runtime/wg-beat-engine"
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
        "wg-beat-engine", "wg-beat-host", 1, 1,
    )


@pytest.mark.parametrize("hbb_env", ["HORNLAB_BEAT_RUNTIME_DIR", "HORNLAB_BEAT_WORKER_DIR"])
@pytest.mark.parametrize("relation", ["same", "inside", "contains"])
def test_overrides_that_overlap_hbb_roots_are_refused(tmp_path, hbb_env, relation):
    shared = tmp_path / "shared"
    hbb = {
        "same": shared / paths.PROVIDER_ID,
        "inside": shared,
        "contains": shared / paths.PROVIDER_ID / "hbb",
    }[relation]
    env = {paths.RUNTIME_DIR_ENV: str(shared), paths.WORKER_DIR_ENV: str(shared), hbb_env: str(hbb)}
    with pytest.raises(paths.RootConflict):
        paths.runtime_dir(environ=env, system="linux", home=tmp_path)
    with pytest.raises(paths.RootConflict):
        paths.worker_dir(environ=env, system="linux", home=tmp_path, temp_dir=tmp_path, uid=1)


def test_symlinked_hbb_override_alias_is_refused(tmp_path):
    real = tmp_path / "real"
    (real / paths.PROVIDER_ID).mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    env = {paths.WORKER_DIR_ENV: str(real), "HORNLAB_BEAT_WORKER_DIR": str(alias / paths.PROVIDER_ID)}
    with pytest.raises(paths.RootConflict):
        paths.worker_dir(environ=env, system="linux", home=tmp_path, temp_dir=tmp_path, uid=1)


def test_separate_hbb_override_is_allowed(tmp_path):
    env = {paths.WORKER_DIR_ENV: str(tmp_path / "wg"), "HORNLAB_BEAT_WORKER_DIR": str(tmp_path / "hbb")}
    assert paths.worker_dir(environ=env, system="linux", home=tmp_path) == tmp_path / "wg" / paths.PROVIDER_ID
