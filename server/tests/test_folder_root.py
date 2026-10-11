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
    assert f.root == root
    assert f.overrides == ({'runs': f.data_dir / 'workspace'} if isolated else {})
    assert app.state.workspace.path() == (f.data_dir / 'workspace' if isolated else root / 'runs')
    if isolated:
        assert app.state.cad_workspace.selected_path() is None
        assert not (root / 'cadlink').exists()
    else:
        assert app.state.cad_workspace.path() == root / 'cadlink'
    assert read_settings(f.settings_path)['rootPath'] == str(root)
    assert read_settings(f.data_dir / 'workspace_settings.json')['workspacePath'] == str(app.state.workspace.path())
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


@pytest.mark.parametrize('case', ['defaults', 'custom-runs', 'custom-cad', 'paired', 'unrelated', 'legacy', 'data-override', 'unmounted'])
def test_upgrade_matrix_preserves_035_effective_folders_without_creating_content(tmp_path, case):
    data, default_root = tmp_path / 'data', tmp_path / 'Documents' / 'Waveguide Generator'
    implicit = data / 'workspace' if case == 'data-override' else default_root / 'runs'
    runs, cad = implicit, None
    legacy = tmp_path / 'output'
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
    if case != 'unmounted':
        (runs / 'previous-run').mkdir(parents=True)
        (runs / 'previous-run' / 'keep.txt').write_text('never move or delete')
        if cad is not None:
            (cad / 'wglink').mkdir(parents=True, exist_ok=True)
    if case in {'custom-runs', 'paired', 'unrelated', 'unmounted'}:
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
    if case == 'unmounted':
        with pytest.raises(api.WorkspaceUnavailableError):
            app.state.workspace.path()
    else:
        assert app.state.workspace.path() == runs
    assert content() == before
    # Repeated starts must be equally harmless.
    api.mount_workspace(FastAPIWithData(data), default_root=default_root, default_path=implicit)
    assert content() == before


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


def test_read_failure_at_persist_preflights_all_files_before_any_publication(tmp_path, monkeypatch):
    app, root = setup(tmp_path)
    f = app.state.folders
    before = {p.name: p.read_bytes() for p in f.data_dir.iterdir()}
    original = Path.read_text
    def refuse(path, *args, **kwargs):
        if path == f.data_dir / 'cadlink_settings.json':
            raise OSError('IO failure')
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


def test_override_cleanup_on_selection_and_persistence(tmp_path):
    app, root = setup(tmp_path)
    f = app.state.folders
    new = tmp_path / 'new'
    (new / 'runs').mkdir(parents=True)
    f.set_override('runs', new / 'runs')
    f.select(new)
    assert f.overrides == {}
    assert restart(f.data_dir, root).overrides == {}
    f.overrides['cadlink'] = new / 'cadlink'
    f.persist()
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


def test_explicit_root_choice_clears_overrides_with_the_same_filesystem_identity(tmp_path):
    app, root = setup(tmp_path)
    f = app.state.folders
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    f.set_override('runs', alias / 'runs')
    assert f.overrides == {'runs': alias / 'runs'}  # recorded spelling retained
    f.select(root)
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
