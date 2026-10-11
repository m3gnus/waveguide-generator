"""Migration and rollback use the same mirrors as released WGLink versions."""
from __future__ import annotations

import asyncio
import ctypes
import importlib.util
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from server.platform import paths
from server.workspace import api
from server.workspace.folders import FolderRoot, NAMES, read_settings


def write(data, file, payload):
    api._write_json_atomic(data / file, payload)


def setup(tmp_path, *, isolated=False, defaults=None):
    data = tmp_path / 'data'
    root = paths.default_folder_root(data, isolated=isolated, system='Linux', environ={}, home=tmp_path)
    app = FastAPI()
    app.state.data_dir = data
    api.mount_workspace(app, default_root=root, default_path=defaults or data / 'workspace')
    return app, root


def restart(data, root, writer=api._write_json_atomic, **kwargs):
    return FolderRoot(data, default_root=root, legacy_runs=data / 'workspace', writer=writer, **kwargs)


def endpoint(app, route):
    return next(item.endpoint for item in app.routes if item.path == route)


@pytest.mark.parametrize('isolated', [False, True])
def test_fresh_install(tmp_path, isolated):
    app, root = setup(tmp_path, isolated=isolated)
    f = app.state.folders
    assert f.root == root
    assert f.overrides == {}
    assert app.state.workspace.path() == root / 'runs'
    assert app.state.cad_workspace.path() == root / 'cadlink'
    assert read_settings(f.settings_path) == {'schemaVersion': 1, 'rootPath': str(root)}
    assert read_settings(f.data_dir / 'workspace_settings.json')['workspacePath'] == str(root / 'runs')
    assert read_settings(f.data_dir / 'cadlink_settings.json')['cadLinkPath'] == str(root / 'cadlink')


@pytest.mark.parametrize('cad_evidence', [False, True])
def test_legacy_only_settings(tmp_path, cad_evidence):
    selected = tmp_path / 'old'
    selected.mkdir()
    if cad_evidence:
        (selected / 'wglink').mkdir()
    write(tmp_path / 'data', 'workspace_settings.json', {'schemaVersion': 1, 'workspacePath': str(selected)})
    app, root = setup(tmp_path)
    assert app.state.folders.overrides == {'runs': selected, **({'cadlink': selected} if cad_evidence else {})}
    assert app.state.folders.effective('cadlink') == (selected if cad_evidence else root / 'cadlink')
    assert selected.is_dir()


def test_populated_legacy_default_is_adopted_first(tmp_path):
    data, legacy = tmp_path / 'data', tmp_path / 'output'
    (legacy / 'previous-run').mkdir(parents=True)
    (legacy / 'wgreturn').mkdir()
    app = FastAPI()
    app.state.data_dir = data
    api.mount_workspace(app, default_root=tmp_path / 'root', default_path=tmp_path / 'root' / 'runs', legacy_defaults=(legacy,))
    assert app.state.folders.overrides == {'runs': legacy, 'cadlink': legacy}
    assert (legacy / 'previous-run').is_dir()


@pytest.mark.parametrize('exists', [False, True])
def test_both_under_one_root_including_missing_folders(tmp_path, exists):
    root, data = tmp_path / 'chosen', tmp_path / 'data'
    for name, key, file in [('runs', 'workspacePath', 'workspace_settings.json'), ('cadlink', 'cadLinkPath', 'cadlink_settings.json')]:
        if exists:
            (root / name).mkdir(parents=True)
        write(data, file, {key: str(root / name)})
    app, _ = setup(tmp_path)
    assert app.state.folders.root == root
    assert not app.state.folders.overrides


