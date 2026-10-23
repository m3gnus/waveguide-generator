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
from server.workspace.folders import FolderRoot, NAMES, SettingsRead, read_settings


@pytest.fixture(autouse=True)
def deterministic_platform(monkeypatch):
    # Root headroom tests opt into Windows explicitly. Temporary directories
    # on Windows CI often exceed the chooser budget before a test adds names.
    monkeypatch.setattr(paths.platform, 'system', lambda: 'Linux')


def write(data, file, payload):
    api._write_json_atomic(data / file, payload)


def setup(tmp_path, *, isolated=False, defaults=None):
    data = tmp_path / 'data'
    root = paths.default_folder_root(data, isolated=isolated, system='Linux', environ={}, home=tmp_path)
    app = FastAPI()
    app.state.data_dir = data
    api.mount_workspace(app, default_root=root, default_path=defaults or (data / 'workspace' if isolated else root / 'runs'),
                        **({'create_implicit_runs': True} if isolated else {}))
    return app, root


def restart(data, root, writer=api._write_json_atomic, **kwargs):
    return FolderRoot(data, default_root=root, legacy_runs=root / 'runs', writer=writer, **kwargs)


def endpoint(app, route):
    return next(item.endpoint for item in app.routes if item.path == route)


@pytest.mark.parametrize('isolated', [False, True])
def test_fresh_install(tmp_path, isolated):
    app, root = setup(tmp_path, isolated=isolated)
    f = app.state.folders
    runs = f.data_dir / 'workspace' if isolated else root / 'runs'
    assert f.root == root
    assert f.overrides == ({'runs': runs} if isolated else {})
    assert app.state.workspace.path() == runs
    if isolated:
        assert app.state.cad_workspace.selected_path() is None
        assert not (root / 'cadlink').exists()
    else:
        assert app.state.cad_workspace.path() == root / 'cadlink'
    assert read_settings(f.settings_path)['rootPath'] == str(root)
    assert read_settings(f.data_dir / 'workspace_settings.json')['workspacePath'] == str(app.state.workspace.path())
    assert read_settings(f.data_dir / 'cadlink_settings.json').get('cadLinkPath') == (None if isolated else str(root / 'cadlink'))


@pytest.mark.parametrize('isolated', [False, True])
def test_implicit_runs_mirror_never_becomes_a_selection_on_restart(tmp_path, isolated):
    app, root = setup(tmp_path, isolated=isolated)
    runs = app.state.workspace.path()
    expected = {'path': str(runs), 'selected': False}
    for _ in range(3):
        assert asyncio.run(endpoint(app, '/api/workspace/path')()) == expected
        assert app.state.workspace.selected_path() is None
        mirror = read_settings(app.state.workspace.settings_path)
        assert mirror['workspacePath'] == str(runs)
        assert mirror['workspacePathSelected'] is False
        before = {p.name: p.read_bytes() for p in app.state.folders.data_dir.glob('*.json')}
        app, _ = setup(tmp_path, isolated=isolated)
        assert {p.name: p.read_bytes() for p in app.state.folders.data_dir.glob('*.json')} == before
    # Released WG reads workspacePath and ignores additional mirror keys.
    rollback = api.WorkspaceState(app.state.folders.data_dir, default_path=root / 'unused', allow_adoption=False)
    assert rollback.path() == runs


@pytest.mark.parametrize('choice', ['root', 'runs', 'older-same', 'older-different'])
def test_explicit_choice_replaces_implicit_runs_marker(tmp_path, choice):
    app, root = setup(tmp_path)
    runs = app.state.workspace.path()
    sentinel = runs / 'sentinel'
    sentinel.write_text('keep')
    if choice == 'root':
        app.state.folders.select(root)
    elif choice == 'runs':
        app.state.workspace.select(runs)
    else:
        if choice == 'older-different':
            runs = tmp_path / 'older-runs'
            runs.mkdir()
        # This is the exact mirror shape written by 0.3.5's chooser, including
        # re-selecting the unchanged implicit path after a rollback.
        write(app.state.folders.data_dir, 'workspace_settings.json', {'schemaVersion': 1, 'workspacePath': str(runs)})
    app, _ = setup(tmp_path)
    assert asyncio.run(endpoint(app, '/api/workspace/path')()) == {'path': str(runs), 'selected': True}
    assert read_settings(app.state.workspace.settings_path).get('workspacePathSelected') is not False
    assert sentinel.read_text() == 'keep'


def test_explicit_workspace_dir_is_selected_across_restarts(tmp_path):
    from server.app import create_app

    data, runs = tmp_path / 'data', tmp_path / 'explicit-runs'
    app = create_app(data_dir=data, workspace_dir=runs, solver_warmup=False)
    sentinel = runs / 'sentinel'
    sentinel.write_text('keep')
    for _ in range(2):
        assert asyncio.run(endpoint(app, '/api/workspace/path')()) == {'path': str(runs), 'selected': True}
        app = create_app(data_dir=data, solver_warmup=False)
    assert sentinel.read_text() == 'keep'


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
    assert app.state.workspace.selected_path() == legacy
    assert read_settings(data / 'workspace_settings.json').get('workspacePathSelected') is not False


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
    assert (root / 'runs').exists() == exists
    assert (root / 'cadlink').exists() == exists


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
    if content in ('{', '[]'):
        assert not f.read_only
        assert f.settings_path.read_text() == content
    else:
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


