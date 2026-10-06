from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import threading
import time

import pytest

from server.solver.beat_runtime import host, ipc, registry as r, spawn
from server.tests.beat_runtime.fake_host_worker import EngineWorker, events, wait_until
from server.tests.beat_runtime.test_host import authenticated


@pytest.mark.parametrize(('field', 'value'), [
    (field, value)
    for field in ['solver_script', 'julia_executable', 'julia_project', 'julia_sysimage']
    for value in ['relative/path', '']
    if value or field in {'solver_script', 'julia_executable'}
])
def test_absolute_launch_paths_required(launch, field, value):
    key, directory, children = launch
    key[field] = value
    with pytest.raises(r.RecordRefused):
        spawn.start_host(key, directory)
    assert not directory.exists()
    assert not children


def test_accept_refreshes_idle_window_after_successful_hello(launch):
    key, directory, children = launch
    record = spawn.start_host(key, directory, idle_timeout=0.5)
    time.sleep(0.3)
    with record.endpoint.connect(1) as connection:
        ipc.send_frame(connection, r.hello_message(record))
        assert ipc.receive_frame(connection)['type'] == 'hello_ok'
        time.sleep(0.3)
        assert children[0].returncode is None
        wait_until(lambda: children[0].returncode is not None)


def test_close_unpublishes_before_slow_engine_retirement(launch):
    key, directory, _ = launch
    retiring, release = threading.Event(), threading.Event()

    class SlowEngine(EngineWorker):
        def terminate(self):
            retiring.set()
            assert release.wait(2)
            super().terminate()

    owner = host.WorkerHost(key, directory, engine_factory=SlowEngine)
    with r.SpawnLock(r.spawn_lock_path(owner.identifier, directory)):
        record = owner.bind()
        r.write_record(record, directory)
    with ThreadPoolExecutor(1) as pool:
        closing = pool.submit(owner.close)
        try:
            assert retiring.wait(1)
            assert r.read_record(owner.identifier, directory) is None
            replacement = spawn.start_host(key, directory)
            assert replacement != record
        finally:
            release.set()
            closing.result(timeout=2)
    assert r.read_record(owner.identifier, directory) == replacement


def test_pending_peers_cannot_reserve_authenticated_capacity(launch):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    pending = []
    try:
        # Keep ownership of every socket even if a later connect fails.
        for _ in range(32):
            pending.append(record.endpoint.connect(1))
        with authenticated(record) as client:
            ipc.send_frame(client, {'op': 'ping'})
            assert ipc.receive_frame(client)['type'] == 'pong'
        # Even a peer that completed hello must prove client knowledge promptly.
        with record.endpoint.connect(1) as peer:
            ipc.send_frame(peer, r.hello_message(record))
            assert ipc.receive_frame(peer)['type'] == 'hello_ok'
            assert ipc.receive_frame(peer, deadline=time.monotonic() + 2)['type'] == 'hello_refused'
    finally:
        for peer in pending:
            peer.close()


def test_authenticated_capacity_is_enforced_at_admission(launch, monkeypatch):
    key, directory, _ = launch
    owner = host.WorkerHost(key, directory, engine_factory=EngineWorker)
    owner.bind()
    monkeypatch.setattr(host, 'MAX_CLIENTS', 1)
    serving = threading.Thread(target=owner.serve)
    serving.start()
    try:
        with authenticated(owner.record):
            with owner.record.endpoint.connect(1) as connection:
                ipc.send_frame(connection, r.hello_message(owner.record))
                hello = ipc.receive_frame(connection)
                ipc.send_frame(connection, {**r.host_key({}), 'op': 'authenticate', 'key': key,
                                           'key_id': owner.identifier, 'nonce': hello['client_nonce'],
                                           'proof': r.auth_proof(owner.record, hello['client_nonce'], 'client_auth')})
                refused = ipc.receive_frame(connection)
                assert refused['type'] == 'hello_refused'
                assert 'capacity' in refused['reason']
    finally:
        owner.close()
        serving.join(timeout=1)
    assert not serving.is_alive()