def test_diverged_missing_folder_and_capture_keys_are_preserved(tmp_path):
    data, runs, cad = tmp_path / 'data', tmp_path / 'missing', tmp_path / 'cad'
    cad.mkdir()
    (cad / 'sentinel').write_text('keep')
    write(data, 'workspace_settings.json', {'workspacePath': str(runs)})
    capture = {'cadLinkPath': str(cad), 'captureDocument': False, 'captureMode': 'off', 'extra': 'keep'}
    write(data, 'cadlink_settings.json', capture)
    app, root = setup(tmp_path)
    assert app.state.folders.overrides == {'runs': runs, 'cadlink': cad}
    assert not runs.exists()
    with pytest.raises(api.WorkspaceUnavailableError):
        app.state.workspace.path()
    assert read_settings(data / 'cadlink_settings.json') == {**capture, 'schemaVersion': 1}
    other = tmp_path / 'other'
    other.mkdir()
    app.state.folders.select(other)
    assert app.state.folders.root == other
    assert app.state.folders.overrides == {'runs': runs, 'cadlink': cad}
    assert (cad / 'sentinel').read_text() == 'keep'
    assert not runs.exists()


@pytest.mark.parametrize('content', ['{', '[]', '{"schemaVersion":1,"rootPath":42}', '{"schemaVersion":1,"rootPath":"relative"}'])
def test_broken_json_uses_mirrors(tmp_path, content):
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'folders.json').write_text(content)
    chosen = tmp_path / 'chosen'
    chosen.mkdir()
    write(data, 'workspace_settings.json', {'workspacePath': str(chosen)})
    f = restart(data, tmp_path / 'default')
    assert f.effective('runs') == chosen
    assert read_settings(f.settings_path)['schemaVersion'] == 1