@pytest.mark.parametrize('capture', [None, {'captureDocument': False, 'captureMode': 'off', 'extra': 'keep'}])
def test_upgrade_without_cad_choice_does_not_publish_missing_cad_mirror(tmp_path, caplog, capture):
    module = addin_workspace_module()
    data, runs = tmp_path / 'data', tmp_path / 'chosen-runs'
    runs.mkdir()
    write(data, 'workspace_settings.json', {'workspacePath': str(runs)})
    if capture is not None:
        write(data, 'cadlink_settings.json', capture)
    app, root = setup(tmp_path)
    hooks = {'system': 'Linux', 'environ': {'WG2_DATA_DIR': str(data)}, 'home': tmp_path}
    mirror = {'schemaVersion': 1, **(capture or {})}
    assert read_settings(data / 'cadlink_settings.json') == mirror
    assert not (root / 'cadlink').exists()
    assert module.workspace_root(**hooks) is None
    assert module.missing_workspace(**hooks) is None
    rollback = api.CadWorkspaceState(data, allow_adoption=False)
    assert rollback.selected_path() is None
    assert 'Persisted WGLink path is unavailable' not in caplog.text
    restart(data, root)
    assert read_settings(data / 'cadlink_settings.json') == mirror
    # Polling and saving capture settings must not manufacture a CAD choice.
    asyncio.run(endpoint(app, '/api/cad-workspace/path')())
    app.state.cad_workspace.set_capture_mode('off' if capture else 'run')
    assert 'cadLinkPath' not in read_settings(data / 'cadlink_settings.json')
    assert module.missing_workspace(**hooks) is None
    chosen = tmp_path / 'chosen-cad'
    chosen.mkdir()
    before_choice = read_settings(data / 'cadlink_settings.json')
    app.state.cad_workspace.select(chosen)
    assert read_settings(data / 'cadlink_settings.json') == {**before_choice, 'cadLinkPath': str(chosen)}
    assert module.workspace_root(**hooks) == chosen
    chosen.rmdir()
    restart(data, root)
    assert module.missing_workspace(**hooks) == chosen
    assert not chosen.exists()


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
    assert seen == [('Select WG folder', root), ('Select WG folder', root)]


def test_windows_documents_ctypes_and_fallback(tmp_path, monkeypatch, real_windows_documents):
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


def test_windows_case_variants_preserve_recorded_spellings(tmp_path):
    data = tmp_path / 'data'
    root = tmp_path / 'ROOT'
    write(data, 'workspace_settings.json', {'workspacePath': str(root / 'RUNS')})
    write(data, 'cadlink_settings.json', {'cadLinkPath': str(tmp_path / 'root' / 'CADLINK')})
    f = restart(data, tmp_path / 'default', system='Windows')
    assert f.root == tmp_path / 'default'
    assert f.overrides == {'runs': root / 'RUNS', 'cadlink': tmp_path / 'root' / 'CADLINK'}
    assert read_settings(data / 'cadlink_settings.json')['cadLinkPath'] == str(tmp_path / 'root' / 'CADLINK')


def test_unavailable_root_does_not_crash_or_repoint_on_start(tmp_path, monkeypatch):
    data, root = tmp_path / 'data', tmp_path / 'offline'
    write(data, 'folders.json', {'schemaVersion': 1, 'rootPath': str(root)})
    f = restart(data, tmp_path / 'default')
    assert f.root == root and not f.overrides
    assert not root.exists()
    assert read_settings(data / 'workspace_settings.json')['workspacePath'] == str(root / 'runs')


def test_windows_deep_export_still_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.platform, 'system', lambda: 'Windows')
    runs = tmp_path / 'runs'
    runs.mkdir()
    subdirectory = 'project/' + 'run' * 80 + '/exports'
    assert len(str(runs / subdirectory / 'result.csv').encode('utf-16-le')) // 2 >= 260
    # Stop at publication so this check works even on Windows hosts whose
    # filesystem cannot write long paths. WG must leave acceptance to the OS,
    # as 0.3.5 did, rather than refusing a resolved spelling during validation.
    class PublicationReached(Exception):
        pass
    def publication(path, *args, **kwargs):
        raise PublicationReached
    monkeypatch.setattr(Path, 'mkdir', publication)
    with pytest.raises(PublicationReached):
        api._write_export_sync(runs, subdirectory, 'reject', [('result.csv', b'result')])
    assert list(runs.iterdir()) == []


@pytest.mark.parametrize('case', ['defaults', 'custom-runs', 'custom-cad', 'paired', 'unrelated', 'legacy', 'data-override', 'unmounted',
                                 'cad-chosen-runs-never-created', 'cad-chosen-runs-never-created-windows-redirected',
                                 'explicit-missing-default-runs'])