@pytest.mark.parametrize('persistent,idle_polls', [(False, False), (True, False), (True, True)])
def test_accept_oserror_retries_with_bounded_error_budget(launch, persistent, idle_polls):
    key, directory, _ = launch
    owner = host.WorkerHost(key, directory, idle_timeout=2 if idle_polls else 0.2,
                            engine_factory=EngineWorker)
    owner.bind()
    listener = owner._server

    class FaultyListener:
        calls = 0

        def settimeout(self, value):
            listener.settimeout(value)

        def accept(self):
            self.calls += 1
            if persistent and idle_polls and self.calls % 2 == 0:
                raise TimeoutError('healthy idle poll between errors')
            if persistent or self.calls == 1:
                if persistent and not idle_polls and self.calls == 1:
                    # Deterministically model slow error logging/scheduling
                    # crossing the idle deadline during the retry sequence.
                    owner._last_activity -= owner.idle_timeout
                raise OSError(errno.EINTR, 'fixture interrupted accept')
            return listener.accept()

        def close(self):
            listener.close()

    faulty = FaultyListener()
    owner._server = faulty
    serving = threading.Thread(target=owner.serve)
    serving.start()
    try:
        if persistent:
            serving.join(timeout=1)
            assert not serving.is_alive()
            expected_calls = host.ACCEPT_ERROR_BUDGET
            if idle_polls:
                expected_calls += host.ACCEPT_ERROR_BUDGET - 1
            assert faulty.calls == expected_calls
        else:
            with authenticated(owner.record) as client:
                ipc.send_frame(client, {'op': 'ping'})
                assert ipc.receive_frame(client)['type'] == 'pong'
    finally:
        owner.close()
        serving.join(timeout=1)
    assert 'accept failed' in r.log_path(owner.identifier, directory).read_text()


def test_accept_error_followed_by_healthy_idle_poll_preserves_idle_exit(launch):
    key, directory, _ = launch
    owner = host.WorkerHost(key, directory, idle_timeout=0.2, engine_factory=EngineWorker)
    owner.bind()
    listener = owner._server

    class RecoveringListener:
        calls = 0

        def settimeout(self, value):
            listener.settimeout(value)

        def accept(self):
            self.calls += 1
            if self.calls == 1:
                owner._last_activity -= owner.idle_timeout
                raise OSError(errno.EINTR, 'fixture interrupted accept')
            raise TimeoutError('healthy idle poll')

        def close(self):
            listener.close()

    recovering = RecoveringListener()
    owner._server = recovering
    serving = threading.Thread(target=owner.serve)
    serving.start()
    try:
        serving.join(timeout=1)
        assert not serving.is_alive()
        assert recovering.calls == 2
        assert 'idle exit' in r.log_path(owner.identifier, directory).read_text()
    finally:
        owner.close()
        serving.join(timeout=1)


def test_start_truncates_log_and_records_serving_idle_exit_and_failures(launch):
    key, directory, children = launch
    directory = r.private_directory(directory)
    log = r.log_path(r.key_id(key), directory)
    log.write_text('old run\n')
    log.chmod(0o600)
    record = spawn.start_host(key, directory, idle_timeout=0.3)
    with record.endpoint.connect(1) as connection:
        ipc.send_frame(connection, {'op': 'invalid'})
        assert ipc.receive_frame(connection)['type'] == 'hello_refused'
    wait_until(lambda: children[0].returncode is not None)
    contents = log.read_text()
    assert 'old run' not in contents
    assert 'serving' in contents
    assert 'idle exit' in contents
    assert 'connection failed: hello must come first' in contents
    key['environment']['TEST_FAIL'] = '1'
    with pytest.raises(RuntimeError):
        spawn.start_host(key, directory)
    assert 'host failed: fixture constructor failure' in r.log_path(r.key_id(key), directory).read_text()


def test_early_record_removal_failure_still_retires_engine(launch, monkeypatch):
    key, directory, _ = launch
    owner = host.WorkerHost(key, directory, engine_factory=EngineWorker)
    owner.bind()

    def refused():
        raise r.RecordRefused('fixture record removal failure')

    with monkeypatch.context() as failing:
        failing.setattr(owner, '_remove_own_record', refused)
        with pytest.raises(r.RecordRefused, match='record removal failure'):
            owner.close()
    assert [event['type'] for event in events(key)] == ['built', 'terminated']
    assert 'record removal failed' in r.log_path(owner.identifier, directory).read_text()
    owner.close()