def test_unknown_schema_never_overwritten(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    future = b'{"schemaVersion":2,"rootPath":"future","newKey":true}'
    (data / 'folders.json').write_bytes(future)
    runs, cad = tmp_path / 'runs', tmp_path / 'cad'
    runs.mkdir()
    cad.mkdir()
    write(data, 'workspace_settings.json', {'workspacePath': str(runs)})
    write(data, 'cadlink_settings.json', {'cadLinkPath': str(cad), 'captureMode': 'project', 'captureDocument': True})
    f = restart(data, tmp_path / 'default')
    assert f.effective('runs') == runs and f.effective('cadlink') == cad
    assert f.read_only
    with pytest.raises(ValueError, match='newer version'):
        f.select(tmp_path)
    assert (data / 'folders.json').read_bytes() == future


@pytest.mark.parametrize('fail_at', ['cadlink_settings.json', 'folders.json'])
def test_crash_between_mirrors_and_config_is_idempotent(tmp_path, fail_at):
    data, root = tmp_path / 'data', tmp_path / 'root'
    def crash(path, value):
        if path.name == fail_at:
            raise OSError('simulated interruption')
        api._write_json_atomic(path, value)
    with pytest.raises(OSError):
        restart(data, root, crash)
    f = restart(data, root)
    before = {p.name: p.read_bytes() for p in data.glob('*.json')}
    restart(data, root, lambda *_: pytest.fail('An idempotent start must not rewrite settings'))
    assert before == {p.name: p.read_bytes() for p in data.glob('*.json')}
    assert f.effective('runs') == root / 'runs'
    assert f.effective('cadlink') == root / 'cadlink'


def test_rollback_drift_adopted_and_logged_once(tmp_path, caplog):
    app, root = setup(tmp_path)
    data = app.state.folders.data_dir
    selected = tmp_path / 'older-choice'
    selected.mkdir()
    write(data, 'cadlink_settings.json', {'cadLinkPath': str(selected), 'captureDocument': False})
    f = restart(data, root)
    assert f.overrides == {'cadlink': selected}
    assert f.notice and len([r for r in caplog.records if 'earlier WG version' in r.message]) == 1
    assert restart(data, root).notice is None
    assert len([r for r in caplog.records if 'earlier WG version' in r.message]) == 1
    assert read_settings(data / 'cadlink_settings.json')['captureDocument'] is False


def addin_workspace_module():
    # Read the sibling add-in checkout without modifying or copying its code.
    checkout = Path(__file__).resolve().parents[3] / 'addin' / 'fusion-addins' / 'WGLink' / 'wglink_workspace.py'
    if not checkout.is_file():
        pytest.skip('Sibling add-in checkout is absent; WGLink mirror interoperability cannot be checked')
    spec = importlib.util.spec_from_file_location('f1_wglink_workspace', checkout)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_addin_reads_mirrors_before_and_after_root_change_and_rollback(tmp_path):
    module = addin_workspace_module()
    app, root = setup(tmp_path)
    data = app.state.folders.data_dir
    hooks = {'system': 'Linux', 'environ': {'WG2_DATA_DIR': str(data)}, 'home': tmp_path}
    assert module.workspace_root(**hooks) == root / 'cadlink'
    previous = {name: read_settings(data / file)[key] for name, (file, key) in NAMES.items()}
    new = tmp_path / 'new'
    new.mkdir()
    app.state.folders.select(new)
    assert module.workspace_root(**hooks) == new / 'cadlink'
    assert read_settings(data / 'workspace_settings.json')['workspacePath'] == str(new / 'runs')
    # Older WG only changes its mirrors: no knowledge of folders.json needed.
    write(data, 'workspace_settings.json', {'workspacePath': previous['runs']})
    write(data, 'cadlink_settings.json', {'cadLinkPath': previous['cadlink']})
    restart(data, root)
    assert module.workspace_root(**hooks) == root / 'cadlink'
    assert (new / 'runs').is_dir() and (root / 'runs').is_dir()


def test_root_change_rewrites_only_unoverridden_mirror_and_preserves_spelling(tmp_path):
    app, old = setup(tmp_path)
    data = app.state.folders.data_dir
    capture = {'captureDocument': False, 'captureMode': 'off'}
    write(data, 'cadlink_settings.json', {'cadLinkPath': str(old / 'cadlink'), **capture})
    chosen = tmp_path / 'chosen'
    chosen.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(chosen, target_is_directory=True)
    app.state.folders.select(alias)
    assert read_settings(data / 'workspace_settings.json')['workspacePath'] == str(alias / 'runs')
    assert read_settings(data / 'cadlink_settings.json') == {'schemaVersion': 1, 'cadLinkPath': str(alias / 'cadlink'), **capture}
    assert app.state.workspace.path() == alias / 'runs'
    assert app.state.cad_workspace.path() == alias / 'cadlink'


def test_root_and_cad_pickers_open_at_current_choice(tmp_path, monkeypatch):
    app, root = setup(tmp_path)
    seen = []
    monkeypatch.setattr(api, '_select_workspace_folder', lambda prompt, start: seen.append((prompt, start)))
    asyncio.run(endpoint(app, '/api/folders/select')())
    asyncio.run(endpoint(app, '/api/cad-workspace/select')())
    assert seen == [('Select WG root folder', root), ('Select WGLink folder', root / 'cadlink')]


def test_windows_documents_ctypes_and_fallback(tmp_path, monkeypatch):
    allocated = ctypes.create_unicode_buffer(str(tmp_path / 'OneDrive' / 'Documents'))
    freed = []
    def known(guid, flags, token, output):
        output._obj.value = ctypes.addressof(allocated)
        assert bytes(guid._obj) == uuid.UUID('fdd39ad0-238f-46af-adb4-6c85480369c7').bytes_le
        return 0
    def free(pointer): freed.append(pointer.value)
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(shell32=SimpleNamespace(SHGetKnownFolderPath=known), ole32=SimpleNamespace(CoTaskMemFree=free)), raising=False)
    assert paths.documents_root(system='Windows', environ={'USERPROFILE': str(tmp_path)}, home=tmp_path) == tmp_path / 'OneDrive' / 'Documents' / 'Waveguide Generator'
    assert freed == [ctypes.addressof(allocated)]
    monkeypatch.setattr(paths, 'windows_documents_dir', lambda: None)
    assert paths.documents_root(system='Windows', environ={'USERPROFILE': str(tmp_path)}, home='/unused') == tmp_path / 'Documents' / 'Waveguide Generator'


def test_windows_case_insensitive_comparison_and_existing_alias(tmp_path):
    assert paths.same_folder(r'C:\Users\Example\Root', r'c:\users\example\ROOT' + '\\', system='Windows')
    assert not paths.same_folder(r'C:\Root', r'C:\Different', system='Windows')
    target = tmp_path / 'target'
    target.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(target, target_is_directory=True)
    assert paths.same_folder(target, alias)