def test_upgrade_matrix_preserves_035_effective_folders_without_creating_content(tmp_path, monkeypatch, case):
    data, default_root = tmp_path / 'data', tmp_path / 'Documents' / 'Waveguide Generator'
    if case.endswith('windows-redirected'):
        monkeypatch.setattr(paths, 'windows_documents_dir', lambda: tmp_path / 'OneDrive' / 'Documents')
        default_root = paths.documents_root(system='Windows', environ={'USERPROFILE': str(tmp_path)}, home=tmp_path)
        assert default_root == tmp_path / 'OneDrive' / 'Documents' / 'Waveguide Generator'
        assert not paths.legacy_default_runs_dir(system='Windows', environ={'USERPROFILE': str(tmp_path)}, home=tmp_path).exists()
    implicit = data / 'workspace' if case == 'data-override' else default_root / 'runs'
    runs, cad = implicit, None
    legacy = tmp_path / 'output'
    implicit_missing = case.startswith('cad-chosen-runs-never-created')
    explicit_missing = case == 'explicit-missing-default-runs'
    if case == 'custom-runs':
        runs = tmp_path / 'custom-runs'
    elif case == 'custom-cad':
        cad = tmp_path / 'custom-cad'
    elif case in {'paired', 'unmounted'}:
        runs, cad = tmp_path / 'chosen' / 'runs', tmp_path / 'chosen' / 'cadlink'
    elif case == 'unrelated':
        runs, cad = tmp_path / 'custom-runs', tmp_path / 'custom-cad'
    elif case == 'legacy':
        runs = cad = legacy
    elif implicit_missing or explicit_missing:
        cad = tmp_path / 'custom-cad'
    if case != 'unmounted' and not (implicit_missing or explicit_missing):
        (runs / 'previous-run').mkdir(parents=True)
        (runs / 'previous-run' / 'keep.txt').write_text('never move or delete')
    if case != 'unmounted' and cad is not None:
        (cad / 'wglink').mkdir(parents=True, exist_ok=True)
    if case in {'custom-runs', 'paired', 'unrelated', 'unmounted'} or explicit_missing:
        write(data, 'workspace_settings.json', {'workspacePath': str(runs)})
    if cad is not None and case != 'legacy':
        write(data, 'cadlink_settings.json', {'cadLinkPath': str(cad), 'captureMode': 'project', 'captureDocument': True})
    if case == 'unmounted':
        write(data, 'folders.json', {'schemaVersion': 1, 'rootPath': str(runs.parent)})
    # Settings publication is allowed; no user folder or content may change.
    def content():
        return {str(p.relative_to(tmp_path)): (p.read_bytes() if p.is_file() else None)
                for p in tmp_path.rglob('*') if not p.is_relative_to(data)}
    before = content()
    app = FastAPI()
    app.state.data_dir = data
    api.mount_workspace(app, default_root=default_root, default_path=implicit,
                        legacy_defaults=(legacy,) if case == 'legacy' else ())
    assert app.state.folders.effective('runs') == runs
    assert app.state.cad_workspace.selected_path() == (cad if case != 'unmounted' else None)
    if cad is not None:
        assert app.state.folders.effective('cadlink') == cad
    if case == 'unmounted' or explicit_missing:
        with pytest.raises(api.WorkspaceUnavailableError):
            app.state.workspace.path()
    elif not implicit_missing:
        assert app.state.workspace.path() == runs
    assert content() == before
    if implicit_missing or explicit_missing:
        assert not runs.exists()
        request = api.WriteExportRequest(subdirectory='first-run/exports',
                                         members=[{'relative_path': 'result.txt', 'text': 'exported'}])
        response = asyncio.run(endpoint(app, '/api/workspace/write-export')(request))
        if implicit_missing:
            assert isinstance(response, dict), response.body
            assert response['directory'] == str(runs / 'first-run' / 'exports')
            assert (runs / 'first-run' / 'exports' / 'result.txt').read_text() == 'exported'
        else:
            assert response.status_code == 409
            assert not runs.exists()
    # Repeated starts preserve the content present after the first export.
    before_restart = content()
    api.mount_workspace(FastAPIWithData(data), default_root=default_root, default_path=implicit)
    assert content() == before_restart


def FastAPIWithData(data):
    app = FastAPI()
    app.state.data_dir = data
    return app


