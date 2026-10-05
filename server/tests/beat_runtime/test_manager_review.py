from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from server.platform import temp_session
from server.solver.beat_runtime import identity, manager, paths, registry as r
from server.solver.beat_runtime.client import HostedWorker
from server.solver.beat_runtime.ipc import Endpoint
from server.solver.beat_runtime.ownership import OwnershipClosed
from server.solver.beat_runtime.session import SolveSession
from server.tests.beat_runtime.fake_host_worker import EngineWorker, events
from server.tests.beat_runtime import test_manager

from server.tests.beat_runtime.test_submission_review import running_host

engine_tree = test_manager.engine_tree


@pytest.fixture(autouse=True)
def staging(tmp_path, monkeypatch):
    monkeypatch.setattr(temp_session, '_active_root', str(tmp_path))


def test_worker_key_ignores_ambient_environment_but_keys_julia_blab(engine_tree, tmp_path):
    base = {'WG2_BEAT_RUNTIME_DIR': str(tmp_path / 'runtime'), 'JULIA_FOO': '1', 'BLAB_FOO': '2'}
    options = {'julia_executable': sys.executable, 'environment': base}
    key = manager.resolve_key('cpu', **options)
    unrelated = dict(base, PWD='/another', OLDPWD='/old', SHLVL='4', TERM_SESSION_ID='new',
                     LAUNCHD_ID='new', AWS_SECRET_ACCESS_KEY='secret', API_TOKEN='token')
    assert r.key_id(manager.resolve_key('cpu', **dict(options, environment=unrelated))) == r.key_id(key)
    for name in ('JULIA_FOO', 'BLAB_FOO'):
        changed = dict(base, **{name: 'changed'})
        assert r.key_id(manager.resolve_key('cpu', **dict(options, environment=changed))) != r.key_id(key)
    assert all(name.startswith(('JULIA_', 'BLAB_')) for name in key['environment'])


def test_unkeyed_secrets_reach_launch_only_and_never_written_records(engine_tree, tmp_path, monkeypatch):
    constructed = []

    class Hosted:
        def __init__(self, key, **kwargs):
            self.key, self.environment = key, kwargs['environment']
            constructed.append(self)

        def detach(self):
            pass

    monkeypatch.setattr(manager, 'HostedWorker', Hosted)
    runtime = manager.WorkerManager(directory=tmp_path / 'workers')
    worker = runtime.get_worker(julia_executable=sys.executable,
                                environment={'API_TOKEN': 'private-token', 'BLAB_TUNING': '1'})
    assert worker.worker.environment['API_TOKEN'] == 'private-token'
    key = worker.worker.key
    directory = tmp_path / 'records'
    r.write_launch_spec(key, directory)
    record = r.HostRecord(key, os.getpid(), r.new_token(), Endpoint('tcp', port=12345))
    r.write_record(record, directory)
    for file in directory.glob('*.json'):
        text = file.read_text()
        assert 'API_TOKEN' not in text and 'private-token' not in text
    assert 'private-token' not in json.dumps(key)
    runtime.detach()


@pytest.mark.parametrize('destination', ['depot', 'project', 'environment_project', 'linked_depot'])
def test_destination_checks_refuse_hbb_project_and_inherited_depots(engine_tree, tmp_path, destination):
    hbb = tmp_path / 'hbb'
    hbb.mkdir()
    env = {'HORNLAB_BEAT_RUNTIME_DIR': str(hbb), 'WG2_BEAT_RUNTIME_DIR': str(tmp_path / 'wg')}
    options = {'julia_executable': sys.executable, 'environment': env}
    if destination == 'project':
        options['julia_project'] = hbb / 'project'
    elif destination == 'environment_project':
        env['JULIA_PROJECT'] = str(hbb / 'project')
    else:
        depot = hbb / 'depot'
        if destination == 'linked_depot':
            link = tmp_path / 'link'
            link.symlink_to(hbb, target_is_directory=True)
            depot = link / 'depot'
        env['JULIA_DEPOT_PATH'] = os.pathsep.join((str(tmp_path / 'safe'), str(depot)))
    with pytest.raises(paths.RootConflict):
        manager.resolve_key('cpu', **options)
    assert list(hbb.iterdir()) == []


