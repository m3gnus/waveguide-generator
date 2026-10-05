from __future__ import annotations

from contextlib import contextmanager
import errno
import json
from pathlib import Path
import socket
import threading
import time

import pytest

from server.solver.beat_runtime import client, cleanup, host, ipc, registry as r, spawn
from server.tests.beat_runtime.fake_host_worker import EngineWorker, events, wait_until
from server.tests.beat_runtime.test_host import authenticated
from server.tests.beat_runtime.test_host_submissions import submit, until


@contextmanager
def running_host(key, directory, *, worker=EngineWorker, owner_type=host.WorkerHost):
    owner = owner_type(key, directory, engine_factory=worker)
    with r.SpawnLock(r.spawn_lock_path(owner.identifier, directory)):
        record = owner.bind()
        r.write_record(record, directory)
    serving = threading.Thread(target=owner.serve)
    serving.start()
    try:
        yield owner
    finally:
        owner.close()
        serving.join(timeout=2)
        assert not serving.is_alive()


def test_transient_probe_timeout_adopts_slow_healthy_host_without_shutdown(launch, tmp_path, monkeypatch):
    key, directory, children = launch
    replies = []
    send = host.send_frame

    def sending(connection, message):
        replies.append(message['type'])
        send(connection, message)

    class SlowHost(host.WorkerHost):
        def _serve_connection(self, connection):
            time.sleep(0.3)  # Longer than the first recovery probe's 0.2 s.
            super()._serve_connection(connection)

    monkeypatch.setattr(host, 'send_frame', sending)
    with running_host(key, directory, owner_type=SlowHost) as owner:
        with authenticated(owner.record) as active:
            submit(active, 'other-client', release_path=str(tmp_path / 'release'))
            until(active, 'event')
            assert spawn.start_host(key, directory, timeout=2) == owner.record
            assert 'shutdown_ok' not in replies
            assert not owner._stopping.is_set()
            assert not children
            (tmp_path / 'release').touch()
            assert until(active, 'event')['event']['type'] == 'completed'


@pytest.mark.parametrize('identity', ['dead', 'reused'])
def test_prune_only_recovery_removes_proven_gone_host(launch, monkeypatch, identity):
    key, directory, children = launch
    stale = r.HostRecord(key, 12345, r.new_token(), ipc.Endpoint('tcp', port=1234), 'old-start')
    r.write_record(stale, directory)
    connect = cleanup.connect_authenticated
    operations = []

    def unavailable(record, *args, **kwargs):
        if record == stale:
            operations.append('hello')
            raise r.RecordRefused('fixture gone endpoint')
        return connect(record, *args, **kwargs)

    monkeypatch.setattr(cleanup, 'connect_authenticated', unavailable)
    monkeypatch.setattr(spawn, 'connect_authenticated', unavailable)
    monkeypatch.setattr(cleanup, 'pid_alive', lambda pid: identity != 'dead' if pid == stale.pid else r.pid_alive(pid))
    monkeypatch.setattr(cleanup, 'process_start_identity',
                        lambda pid: 'new-start' if pid == stale.pid else r.process_start_identity(pid))
    replacement = spawn.start_host(key, directory)
    assert replacement != stale
    assert operations == ['hello', 'hello']
    assert len(children) == 1


def test_prune_only_preserves_authenticatable_host_even_with_gone_pid_hint(launch, monkeypatch):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    with monkeypatch.context() as uncertain:
        uncertain.setattr(cleanup, '_same_process', lambda record: False)
        with pytest.raises(r.RecordRefused, match='Authenticatable host retained'):
            cleanup.cleanup_host(record, key, directory, prune_only=True)
    assert spawn.start_host(key, directory) == record
    assert r.read_record(record.identifier, directory) == record


@pytest.mark.parametrize('error', [ConnectionResetError(errno.ECONNRESET, 'pending-peer eviction'),
                                 ConnectionError(None, 'slow connect timed out')])