@pytest.mark.parametrize('cad_choice', ['absent', 'paired', 'unrelated'])
@pytest.mark.parametrize('content', ['runs', 'loose-file', 'empty'])
def test_windows_redirected_documents_retains_035_userprofile_runs(tmp_path, monkeypatch, cad_choice, content):
    from server import app as app_module

    home, data = tmp_path / 'profile', tmp_path / 'data'
    redirected = tmp_path / 'OneDrive' / 'Documents'
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('WG2_DRIVER_LIBRARY_DIR', str(tmp_path / 'drivers'))
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(paths.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(paths, 'windows_documents_dir', lambda: redirected)
    monkeypatch.setattr(app_module, 'resolve_data_dir', lambda *_: data)
    monkeypatch.setattr(app_module, 'LEGACY_WORKSPACE_DIR', tmp_path / 'unused-output')
    frontend = tmp_path / 'frontend'
    frontend.mkdir()
    monkeypatch.setattr(app_module, 'FRONTEND_DIST', frontend)
    old_runs = paths.legacy_default_runs_dir()
    assert old_runs == home / 'Documents' / 'Waveguide Generator' / 'runs'
    old_runs.mkdir(parents=True)
    if content == 'runs':
        (old_runs / 'previous-run').mkdir()
        (old_runs / 'previous-run' / 'keep').write_text('history')
    elif content == 'loose-file':
        (old_runs / 'keep').write_text('history')
    cad = old_runs.parent / 'cadlink' if cad_choice == 'paired' else tmp_path / 'other-cad'
    if cad_choice != 'absent':
        cad.mkdir()
        write(data, 'cadlink_settings.json', {'cadLinkPath': str(cad)})
    application = app_module.create_app()
    assert application.state.workspace.path() == old_runs
    assert not redirected.exists()
    if cad_choice == 'paired':
        assert application.state.folders.root == old_runs.parent
    else:
        assert application.state.folders.overrides['runs'] == old_runs
    assert application.state.cad_workspace.selected_path() == (cad if cad_choice != 'absent' else None)
    if content == 'runs':
        assert (old_runs / 'previous-run' / 'keep').read_text() == 'history'
    elif content == 'loose-file':
        assert (old_runs / 'keep').read_text() == 'history'
    else:
        assert list(old_runs.iterdir()) == []


@pytest.mark.parametrize('file', ['folders.json', 'workspace_settings.json', 'cadlink_settings.json'])
@pytest.mark.parametrize('error', [PermissionError, OSError])
def test_unreadable_settings_publish_nothing_and_use_readable_choices(tmp_path, monkeypatch, caplog, file, error):
    data, root, cad = tmp_path / 'data', tmp_path / 'saved-root', tmp_path / 'saved-cad'
    (root / 'runs').mkdir(parents=True)
    cad.mkdir()
    write(data, 'folders.json', {'schemaVersion': 1, 'rootPath': str(root), 'overrides': {'cadlink': str(cad)}})
    write(data, 'workspace_settings.json', {'workspacePath': str(root / 'runs')})
    capture = {'cadLinkPath': str(cad), 'captureDocument': False, 'captureMode': 'off', 'unknown': 'keep'}
    write(data, 'cadlink_settings.json', capture)
    before = {p.name: p.read_bytes() for p in data.iterdir()}
    real_read = Path.read_text
    def refuse(path, *args, **kwargs):
        if path == data / file:
            raise error('simulated unreadable settings')
        return real_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', refuse)
    app = FastAPIWithData(data)
    api.mount_workspace(app, default_root=tmp_path / 'default', default_path=root / 'runs')
    f = app.state.folders
    assert f.read_only
    assert f.effective('runs') == root / 'runs'
    assert f.effective('cadlink') == cad  # root config or readable mirror
    assert 'cannot read' in caplog.text
    with pytest.raises(ValueError, match='read-only'):
        f.select(tmp_path)
    with pytest.raises(ValueError, match='read-only'):
        app.state.cad_workspace.set_capture_mode('run')
    f.persist()
    assert {p.name: p.read_bytes() for p in data.iterdir()} == before
    assert not (tmp_path / 'default').exists()


def test_unreadable_newer_schema_is_never_replaced_or_legacy_adopted(tmp_path, monkeypatch):
    data, legacy = tmp_path / 'data', tmp_path / 'output'
    (legacy / 'wglink').mkdir(parents=True)
    write(data, 'folders.json', {'schemaVersion': 2, 'private': 'future settings'})
    before = (data / 'folders.json').read_bytes()
    real_read = Path.read_text
    def refuse(path, *args, **kwargs):
        if path == data / 'folders.json':
            raise PermissionError('not readable')
        return real_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', refuse)
    app = FastAPIWithData(data)
    api.mount_workspace(app, default_root=tmp_path / 'default', default_path=tmp_path / 'default' / 'runs', legacy_defaults=(legacy,))
    assert app.state.folders.read_only
    assert (data / 'folders.json').read_bytes() == before
    assert not (data / 'workspace_settings.json').exists()
    assert not (data / 'cadlink_settings.json').exists()
    assert not (tmp_path / 'default').exists()


@pytest.mark.parametrize('error', [OSError, FileNotFoundError, ValueError])
def test_read_failure_at_persist_preflights_all_files_before_any_publication(tmp_path, monkeypatch, error):
    app, root = setup(tmp_path)
    f = app.state.folders
    before = {p.name: p.read_bytes() for p in f.data_dir.iterdir()}
    original = Path.read_text
    def refuse(path, *args, **kwargs):
        if path == f.data_dir / 'cadlink_settings.json':
            raise error('read failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', refuse)
    f.writer = lambda *_: pytest.fail('Read failure must prevent every publication')
    with pytest.raises(OSError, match='no settings were published'):
        f.persist()
    assert {p.name: p.read_bytes() for p in f.data_dir.iterdir()} == before


def test_alias_parents_keep_exact_mirror_spellings(tmp_path):
    root, alias, data = tmp_path / 'mapped-drive', tmp_path / 'unc-share', tmp_path / 'data'
    (root / 'runs').mkdir(parents=True)
    (root / 'cadlink').mkdir()
    alias.symlink_to(root, target_is_directory=True)
    runs, cad = root / 'runs', alias / 'cadlink'
    assert paths.same_folder(runs.parent, cad.parent, system='Windows')
    write(data, 'workspace_settings.json', {'workspacePath': str(runs)})
    write(data, 'cadlink_settings.json', {'cadLinkPath': str(cad)})
    f = restart(data, tmp_path / 'default', system='Windows')
    assert f.effective('runs') == runs and f.effective('cadlink') == cad
    assert read_settings(data / 'cadlink_settings.json')['cadLinkPath'] == str(cad)
    assert not (tmp_path / 'default').exists()


def test_spelling_only_rollback_drift_is_adopted_once(tmp_path):
    app, root = setup(tmp_path)
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    data = app.state.folders.data_dir
    write(data, 'cadlink_settings.json', {'cadLinkPath': str(alias / 'cadlink')})
    f = restart(data, root)
    assert f.effective('cadlink') == alias / 'cadlink'
    assert f.notice
    assert restart(data, root).notice is None
    assert read_settings(data / 'cadlink_settings.json')['cadLinkPath'] == str(alias / 'cadlink')


def test_only_explicit_use_root_clears_an_override(tmp_path):
    app, root = setup(tmp_path)
    f = app.state.folders
    new = tmp_path / 'new'
    (new / 'runs').mkdir(parents=True)
    f.set_override('runs', new / 'runs')
    f.select(new)
    assert f.overrides == {'runs': new / 'runs'}
    assert restart(f.data_dir, root).overrides == f.overrides
    f.use_root('runs')
    assert f.overrides == {}
    assert 'overrides' not in read_settings(f.settings_path)


def test_failed_config_publication_does_not_pin_successfully_selected_root_as_overrides(tmp_path):
    app, root = setup(tmp_path)
    f = app.state.folders
    new = tmp_path / 'new'
    new.mkdir()
    def refuse(path, payload):
        if path.name == 'folders.json':
            raise OSError('config write refused')
        api._write_json_atomic(path, payload)
    f.writer = refuse
    with pytest.raises(OSError):
        f.select(new)
    assert f.root == new and f.overrides == {}
    resumed = restart(f.data_dir, root)
    assert resumed.root == new and resumed.overrides == {}
    assert resumed.notice is None
    assert restart(f.data_dir, root).notice is None
    resumed.select(new)
    assert resumed.overrides == {}


def test_schema_one_unknown_fields_and_override_names_survive(tmp_path):
    data, root = tmp_path / 'data', tmp_path / 'saved'
    (root / 'runs').mkdir(parents=True)
    (root / 'cadlink').mkdir()
    payload = {'schemaVersion': 1, 'rootPath': str(root), 'newOption': {'keep': True},
               'overrides': {'futureFolder': {'opaque': 'preserve'}}}
    write(data, 'folders.json', payload)
    f = restart(data, tmp_path / 'default')
    assert f.root == root and f.overrides == {} and not f.read_only
    assert read_settings(f.settings_path) == payload
    new = tmp_path / 'new'
    new.mkdir()
    f.select(new)
    assert read_settings(f.settings_path) == {**payload, 'rootPath': str(new)}
    assert not (tmp_path / 'default').exists()


def test_open_missing_selected_root_refuses_without_creating_it(tmp_path, monkeypatch):
    data, root = tmp_path / 'data', tmp_path / 'offline'
    write(data, 'folders.json', {'schemaVersion': 1, 'rootPath': str(root)})
    app = FastAPIWithData(data)
    api.mount_workspace(app, default_root=tmp_path / 'default', default_path=tmp_path / 'default' / 'runs')
    monkeypatch.setattr(api.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('Unavailable root cannot be opened'))
    with pytest.raises(api.HTTPException, match='unavailable'):
        asyncio.run(endpoint(app, '/api/folders/open')())
    with pytest.raises(api.WorkspaceUnavailableError):
        app.state.workspace.path()
    assert not root.exists()


def test_windows_documents_guard_uses_injected_environment(tmp_path, monkeypatch):
    # Without the suite guard, Windows' known-folder API ignores these values.
    def refuse(*_args):
        pytest.fail('The suite must not call the real known-folder API')
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(shell32=SimpleNamespace(SHGetKnownFolderPath=refuse),
                                                       ole32=SimpleNamespace(CoTaskMemFree=lambda *_: None)), raising=False)
    assert paths.windows_documents_dir() is None
    assert paths.documents_root(system='Windows', environ={'USERPROFILE': str(tmp_path)}, home='/unused') == tmp_path / 'Documents' / 'Waveguide Generator'


