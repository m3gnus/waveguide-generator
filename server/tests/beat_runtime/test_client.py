from __future__ import annotations

import json
import os
import struct
import threading
import time

import pytest

from server.solver.beat_runtime import client, ipc, registry as r, spawn
from server.tests.beat_runtime.fake_host_worker import events, wait_until
from server.tests.beat_runtime.test_host import authenticated


@pytest.fixture
def clients(launch):
    key, directory, _ = launch
    first = client.HostedWorker(key, directory=directory)
    second = client.HostedWorker(key, directory=directory)
    yield first, second
    first.detach()
    second.detach()


def test_file_inline_operation_metadata_and_reuse(clients, launch, tmp_path):
    first, second = clients
    key, _, _ = launch
    statuses = []
    request = tmp_path / 'request.json'
    request.write_text('{"name":"file","payload_size":1048577}')
    stream = first.submit(request, status_callback=statuses.append)
    result = next(stream)
    assert result['name'] == 'file'
    assert len(result['payload']) > ipc.CONTROL_FRAME_BYTES
    assert next(stream)['type'] == 'completed'
    assert statuses
    assert first.worker_info['protocol'] == {'name': 'beat-worker', 'version': 1}
    info = first.worker_info
    info['engine']['name'] = 'changed'
    assert first.worker_info['engine']['name'] == 'BEAT Engine'
    assert list(second.submit({'name': 'inline'}, operation='bem_field'))[-1]['type'] == 'completed'
    stream.close()  # Stale stream cannot cancel the later operation.
    assert first.ping()['host_pid'] == second.host_pid
    assert [e['operation'] for e in events(key) if e['type'] == 'submitted'] == ['solve', 'bem_field']


@pytest.mark.parametrize('when', ['unread', 'reading', 'callback'])
def test_client_close_or_callback_failure_recovers(clients, launch, tmp_path, when):
    first, second = clients
    key, _, _ = launch

    def broken_status(message):
        raise ValueError('application callback failed')

    stream = first.submit({'name': 'abandoned', 'release_path': str(tmp_path / 'never')},
                          status_callback=broken_status if when == 'callback' else None)
    if when == 'reading':
        assert next(stream)['type'] == 'result'
        wait_until(lambda: any(e['type'] == 'reading' for e in events(key)))
    if when == 'callback':
        with pytest.raises(ValueError, match='application callback failed'):
            next(stream)
    else:
        stream.close()
    assert list(second.submit({'name': 'successor'}))[-1]['type'] == 'completed'
    stream.cancel()
    assert list(first.submit({'name': 'later'}))[-1]['type'] == 'completed'


def test_idle_retire_declines_another_clients_solve(clients, tmp_path):
    first, second = clients
    stream = first.submit({'name': 'active', 'release_path': str(tmp_path / 'release')})
    next(stream)
    assert second.terminate() is False
    (tmp_path / 'release').touch()
    assert next(stream)['type'] == 'completed'
    assert second.terminate() is True
    assert list(first.submit({'name': 'fresh'}))[-1]['type'] == 'completed'


def test_detach_retires_active_stream_and_ends_local_admission(clients, launch, tmp_path):
    first, second = clients
    _, _, children = launch
    stream = first.submit({'name': 'active', 'release_path': str(tmp_path / 'never')})
    next(stream)
    first.detach()
    assert children[0].returncode is None
    with pytest.raises(RuntimeError, match='closed'):
        first.submit({'name': 'refused'})
    assert list(second.submit({'name': 'other'}))[-1]['type'] == 'completed'
    second.shutdown()
    wait_until(lambda: children[0].returncode is not None)


def test_disconnect_during_partial_control_frame_retires_stream(launch, tmp_path):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    first = authenticated(record)
    try:
        ipc.send_frame(first, {'op': 'submit', 'request': {'name': 'partial',
                                                       'release_path': str(tmp_path / 'never')}})
        while ipc.receive_frame(first)['type'] != 'event':
            pass
        first.sendall(struct.pack('!I', 20) + b'{')
        # The total control-frame deadline bounds a partial cancellation too.
        wait_until(lambda: any(e['type'] == 'closed' for e in events(key)), timeout=4)
    finally:
        first.close()
    second = client.HostedWorker(key, directory=directory)
    try:
        assert list(second.submit({'name': 'next'}))[-1]['type'] == 'completed'
    finally:
        second.detach()


def test_client_refuses_wrong_admission_proof(launch, monkeypatch):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    receive = client.receive_frame

    def corrupt(*args, **kwargs):
        frame = receive(*args, **kwargs)
        if frame['type'] == 'authenticated':
            frame['proof'] = '0' * 64
        return frame

    monkeypatch.setattr(client, 'receive_frame', corrupt)
    with pytest.raises(r.RecordRefused, match='admission proof'):
        client.connect_client(record, directory)