def test_transient_connection_failure_retries_live_host_without_shutdown(launch, monkeypatch, error):
    key, directory, children = launch
    record = spawn.start_host(key, directory)
    connect = spawn.connect_authenticated
    calls = 0

    def transient(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return connect(*args, **kwargs)

    monkeypatch.setattr(spawn, 'connect_authenticated', transient)
    assert spawn.start_host(key, directory, timeout=2) == record
    assert calls == 2
    assert len(children) == 1
    assert not any(e['type'] == 'terminated' for e in events(key))


def test_recovery_remaining_time_timeout_retains_live_host(launch, monkeypatch):
    key, directory, children = launch
    record = spawn.start_host(key, directory)
    remaining = spawn.remaining_time
    calls = 0

    def expiring(deadline):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise TimeoutError('fixture deadline race')
        return remaining(deadline)

    with monkeypatch.context() as expired:
        expired.setattr(spawn, 'remaining_time', expiring)
        with pytest.raises(r.RecordRefused, match='deadline'):
            spawn.start_host(key, directory, timeout=0.1)
    assert spawn.start_host(key, directory) == record
    assert len(children) == 1


@pytest.mark.parametrize('operation', ['submit', 'ensure_started'])
@pytest.mark.parametrize('disconnect', [True, False])
def test_cancel_during_cold_start_retains_fifo_until_stream_returns(launch, tmp_path, operation, disconnect):
    key, directory, children = launch
    gate = tmp_path / 'startup'
    key['environment']['TEST_START_GATE'] = str(gate)
    record = spawn.start_host(key, directory)
    first = authenticated(record)
    try:
        with authenticated(record) as second:
            ipc.send_frame(first, {'op': operation, 'request': {'name': 'abandoned'}})
            until(first, 'queued')
            until(first, 'status')
            submit(second, 'successor')
            if disconnect:
                first.shutdown(socket.SHUT_RDWR)
                first.close()
            else:
                ipc.send_frame(first, {'op': 'cancel'})
                until(first, 'cancelled')
            # Exceed the old five-second cancellation deadline while startup
            # is held. The successor's heartbeats prove admission stays alive.
            started = time.monotonic()
            while time.monotonic() - started <= host.RETIREMENT_TIMEOUT + 0.1:
                until(second, 'heartbeat')
            assert children[0].returncode is None
            assert not any(e['type'] == 'submitted' for e in events(key))
            gate.touch()
            assert until(second, 'event')['event']['name'] == 'successor'
            assert until(second, 'event')['event']['type'] == 'completed'
            ipc.send_frame(second, {'op': 'ping'})
            assert until(second, 'pong')['host_pid'] == record.pid
    finally:
        gate.touch()
        first.close()
    if operation == 'submit':
        lifecycle = [(e['type'], e.get('name')) for e in events(key)]
        assert lifecycle.index(('closed', 'abandoned')) < lifecycle.index(('submitted', 'successor'))


@pytest.mark.parametrize('failure', ['missing', 'unreadable', 'invalid_json', 'nonobject'])
def test_request_file_admission_failure_preserves_host(launch, tmp_path, monkeypatch, failure):
    key, directory, _ = launch
    request = tmp_path / 'request.json'
    if failure != 'missing':
        request.write_text('broken' if failure == 'invalid_json' else '[]' if failure == 'nonobject' else '{}')
    read = Path.read_text

    def reading(path, *args, **kwargs):
        if path == request and failure == 'unreadable':
            raise PermissionError('fixture request unreadable')
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', reading)
    with running_host(key, directory) as owner:
        worker = client.HostedWorker(key, directory=directory)
        try:
            with pytest.raises(client.HostError, match='Invalid BEAT request file'):
                worker.submit(request)
            assert not any(e['type'] == 'started' for e in events(key))
            assert list(worker.submit({'name': 'successor'}))[-1]['type'] == 'completed'
            assert worker.host_pid == owner.record.pid
            assert not owner._stopping.is_set()
        finally:
            worker.detach()


@pytest.mark.parametrize('error', [FileNotFoundError('removed'), PermissionError('unreadable'),
                                   json.JSONDecodeError('changed', '', 0)])
def test_request_file_changed_after_admission_fails_only_submission(launch, tmp_path, error):
    key, directory, _ = launch
    request = tmp_path / 'request.json'
    request.write_text('{"name":"raced"}')

    class RacedWorker(EngineWorker):
        def submit(self, request, **kwargs):
            if isinstance(request, Path):
                raise error
            return super().submit(request, **kwargs)

    with running_host(key, directory, worker=RacedWorker) as owner:
        worker = client.HostedWorker(key, directory=directory)
        try:
            assert list(worker.submit(request))[-1]['type'] == 'failed'
            assert list(worker.submit({'name': 'successor'}))[-1]['type'] == 'completed'
            assert worker.host_pid == owner.record.pid
            assert not owner._stopping.is_set()
        finally:
            worker.detach()


def test_retirement_deadline_allows_three_second_engine_termination(launch):
    key, directory, _ = launch

    class SlowRetirement(EngineWorker):
        def terminate(self):
            if self.worker_info is not None:
                time.sleep(3)
            super().terminate()

    with running_host(key, directory, worker=SlowRetirement) as owner:
        worker = client.HostedWorker(key, directory=directory)
        try:
            worker.ensure_started()
            assert worker.terminate() is True
            assert worker.ping()['host_pid'] == owner.record.pid
        finally:
            worker.detach()


def test_platform_native_launch_fixture_paths_are_absolute(launch, tmp_path):
    key, _, _ = launch
    for name in ('julia_executable', 'solver_script', 'julia_project', 'julia_sysimage'):
        assert Path(key[name]).is_absolute()
        assert tmp_path in Path(key[name]).parents


def test_explicit_cancel_unexpected_close_error_retires_engine_and_serves_successor(launch, tmp_path):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    with authenticated(record) as first, authenticated(record) as second:
        submit(first, 'cancelled', release_path=str(tmp_path / 'never'), close_unexpected_error=True)
        until(first, 'event')
        wait_until(lambda: any(e['type'] == 'reading' for e in events(key)))
        submit(second, 'successor')
        ipc.send_frame(first, {'op': 'cancel'})
        until(first, 'cancelled')
        assert until(second, 'event')['event']['name'] == 'successor'
        assert until(second, 'event')['event']['type'] == 'completed'
    assert spawn.start_host(key, directory).pid == record.pid
    lifecycle = [(e['type'], e.get('name')) for e in events(key)]
    assert lifecycle.index(('terminated', None)) < lifecycle.index(('submitted', 'successor'))
    assert 'unexpected retirement failure' in r.log_path(record.identifier, directory).read_text()


def test_stream_send_timeout_survives_reader_stall_beyond_control_deadline(launch):
    key, directory, _ = launch

    class SmallSendBufferHost(host.WorkerHost):
        def _serve_connection(self, connection):
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            super()._serve_connection(connection)

    with running_host(key, directory, owner_type=SmallSendBufferHost) as owner:
        with authenticated(owner.record) as connection:
            submit(connection, 'slow-reader', payload_size=2 * ipc.CONTROL_FRAME_BYTES)
            until(connection, 'worker_info')
            time.sleep(host.CONTROL_TIMEOUT + 0.3)
            assert until(connection, 'event')['event']['name'] == 'slow-reader'
            assert until(connection, 'event')['event']['type'] == 'completed'
            assert not owner._stopping.is_set()


def test_authenticate_gets_fresh_deadline_after_slow_hello(launch):
    key, directory, _ = launch
    with running_host(key, directory) as owner:
        with owner.record.endpoint.connect(2) as connection:
            time.sleep(host.PREAUTH_TIMEOUT * 0.7)
            ipc.send_frame(connection, r.hello_message(owner.record))
            hello = ipc.receive_frame(connection)
            time.sleep(host.PREAUTH_TIMEOUT * 0.7)
            ipc.send_frame(connection, {**r.host_key({}), 'op': 'authenticate', 'key': key,
                                       'key_id': owner.identifier, 'nonce': hello['client_nonce'],
                                       'proof': r.auth_proof(owner.record, hello['client_nonce'], 'client_auth')})
            assert ipc.receive_frame(connection)['type'] == 'authenticated'


def test_oversized_inline_request_refused_before_connection(launch, monkeypatch):
    key, directory, children = launch
    worker = client.HostedWorker(key, directory=directory)
    monkeypatch.setattr(worker, 'adopt', lambda: pytest.fail('Oversized request connected'))
    try:
        with pytest.raises(client.HostError, match='1 MiB.*stage'):
            worker.submit({'name': 'large', 'payload': 'x' * ipc.CONTROL_FRAME_BYTES})
        assert not children
        assert not events(key)
    finally:
        worker.detach()


def test_respawn_adoption_records_and_exposes_engine_replacement(launch):
    key, directory, children = launch
    worker = client.HostedWorker(key, directory=directory)
    try:
        worker.ensure_started()
        old = worker.adopt()
        assert old['engine_replaced'] is False
        children[0].terminate()  # Only the test's recorded Popen.
        children[0].wait(timeout=3)
        with pytest.raises((client.HostError, OSError)):
            worker.ping()
        replacement = worker.adopt()
        assert replacement['host_pid'] != old['host_pid']
        assert replacement['worker_instance'] != old['worker_instance']
        assert replacement['engine_replaced'] is True
        assert worker.engine_replaced is True
        assert worker.worker_info is None
        worker.ensure_started()
        assert worker.adopt()['engine_replaced'] is True
    finally:
        worker.detach()


def test_worker_instance_report_change_records_engine_replacement(launch):
    key, directory, _ = launch
    worker = client.HostedWorker(key, directory=directory)
    try:
        report = worker.adopt()
        worker._report({**report, 'worker_instance': r.new_token()})
        assert worker.engine_replaced is True
    finally:
        worker.detach()


@pytest.mark.parametrize('field', ['julia_project', 'julia_sysimage'])
def test_empty_optional_launch_path_normalizes_to_unset(launch, field):
    key, directory, _ = launch
    key[field] = ''
    expected = host.validate_key(key)
    assert expected[field] is None
    assert key[field] == ''  # Validation does not mutate the caller's identity.
    worker = client.HostedWorker(key, directory=directory)
    try:
        assert worker.key[field] is None
        worker.ensure_started()
        built = next(e for e in events(key) if e['type'] == 'built')
        assert built['kwargs'][field] is None
        assert spawn.start_host({**key, field: None}, directory).pid == worker.host_pid
    finally:
        worker.detach()