def test_recorded_root_does_not_readopt_legacy_output_when_mirrors_are_absent(tmp_path):
    data, root, legacy = tmp_path / 'data', tmp_path / 'offline', tmp_path / 'output'
    (legacy / 'old-run').mkdir(parents=True)
    write(data, 'folders.json', {'schemaVersion': 1, 'rootPath': str(root)})
    app = FastAPIWithData(data)
    api.mount_workspace(app, default_root=tmp_path / 'default', default_path=tmp_path / 'default' / 'runs', legacy_defaults=(legacy,))
    assert app.state.folders.root == root
    assert app.state.folders.overrides == {}
    assert app.state.folders.effective('runs') == root / 'runs'
    assert not root.exists()


def test_root_choice_preserves_alias_override_until_explicitly_cleared(tmp_path):
    app, root = setup(tmp_path)
    f = app.state.folders
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    f.set_override('runs', alias / 'runs')
    assert f.overrides == {'runs': alias / 'runs'}  # recorded spelling retained
    f.select(root)
    assert f.overrides == {'runs': alias / 'runs'}
    f.use_root('runs')
    assert f.overrides == {}
    assert read_settings(f.settings_path).get('overrides', {}) == {}
    assert f.effective('runs') == root / 'runs'


def test_override_read_failure_keeps_the_last_readable_selection(tmp_path, monkeypatch):
    app, root = setup(tmp_path)
    f = app.state.folders
    chosen = tmp_path / 'chosen'
    chosen.mkdir()
    before = {p.name: p.read_bytes() for p in f.data_dir.iterdir()}
    original = Path.read_text
    def refuse(path, *args, **kwargs):
        if path == f.data_dir / 'workspace_settings.json':
            raise PermissionError('became unreadable')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', refuse)
    with pytest.raises(OSError, match='no settings were published'):
        f.set_override('runs', chosen)
    assert f.read_only
    assert f.effective('runs') == root / 'runs'
    assert not f.runs_selected
    assert app.state.workspace.selected_path() is None
    assert {p.name: p.read_bytes() for p in f.data_dir.iterdir()} == before


def test_windows_child_case_variants_pair_only_the_exact_recorded_parent(tmp_path):
    data, root = tmp_path / 'data', tmp_path / 'chosen'
    runs, cad = root / 'RUNS', root / 'CADLINK'
    runs.mkdir(parents=True)
    cad.mkdir()
    write(data, 'workspace_settings.json', {'workspacePath': str(runs)})
    write(data, 'cadlink_settings.json', {'cadLinkPath': str(cad)})
    f = restart(data, tmp_path / 'default', system='Windows')
    assert f.root == root
    assert f.effective('runs') == runs and f.effective('cadlink') == cad
    assert read_settings(data / 'workspace_settings.json')['workspacePath'] == str(runs)
    assert read_settings(data / 'cadlink_settings.json')['cadLinkPath'] == str(cad)


def test_both_spelling_only_mirror_changes_keep_their_recorded_windows_case(tmp_path):
    app, root = setup(tmp_path)
    data = app.state.folders.data_dir
    choices = {'runs': root / 'RUNS', 'cadlink': root / 'CADLINK'}
    for name, (file, key) in NAMES.items():
        write(data, file, {key: str(choices[name])})
    f = restart(data, root, system='Windows')
    assert f.root == root and f.notice
    for name, (file, key) in NAMES.items():
        assert f.effective(name) == choices[name]
        assert read_settings(data / file)[key] == str(choices[name])
    assert restart(data, root, system='Windows').notice is None


@pytest.mark.parametrize('child', ['wglink', 'wgreturn', 'runs'])
def test_new_root_refuses_child_folders_without_changing_settings(tmp_path, child):
    app, root = setup(tmp_path)
    chosen = tmp_path / 'exchange'
    if child == 'runs':
        chosen = root / 'runs'
    else:
        (chosen / child).mkdir(parents=True)
    before = {p.name: p.read_bytes() for p in app.state.folders.data_dir.glob('*.json')}
    with pytest.raises(ValueError, match='Choose the folder that should contain'):
        app.state.folders.select(chosen)
    assert before == {p.name: p.read_bytes() for p in app.state.folders.data_dir.glob('*.json')}


def test_existing_child_like_root_is_only_noted_on_restart(tmp_path):
    app, root = setup(tmp_path)
    (root / 'wglink').mkdir()
    before = {p.name: p.read_bytes() for p in app.state.folders.data_dir.glob('*.json')}
    f = restart(app.state.folders.data_dir, root)
    assert f.root == root
    assert f.info()['rootNote'].startswith('This folder is a WGLink folder.')
    assert before == {p.name: p.read_bytes() for p in f.data_dir.glob('*.json')}