@pytest.mark.parametrize('bad_request', [None, '', 'relative.json', 1, []])
def test_bad_submission_keeps_authenticated_host_available(launch, bad_request):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    with authenticated(record) as connection:
        ipc.send_frame(connection, {'op': 'submit', 'request': bad_request})
        assert ipc.receive_frame(connection)['type'] == 'failed'
        ipc.send_frame(connection, {'op': 'ping'})
        assert ipc.receive_frame(connection)['host_pid'] == record.pid
    assert not any(e['type'] == 'started' for e in events(key))


def test_two_public_clients_serialize_submissions(clients, launch, tmp_path):
    first, second = clients
    key, _, _ = launch
    active = first.submit({'name': 'first', 'release_path': str(tmp_path / 'release')})
    assert next(active)['name'] == 'first'
    queued = second.submit({'name': 'second'})  # Returns only after the host's queued acknowledgement.
    assert [e['name'] for e in events(key) if e['type'] == 'submitted'] == ['first']
    (tmp_path / 'release').touch()
    assert next(active)['type'] == 'completed'
    assert next(queued)['name'] == 'second'
    assert next(queued)['type'] == 'completed'
    assert first.host_pid == second.host_pid
    lifecycle = [(e['type'], e.get('name')) for e in events(key)]
    assert lifecycle.index(('closed', 'first')) < lifecycle.index(('submitted', 'second'))


@pytest.mark.parametrize("phase", ["hello", "authenticate", "ensure_started", "submit"])
def test_detach_polls_cancellation_during_handshake_control_and_admission(launch, monkeypatch, phase):
    key, directory, _ = launch
    record = r.HostRecord(key, os.getpid(), r.new_token(), ipc.Endpoint("tcp", port=12345))
    worker = client.HostedWorker(key, directory=directory)
    entered = threading.Event()
    errors = []
    connections = []

    class SilentConnection:
        # Model platforms where socket closure does not wake a blocked recv.
        def __init__(self):
            self.message = None
            self.closed = False
            connections.append(self)

        def sendall(self, data):
            self.message = json.loads(data[4:])

        def settimeout(self, timeout):
            self.timeout = timeout

        def recv(self, count):
            entered.set()
            threading.Event().wait(self.timeout)
            raise TimeoutError("silent host")

        def shutdown(self, how):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(client, "start_host", lambda *args, **kwargs: record)
    monkeypatch.setattr(ipc.Endpoint, "connect", lambda *args: SilentConnection())

    def receive(connection, **kwargs):
        message = connection.message
        op = message["op"]
        if op == phase:
            return ipc.receive_frame(connection, **kwargs)
        if op == "hello":
            return {**r.auth_reply(record, message), "client_nonce": record.token}
        if op == "authenticate":
            return {"type": "authenticated", "nonce": message["nonce"],
                    "proof": r.auth_proof(record, message["nonce"], "client_auth_ok")}
        assert op == "adopt"
        return {"type": "adopted", "host_pid": record.pid, "worker_instance": "fixture",
                "worker_info": None}

    monkeypatch.setattr(client, "receive_frame", receive)

    def work():
        try:
            if phase == "submit":
                worker.submit({"name": "unadmitted"})
            else:
                worker.ensure_started()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    try:
        assert entered.wait(1)
        started = time.monotonic()
        worker.detach()
        thread.join(1)
        assert not thread.is_alive() and time.monotonic() - started < 1
        assert len(errors) == 1 and isinstance(errors[0], client.HostError)
        assert all(connection.closed for connection in connections)
    finally:
        worker.detach()
        thread.join(1)


def test_cancelled_remote_stream_raises_host_error_not_oserror(clients, monkeypatch):
    worker, _ = clients
    stream = worker.submit({"name": "cancelled"})

    def aborted(*args, **kwargs):
        raise ConnectionAbortedError("cancelled during receive")

    monkeypatch.setattr(client, "receive_frame", aborted)
    with pytest.raises(client.HostError, match="receive cancelled"):
        next(stream)



def test_startup_eof_recovery_is_bounded_and_does_not_retry_host_failures(launch, monkeypatch):
    key, directory, _ = launch
    worker = client.HostedWorker(key, directory=directory)
    calls = []

    def unavailable(operation, *args, **kwargs):
        calls.append(operation)
        raise client.HostConnectionClosed("retiring startup host")

    monkeypatch.setattr(worker, "_request", unavailable)
    with pytest.raises(client.HostError, match="did not reopen"):
        worker.ensure_started()
    assert calls == ["ensure_started", "ensure_started"]
    assert worker._startup_done.is_set()

    def failed(*args, **kwargs):
        raise client.HostError("engine refused startup")

    monkeypatch.setattr(worker, "_request", failed)
    with pytest.raises(client.HostError, match="engine refused startup"):
        worker.ensure_started()