def test_windows_root_refusal_leaves_settings_untouched(tmp_path):
    data, root = tmp_path / 'data', tmp_path / 'root'
    f = restart(data, root, system='Windows')
    before = {p.name: p.read_bytes() for p in data.glob('*.json')}
    with pytest.raises(ValueError, match='too long for Windows'):
        f.select(Path('C:/' + 'long' * 50))
    assert before == {p.name: p.read_bytes() for p in data.glob('*.json')}
    paths.validate_folder_root(r'C:\WG', system='Windows')


@pytest.mark.parametrize('fail_at', ['cadlink_settings.json', 'folders.json'])
def test_interrupted_root_change_preserves_every_written_choice(tmp_path, fail_at):
    app, root = setup(tmp_path)
    folders = app.state.folders
    new = tmp_path / 'new'
    new.mkdir()
    def crash(path, value):
        if path.name == fail_at:
            raise OSError('interrupted root choice')
        api._write_json_atomic(path, value)
    folders.writer = crash
    with pytest.raises(OSError):
        folders.select(new)
    assert app.state.workspace.path() == new / 'runs'
    assert app.state.cad_workspace.path() == (root / 'cadlink' if fail_at == 'cadlink_settings.json' else new / 'cadlink')
    recovered = restart(folders.data_dir, root)
    assert recovered.effective('runs') == new / 'runs'
    assert recovered.effective('cadlink') == (root / 'cadlink' if fail_at == 'cadlink_settings.json' else new / 'cadlink')
    assert (root / 'runs').is_dir() and (new / 'runs').is_dir()
    restart(folders.data_dir, root, lambda *_: pytest.fail('Recovery must be idempotent'))


def test_exports_and_internal_data_are_untouched(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    write(data, 'export_settings.json', {'lastExportPath': '/separate/export'})
    internal = data / 'ipc' / 'wglink'
    internal.mkdir(parents=True)
    sentinel = internal / 'sentinel'
    sentinel.write_bytes(b'keep')
    before = (data / 'export_settings.json').read_bytes()
    app, root = setup(tmp_path, isolated=True)
    app.state.folders.select(tmp_path)
    assert (data / 'export_settings.json').read_bytes() == before
    assert sentinel.read_bytes() == b'keep'


def test_mirror_publication_order_is_config_last(tmp_path):
    order = []
    def record(path, payload):
        order.append(path.name)
        api._write_json_atomic(path, payload)
    restart(tmp_path / 'data', tmp_path / 'root', record)
    assert order == ['workspace_settings.json', 'cadlink_settings.json', 'folders.json']


def test_windows_case_variants_share_root_without_overrides(tmp_path):
    data = tmp_path / 'data'
    root = tmp_path / 'ROOT'
    write(data, 'workspace_settings.json', {'workspacePath': str(root / 'RUNS')})
    write(data, 'cadlink_settings.json', {'cadLinkPath': str(tmp_path / 'root' / 'CADLINK')})
    f = restart(data, tmp_path / 'default', system='Windows')
    assert f.root == root
    assert f.overrides == {}


def test_unavailable_root_does_not_crash_or_repoint_on_start(tmp_path, monkeypatch):
    data, root = tmp_path / 'data', tmp_path / 'offline'
    write(data, 'folders.json', {'schemaVersion': 1, 'rootPath': str(root)})
    real_mkdir = Path.mkdir
    def unavailable(path, *args, **kwargs):
        if path.is_relative_to(root):
            raise OSError('drive disconnected')
        return real_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', unavailable)
    f = restart(data, tmp_path / 'default')
    assert f.root == root and not f.overrides
    assert read_settings(data / 'workspace_settings.json')['workspacePath'] == str(root / 'runs')


def test_windows_deep_run_export_refused_before_any_files_are_written(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.platform, 'system', lambda: 'Windows')
    runs = tmp_path / 'runs'
    runs.mkdir()
    with pytest.raises(api.HTTPException, match='too long for Windows') as caught:
        api._write_export_sync(runs, 'project/' + 'run' * 45 + '/exports', 'reject', [('result.csv', b'result')])
    assert caught.value.status_code == 422
    assert list(runs.iterdir()) == []