def test_legacy_cad_select_chooses_root_and_preserves_overrides(tmp_path):
    app, root = setup(tmp_path)
    old = root / 'cadlink'
    app.state.folders.set_override('cadlink', old)
    new = tmp_path / 'new'
    new.mkdir()
    result = asyncio.run(endpoint(app, '/api/cad-workspace/select')(api.SelectCadWorkspaceRequest(path=str(new))))
    assert result == {'selected': True, 'path': str(old)}
    assert app.state.folders.root == new
    assert app.state.folders.overrides == {'cadlink': old}
    assert not (new / 'cadlink').exists()


@pytest.mark.parametrize('override', [False, True])
def test_missing_cad_folder_is_reported_and_created_only_on_explicit_action(tmp_path, override):
    app, root = setup(tmp_path)
    f = app.state.folders
    old = tmp_path / 'old-exchange'
    (root / 'cadlink').rmdir()
    if override:
        old.mkdir()
        f.set_override('cadlink', old)
        old.rmdir()
    app, _ = setup(tmp_path)
    f = app.state.folders
    result = asyncio.run(endpoint(app, '/api/cad-workspace/path')())
    assert result['path'] is None
    assert result['missingPath'] == str(old if override else root / 'cadlink')
    assert not (root / 'cadlink').exists()
    asyncio.run(endpoint(app, '/api/folders/select')(api.UseRootFolderRequest(useRoot='cadlink')))
    assert app.state.cad_workspace.path() == root / 'cadlink'
    assert f.overrides == {}
    assert not old.exists()
    assert read_settings(f.data_dir / 'cadlink_settings.json')['cadLinkPath'] == str(root / 'cadlink')


def test_proposed_creation_uses_chosen_root_not_platform_default_or_override(tmp_path):
    app, root = setup(tmp_path)
    new = tmp_path / 'new'
    new.mkdir()
    app.state.folders.select(new)
    (new / 'cadlink').rmdir()
    old = tmp_path / 'old'
    old.mkdir()
    app.state.folders.set_override('cadlink', old)
    old.rmdir()
    cad = app.state.cad_workspace
    cad.create_proposed_if_requested(old)
    assert not old.exists()
    cad.create_proposed_if_requested(new / 'cadlink')
    assert (new / 'cadlink').is_dir()
    assert cad.selected_path() is None  # Creation alone never clears the override.


@pytest.mark.parametrize('fail_at', ['workspace_settings.json', 'cadlink_settings.json', 'folders.json'])
def test_interrupted_override_clear_recovers_durable_mirrors(tmp_path, fail_at):
    app, root = setup(tmp_path)
    f = app.state.folders
    old = tmp_path / 'old'
    old.mkdir()
    f.set_override('cadlink', old)
    f.set_override('runs', old)
    previous = {name: f.effective(name) for name in NAMES}
    def writer(path, payload):
        if path.name == fail_at:
            raise OSError('interrupted')
        api._write_json_atomic(path, payload)
    f.writer = writer
    with pytest.raises(OSError, match='interrupted'):
        f.use_root('runs' if fail_at == 'workspace_settings.json' else 'cadlink')
    durable = {name: Path(read_settings(f.data_dir / file)[key]) for name, (file, key) in NAMES.items()}
    assert {name: f.effective(name) for name in NAMES} == durable
    again = restart(f.data_dir, root)
    assert {name: again.effective(name) for name in NAMES} == durable
    assert old.is_dir()
    assert all(value == previous[name] or value == root / name for name, value in durable.items())


@pytest.mark.parametrize('name', ['cadlink', 'runs'])
def test_empty_child_folder_and_its_alias_are_refused_as_roots(tmp_path, name):
    app, root = setup(tmp_path)
    alias = tmp_path / 'alias'
    alias.symlink_to(root / name, target_is_directory=True)
    for chosen in (root / name, alias):
        with pytest.raises(ValueError, match='Choose the folder that should contain'):
            app.state.folders.select(chosen)
    assert not (root / name / 'cadlink').exists()


@pytest.mark.parametrize('name', ['cadlink', 'runs'])
def test_failed_root_choice_retains_equal_child_override_before_retry(tmp_path, name):
    app, root = setup(tmp_path)
    f = app.state.folders
    child = root / name
    f.set_override(name, child)
    new = tmp_path / 'new'
    new.mkdir()
    def refuse(path, payload):
        raise OSError('before publication')
    f.writer = refuse
    with pytest.raises(OSError, match='before publication'):
        f.select(new)
    assert f.overrides == {name: child}
    assert restart(f.data_dir, root).overrides == {name: child}
    f.writer = api._write_json_atomic
    f.select(new)
    assert f.effective(name) == child
    assert f.overrides == {name: child}


def test_failed_override_clear_does_not_replace_chosen_root_with_mirror_parent(tmp_path):
    app, root = setup(tmp_path)
    f = app.state.folders
    for name in NAMES:
        f.set_override(name, root / name)
    new = tmp_path / 'new'
    new.mkdir()
    f.select(new)
    def refuse(path, payload):
        raise OSError('before publication')
    f.writer = refuse
    with pytest.raises(OSError, match='before publication'):
        f.use_root('cadlink')
    assert f.root == new
    assert f.overrides == {name: root / name for name in NAMES}
    f.writer = api._write_json_atomic
    f.use_root('cadlink')
    assert f.root == new
    assert f.effective('cadlink') == new / 'cadlink'
    assert f.effective('runs') == root / 'runs'


@pytest.mark.parametrize('missing', [False, True])
def test_compatibility_cad_select_reports_effective_availability_on_cancel(tmp_path, monkeypatch, missing):
    app, root = setup(tmp_path)
    if missing:
        (root / 'cadlink').rmdir()
    monkeypatch.setattr(api, '_select_workspace_folder', lambda *args: None)
    assert asyncio.run(endpoint(app, '/api/cad-workspace/select')()) == {
        'selected': not missing, 'path': None if missing else str(root / 'cadlink')}