def test_depot_and_launch_path_entries_are_absolute(engine_tree, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = {'JULIA_DEPOT_PATH': os.pathsep.join(('depot', '', 'other')),
           'PATH': os.pathsep.join(('bin', '', 'tools'))}
    key = manager.resolve_key('cpu', julia_executable=sys.executable, environment=env)
    assert key['depots'] == [str(tmp_path / 'depot'), str(tmp_path / 'other')]
    assert key['environment']['JULIA_DEPOT_PATH'] == os.pathsep.join(key['depots'])
    assert all(Path(p).is_absolute() for p in key['environment']['PATH'].split(os.pathsep))


def test_stat_fingerprint_cache_invalidates_and_evicts_changed_client(engine_tree, monkeypatch):
    reads = []
    original = identity._file_digest

    def read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(identity, '_file_digest', read)
    identity._cached_digest.cache_clear()
    built = []

    class Worker:
        def __init__(self, **kwargs):
            self.retired = False
            built.append(self)

        def terminate(self):
            self.retired = True

    runtime = manager.WorkerManager(mode='child', engine_factory=Worker)
    options = dict(julia_executable=sys.executable, environment={})
    first = runtime.get_worker(**options)
    count = len(reads)
    assert runtime.get_worker(**options) is first
    assert len(reads) == count
    changed = engine_tree.root / 'solver.jl'
    changed.write_text('different bytes')
    second = runtime.get_worker(**options)
    assert reads[count:] == [changed]
    assert second is not first and first.worker.retired
    assert first not in runtime._clients.values()
    runtime.shutdown()


def test_compiled_request_policy_parameter_keys_selected_adapter(engine_tree, tmp_path):
    policy = tmp_path / 'policy.py'
    policy.write_text('adapter one')
    options = dict(julia_executable=sys.executable, environment={}, compiled_request_policy=policy)
    before = manager.resolve_key('cpu', **options)
    policy.write_text('adapter two')
    assert manager.resolve_key('cpu', **options)['runtime_fingerprint'] != before['runtime_fingerprint']


def test_stale_close_outside_global_lock_removes_client_even_when_close_raises(engine_tree, monkeypatch):
    class Worker:
        def __init__(self, **kwargs):
            pass

        def terminate(self):
            pass

    runtime = manager.WorkerManager(mode='child', engine_factory=Worker)
    options = dict(julia_executable=sys.executable, environment={})
    stale = runtime.get_worker(**options)
    stale.unusable = True
    entered, release = threading.Event(), threading.Event()

    def close(**kwargs):
        entered.set()
        assert release.wait(2)
        raise RuntimeError('old client close failed')

    monkeypatch.setattr(stale, 'close', close)
    errors = []

    def get():
        try:
            runtime.get_worker(**options)
        except OwnershipClosed as exc:
            errors.append(exc)

    thread = threading.Thread(target=get)
    thread.start()
    try:
        assert entered.wait(1)
        assert stale not in runtime._clients.values()
        started = time.monotonic()
        runtime.detach()
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and len(errors) == 1


def test_key_resolution_outside_global_lock_allows_quit(engine_tree, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    resolve = manager.resolve_key

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return resolve(*args, **kwargs)

    monkeypatch.setattr(manager, 'resolve_key', slow)
    runtime = manager.WorkerManager()
    errors = []

    def get():
        try:
            runtime.get_worker(julia_executable=sys.executable, environment={})
        except OwnershipClosed as exc:
            errors.append(exc)

    thread = threading.Thread(target=get)
    thread.start()
    try:
        assert entered.wait(1)
        started = time.monotonic()
        runtime.detach()
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and len(errors) == 1


def test_hosted_failed_terminal_retires_engine_before_successor(launch, monkeypatch):
    key, directory, _ = launch
    monkeypatch.setattr(manager, 'resolve_key', lambda *args, **kwargs: copy.deepcopy(key))

    class FailedEngine(EngineWorker):
        def submit(self, request, **kwargs):
            stream = super().submit(request, **kwargs)
            if stream.request['name'] == 'failed':
                class FailedStream:
                    def __next__(self):
                        stream.terminal = True
                        return {'type': 'failed', 'error': 'numerical failure'}

                    def close(self):
                        stream.close()
                return FailedStream()
            return stream

    with running_host(key, directory, worker=FailedEngine) as owner:
        runtime = manager.WorkerManager(directory=directory)
        for name in ('failed', 'successor'):
            with SolveSession() as session:
                session.submit(runtime.get_worker(), {'name': name})
                terminal = list(session.events())[-1]
                assert terminal['type'] == ('failed' if name == 'failed' else 'completed')
        lifecycle = [e['type'] for e in events(key)]
        assert lifecycle.count('started') == 2
        assert lifecycle.index('terminated') < len(lifecycle) - 1
        assert not owner._stopping.is_set()
        runtime.detach()


def test_hosted_startup_cancel_retires_fifo_and_waiter_retries_fresh_client(launch, monkeypatch):
    key, directory, _ = launch
    monkeypatch.setattr(manager, 'resolve_key', lambda *args, **kwargs: copy.deepcopy(key))
    entered, released, parked = threading.Event(), threading.Event(), threading.Event()

    class SlowEngine(EngineWorker):
        def ensure_started(self, **kwargs):
            if not released.is_set():
                entered.set()
                assert released.wait(2)
            super().ensure_started(**kwargs)

        def terminate(self):
            released.set()
            super().terminate()

    with running_host(key, directory, worker=SlowEngine) as owner:
        runtime = manager.WorkerManager(directory=directory)
        first = runtime.get_worker()
        errors, replies = [], []
        with SolveSession(cancel_grace_s=0) as abandoned:
            def start():
                try:
                    abandoned.submit(first, {'name': 'abandoned'})
                except BaseException as exc:
                    errors.append(exc)

            def next_solve():
                try:
                    with SolveSession(cancellation_callback=parked.set) as session:
                        session.submit(first, {'name': 'successor'})
                        replies.extend(session.events())
                except BaseException as exc:
                    errors.append(exc)

            a = threading.Thread(target=start)
            b = threading.Thread(target=next_solve)
            a.start()
            try:
                assert entered.wait(1)
                b.start()
                assert parked.wait(1)
                started = time.monotonic()
                abandoned.request_cancel()
                a.join(2)
                b.join(2)
                assert time.monotonic() - started < 3, (errors, replies, r.log_path(owner.identifier, directory).read_text())
                assert not a.is_alive() and not b.is_alive(), (errors, replies, r.log_path(owner.identifier, directory).read_text())
                assert replies[-1]['type'] == 'completed'
                assert len(errors) == 1 and 'cancel' in str(errors[0]).lower()
                assert first.unusable and runtime.get_worker() is not first
                assert not owner._stopping.is_set()
            finally:
                released.set()
                a.join(2)
                if b.ident is not None:
                    b.join(2)
        runtime.detach()


@pytest.mark.parametrize('error', [FileNotFoundError('missing Julia'), PermissionError('non-executable Julia')])
def test_cold_start_executable_failure_is_engine_failure_not_request_file(launch, error):
    key, directory, _ = launch

    class MissingJulia(EngineWorker):
        def ensure_started(self, **kwargs):
            raise error

    with running_host(key, directory, worker=MissingJulia) as owner:
        worker = HostedWorker(key, directory=directory)
        stream = worker.submit({'name': 'cold'})
        try:
            terminal = list(stream)[-1]
            assert owner._stopping.wait(1)
            from server.tests.beat_runtime.fake_host_worker import wait_until
            wait_until(lambda: 'Julia executable failed' in r.log_path(owner.identifier, directory).read_text())
            log = r.log_path(owner.identifier, directory).read_text()
            assert 'Julia executable failed' in log
            assert 'Invalid BEAT request file' not in log
            assert terminal['type'] == 'failed'
            assert 'Julia executable failed' in terminal['error']
        finally:
            stream.close()
            worker.detach()


@pytest.mark.parametrize('operation', ['shutdown', 'detach'])
def test_process_default_manager_stays_closed_for_good(monkeypatch, operation):
    runtime = manager.WorkerManager()
    monkeypatch.setattr(manager, '_default_manager', runtime)
    getattr(manager.get_manager(), operation)()
    assert manager.get_manager() is runtime
    with pytest.raises(OwnershipClosed, match='admission is closed'):
        manager.get_manager().get_worker()


def test_condemned_client_retry_respects_real_manager_shutdown(engine_tree):
    class Worker:
        def __init__(self, **kwargs):
            pass

        def terminate(self):
            pass

    runtime = manager.WorkerManager(mode='child', engine_factory=Worker)
    client = runtime.get_worker(julia_executable=sys.executable, environment={})
    client.unusable = True
    runtime.shutdown()
    with pytest.raises(OwnershipClosed, match='admission is closed'):
        client.acquire(lambda: None)
    assert not runtime._clients


def test_cancel_condemns_client_and_queued_solve_negotiates_fresh_worker(engine_tree):
    from server.tests.beat_runtime.test_session import Stream, Worker

    built = []
    parked = threading.Event()
    errors, replies, negotiated = [], [], []

    class FreshWorker(Worker):
        def __init__(self, **kwargs):
            super().__init__()
            built.append(self)
            self.worker_info = {'generation': len(built)}

        def submit(self, path, **kwargs):
            return Stream(self, json.loads(path.read_text()))

    runtime = manager.WorkerManager(mode='child', engine_factory=FreshWorker)
    first = runtime.get_worker(julia_executable=sys.executable, environment={})

    def queued():
        try:
            with SolveSession(cancellation_callback=parked.set) as session:
                session.submit(first, {}, negotiate=lambda info, *_: negotiated.append(info))
                replies.extend(session.events())
        except BaseException as exc:
            errors.append(exc)

    with SolveSession() as owner:
        owner.submit(first, {'close_error': True})
        waiter = threading.Thread(target=queued)
        waiter.start()
        try:
            assert parked.wait(1)
            with pytest.raises(OSError, match='closure failed'):
                owner._lease.cancel()
            waiter.join(2)
            assert not waiter.is_alive() and not errors
            assert first.unusable and len(built) == 2
            assert replies[-1]['type'] == 'completed'
            assert negotiated == [{'generation': 2}]
        finally:
            waiter.join(2)
    runtime.shutdown()


def test_unresponsive_hosted_startup_cancel_bounds_successor_wait_by_respawning(launch, tmp_path, monkeypatch):
    from server.tests.beat_runtime.fake_host_worker import wait_until

    key, directory, children = launch
    key['environment']['TEST_START_GATE'] = str(tmp_path / 'never')
    key['environment']['TEST_START_ONCE_MARKER'] = str(tmp_path / 'startup-entered')
    monkeypatch.setattr(manager, 'resolve_key', lambda *args, **kwargs: copy.deepcopy(key))
    runtime = manager.WorkerManager(directory=directory)
    first = runtime.get_worker()
    errors, replies = [], []
    with SolveSession(cancel_grace_s=0) as abandoned:
        def start():
            try:
                abandoned.submit(first, {'name': 'abandoned'})
            except BaseException as exc:
                errors.append(exc)

        def successor():
            try:
                with SolveSession() as session:
                    session.submit(first, {'name': 'successor'})
                    replies.extend(session.events())
            except BaseException as exc:
                errors.append(exc)

        a = threading.Thread(target=start)
        b = threading.Thread(target=successor)
        a.start()
        try:
            wait_until(lambda: Path(key['environment']['TEST_START_ONCE_MARKER']).exists())
            b.start()
            started = time.monotonic()
            abandoned.request_cancel()
            a.join(9)
            b.join(2)
            assert not a.is_alive() and not b.is_alive()
            assert time.monotonic() - started < 9
            assert replies and replies[-1]['type'] == 'completed', [str(error) for error in errors]
            assert len(errors) == 1 and 'cancel' in str(errors[0]).lower()
            assert len(children) == 2 and children[0].poll() is not None
        finally:
            (tmp_path / 'never').touch()
            a.join(2)
            if b.ident is not None:
                b.join(2)
    runtime.shutdown()


def test_retiring_host_admission_eof_retries_authenticated_connection(launch, monkeypatch):
    from server.solver.beat_runtime import client

    key, directory, _ = launch
    connect = client.connect_client
    attempts = []

    def retiring(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise client.HostConnectionClosed('retiring host closed after hello')
        return connect(*args, **kwargs)

    monkeypatch.setattr(client, 'connect_client', retiring)
    worker = HostedWorker(key, directory=directory)
    try:
        worker.ensure_started()
        assert len(attempts) == 2
    finally:
        worker.detach()