def test_compatibility_cad_select_does_not_claim_success_for_missing_override(tmp_path):
    app, root = setup(tmp_path)
    old = root / 'cadlink'
    app.state.folders.set_override('cadlink', old)
    old.rmdir()
    new = tmp_path / 'new'
    new.mkdir()
    result = asyncio.run(endpoint(app, '/api/cad-workspace/select')(api.SelectCadWorkspaceRequest(path=str(new))))
    assert result == {'selected': False, 'path': None}
    assert app.state.folders.root == new
    assert app.state.folders.overrides == {'cadlink': old}


def test_stale_override_confirmation_refuses_without_publication(tmp_path):
    app, root = setup(tmp_path)
    f = app.state.folders
    old = tmp_path / 'old'
    old.mkdir()
    f.set_override('cadlink', old)
    new = tmp_path / 'new'
    new.mkdir()
    f.select(new)
    before = {p.name: p.read_bytes() for p in f.data_dir.glob('*.json')}
    with pytest.raises(api.HTTPException, match='WG folder changed'):
        asyncio.run(endpoint(app, '/api/folders/select')(
            api.UseRootFolderRequest(useRoot='cadlink', expectedPath=str(root / 'cadlink'))))
    assert f.overrides == {'cadlink': old}
    assert before == {p.name: p.read_bytes() for p in f.data_dir.glob('*.json')}
    assert not (new / 'cadlink').exists()


def test_stale_override_path_confirmation_is_refused(tmp_path):
    app, root = setup(tmp_path)
    f = app.state.folders
    old, newer = tmp_path / 'old', tmp_path / 'newer'
    old.mkdir()
    newer.mkdir()
    f.set_override('runs', old)
    f.set_override('runs', newer)
    with pytest.raises(ValueError, match='WG folder changed'):
        f.use_root('runs', root / 'runs', old)
    assert f.overrides == {'runs': newer}



def test_read_failure_preserves_missing_legacy_runs_without_publication(tmp_path):
    data, root = tmp_path / 'data', tmp_path / 'data' / 'workspace-root'
    (root / 'runs').mkdir(parents=True)
    legacy = data / 'workspace'
    f = FolderRoot(data, default_root=root, legacy_runs=legacy,
                   writer=api._write_json_atomic, startup_read=SettingsRead(failed=True, read_only=True))
    assert f.read_only
    assert f.effective('runs') == legacy
    assert f.implicit_runs_path is None
    assert not (data / 'folders.json').exists()


def test_missing_legacy_default_is_not_replaced_on_startup_or_restore(tmp_path):
    data, root = tmp_path / 'data', tmp_path / 'data' / 'workspace-root'
    legacy = data / 'workspace'
    for restored in (False, True):
        if restored:
            (legacy / 'previous-run').mkdir(parents=True)
            (legacy / 'previous-run' / 'keep').write_text('history')
        app = FastAPIWithData(data)
        api.mount_workspace(app, default_root=root, default_path=legacy)
        f = app.state.folders
        assert f.effective('runs') == legacy
        assert f.overrides == {'runs': legacy}
        assert f.implicit_runs_path == legacy
        assert not f.runs_selected
        assert legacy.exists() == restored
        assert not root.exists()
        assert read_settings(data / 'workspace_settings.json')['workspacePath'] == str(legacy)
        assert read_settings(data / 'folders.json')['overrides'] == {'runs': str(legacy)}
    assert app.state.workspace.path() == legacy
    assert (legacy / 'previous-run' / 'keep').read_text() == 'history'


@pytest.mark.parametrize('file', ['folders.json', 'workspace_settings.json', 'cadlink_settings.json'])
@pytest.mark.parametrize('content', [b'{', b'[]', b'\xff'])
def test_malformed_settings_block_startup_publication_but_allow_explicit_recovery(tmp_path, file, content):
    data, root, legacy = tmp_path / 'data', tmp_path / 'default', tmp_path / 'old-output'
    (legacy / 'wglink').mkdir(parents=True)
    data.mkdir()
    (data / file).write_bytes(content)
    before = {p.name: p.read_bytes() for p in data.iterdir()}
    app = FastAPIWithData(data)
    api.mount_workspace(app, default_root=root, default_path=data / 'workspace', legacy_defaults=(legacy,))
    f = app.state.folders
    assert not f.read_only
    assert f.notice == f"{file} is corrupt. Choosing a WG folder will replace it."
    assert f.effective('runs') == data / 'workspace'
    assert f.implicit_runs_path is None
    assert {p.name: p.read_bytes() for p in data.iterdir()} == before
    assert not root.exists()
    assert not (data / 'workspace').exists()
    f.select(tmp_path)
    assert f.notice is None
    assert not f.read_only
    assert read_settings(data / 'folders.json')['rootPath'] == str(tmp_path)
    for name, (mirror, key) in NAMES.items():
        assert read_settings(data / mirror)[key] == str(f.effective(name))


@pytest.mark.parametrize('file', ['folders.json', 'workspace_settings.json', 'cadlink_settings.json'])
def test_settings_disappearing_between_startup_reads_publish_nothing(tmp_path, monkeypatch, file):
    app, root = setup(tmp_path)
    data = app.state.folders.data_dir
    real_read = api.read_folder_settings
    def disappear(*args, **kwargs):
        result = real_read(*args, **kwargs)
        (data / file).unlink()
        return result
    monkeypatch.setattr(api, 'read_folder_settings', disappear)
    writer = api._write_json_atomic
    monkeypatch.setattr(api, '_write_json_atomic', lambda *_: pytest.fail('Disappeared settings must prevent publication'))
    app = FastAPIWithData(data)
    api.mount_workspace(app, default_root=tmp_path / 'default', default_path=data / 'workspace')
    assert not app.state.folders.read_only
    assert not (data / file).exists()
    assert not (tmp_path / 'default').exists()
    app.state.folders.writer = writer
    app.state.folders.select(tmp_path)
    assert read_settings(data / 'folders.json')['rootPath'] == str(tmp_path)
    assert read_settings(data / file)


@pytest.mark.parametrize('file', ['folders.json', 'workspace_settings.json', 'cadlink_settings.json'])
def test_previously_published_settings_disappearing_prevent_republication(tmp_path, file):
    app, root = setup(tmp_path)
    f = app.state.folders
    (f.data_dir / file).unlink()
    before = {p.name: p.read_bytes() for p in f.data_dir.iterdir()}
    f.writer = lambda *_: pytest.fail('Disappeared settings must prevent every publication')
    with pytest.raises(OSError, match='no settings were published'):
        f.persist()
    assert not f.read_only
    assert {p.name: p.read_bytes() for p in f.data_dir.iterdir()} == before
    f.writer = api._write_json_atomic
    f.select(tmp_path)
    assert read_settings(f.data_dir / file)


def test_disappearing_runs_mirror_does_not_enable_legacy_adoption(tmp_path, monkeypatch):
    data, chosen, legacy = tmp_path / 'data', tmp_path / 'chosen', tmp_path / 'old-output'
    chosen.mkdir()
    (legacy / 'previous-run').mkdir(parents=True)
    write(data, 'workspace_settings.json', {'workspacePath': str(chosen)})
    real_read = api.read_folder_settings
    def disappear(*args, **kwargs):
        result = real_read(*args, **kwargs)
        (data / 'workspace_settings.json').unlink()
        return result
    monkeypatch.setattr(api, 'read_folder_settings', disappear)
    writer = api._write_json_atomic
    monkeypatch.setattr(api, '_write_json_atomic', lambda *_: pytest.fail('Disappeared settings must prevent adoption'))
    app = FastAPIWithData(data)
    api.mount_workspace(app, default_root=tmp_path / 'default', default_path=data / 'workspace', legacy_defaults=(legacy,))
    assert not app.state.folders.read_only
    assert list(data.iterdir()) == []
    app.state.folders.writer = writer
    app.state.folders.select(tmp_path)
    assert read_settings(data / 'folders.json')['rootPath'] == str(tmp_path)


@pytest.mark.parametrize('file', ['folders.json', 'workspace_settings.json', 'cadlink_settings.json'])
@pytest.mark.parametrize('content', [b'{', b'[]', b'\xff'])
@pytest.mark.parametrize('action', ['choose', 'cadlink', 'runs'])
def test_corrupt_settings_keep_readable_paths_and_allow_each_folder_action(tmp_path, file, content, action):
    data, root, cad = tmp_path / 'data', tmp_path / 'saved-root', tmp_path / 'saved-cad'
    (root / 'runs').mkdir(parents=True)
    cad.mkdir()
    write(data, 'folders.json', {'schemaVersion': 1, 'rootPath': str(root), 'overrides': {'cadlink': str(cad)}})
    write(data, 'workspace_settings.json', {'workspacePath': str(root / 'runs'), 'unknownRuns': 'keep'})
    write(data, 'cadlink_settings.json', {'cadLinkPath': str(cad), 'captureMode': 'off', 'unknownCad': 'keep'})
    (data / file).write_bytes(content)
    before = {p.name: p.read_bytes() for p in data.iterdir()}
    app = FastAPIWithData(data)
    api.mount_workspace(app, default_root=tmp_path / 'default', default_path=root / 'runs')
    f = app.state.folders
    assert not f.read_only
    assert app.state.workspace.path() == root / 'runs'
    assert app.state.cad_workspace.path() == cad
    assert {p.name: p.read_bytes() for p in data.iterdir()} == before
    published = []
    def publish(path, payload):
        published.append(path.name)
        api._write_json_atomic(path, payload)
    f.writer = publish
    if action == 'choose':
        f.select(tmp_path)
    else:
        f.use_root(action)
    assert not f.read_only
    assert f.notice is None
    if 'folders.json' in published:
        assert published[-1] == 'folders.json'
    for name, (mirror, key) in NAMES.items():
        payload = read_settings(data / mirror)
        assert payload[key] == str(f.effective(name))
        if mirror != file:
            assert payload['unknownRuns' if name == 'runs' else 'unknownCad'] == 'keep'
    assert read_settings(data / 'folders.json')['rootPath'] == str(f.root)


@pytest.mark.parametrize('file', ['folders.json', 'workspace_settings.json', 'cadlink_settings.json'])
def test_explicit_choice_ignores_disappearance_during_read(tmp_path, monkeypatch, file):
    app, _ = setup(tmp_path)
    f = app.state.folders
    original = Path.read_text
    def disappear(path, *args, **kwargs):
        if path == f.data_dir / file:
            raise FileNotFoundError('disappeared after discovery')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_text', disappear)
        f.select(tmp_path)
    assert not f.read_only
    assert read_settings(f.settings_path)['rootPath'] == str(tmp_path)
    assert read_settings(f.data_dir / file)


@pytest.mark.parametrize('fail_at', ['cadlink_settings.json', 'folders.json'])
def test_corrupt_settings_recovery_retains_durable_mirrors_on_write_failure(tmp_path, fail_at):
    data, root = tmp_path / 'data', tmp_path / 'default'
    data.mkdir()
    (data / 'folders.json').write_bytes(b'{')
    f = restart(data, root)
    chosen = tmp_path / 'chosen'
    chosen.mkdir()
    def fail(path, payload):
        if path.name == fail_at:
            raise OSError('write failed')
        api._write_json_atomic(path, payload)
    f.writer = fail
    with pytest.raises(OSError, match='write failed'):
        f.select(chosen)
    assert not f.read_only
    assert f.effective('runs') == chosen / 'runs'
    assert (data / 'folders.json').read_bytes() == b'{'
    f.writer = api._write_json_atomic
    f.select(chosen)
    assert f.notice is None
    assert read_settings(data / 'folders.json')['rootPath'] == str(chosen)
