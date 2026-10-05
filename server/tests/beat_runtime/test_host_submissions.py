from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from server.solver.beat_runtime import client, ipc, registry as r, spawn
from server.tests.beat_runtime.fake_host_worker import events, wait_until
from server.tests.beat_runtime.test_host import authenticated


def until(connection, kind):
    while True:
        frame = ipc.receive_frame(connection, max_bytes=ipc.MAX_FRAME_BYTES,
                                  deadline=time.monotonic() + 3)
        assert frame is not None
        if frame['type'] == kind:
            return frame


def submit(connection, name, **kwargs):
    ipc.send_frame(connection, {'op': 'submit', 'request': {'name': name, **kwargs}})
    return until(connection, 'queued')['sequence']


def test_fifo_two_clients_serialize_and_keep_serving(launch, tmp_path):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    # Two applications can have overlapping connections; host receipt order
    # (the queued sequence) determines startup/submission admission order.
    with authenticated(record) as first, authenticated(record) as second, authenticated(record) as third:
        a = submit(first, 'a', release_path=str(tmp_path / 'a'))
        assert until(first, 'event')['event']['name'] == 'a'
        b = submit(second, 'b', release_path=str(tmp_path / 'b'))
        c = submit(third, 'c')
        assert a < b < c
        assert [e['name'] for e in events(key) if e['type'] == 'submitted'] == ['a']
        (tmp_path / 'a').touch()
        assert until(first, 'event')['event']['type'] == 'completed'
        assert until(second, 'event')['event']['name'] == 'b'
        assert [e['name'] for e in events(key) if e['type'] == 'submitted'] == ['a', 'b']
        (tmp_path / 'b').touch()
        assert until(second, 'event')['event']['type'] == 'completed'
        assert until(third, 'event')['event']['name'] == 'c'
        assert until(third, 'event')['event']['type'] == 'completed'
        ipc.send_frame(first, {'op': 'ping'})
        assert until(first, 'pong')['host_pid'] == record.pid
    lifecycle = [(e['type'], e.get('name')) for e in events(key) if 'name' in e]
    assert lifecycle.index(('closed', 'a')) < lifecycle.index(('submitted', 'b'))
    assert lifecycle.index(('closed', 'b')) < lifecycle.index(('submitted', 'c'))


@pytest.mark.parametrize('disconnect', [True, False])
def test_blocked_read_disconnect_or_cancel_retires_before_successor(launch, tmp_path, disconnect):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    first = authenticated(record)
    try:
        submit(first, 'abandoned', release_path=str(tmp_path / 'never'))
        until(first, 'event')
        wait_until(lambda: any(e['type'] == 'reading' for e in events(key)))
        with authenticated(record) as second:
            submit(second, 'next')
            if disconnect:
                first.shutdown(2)
                first.close()
            else:
                ipc.send_frame(first, {'op': 'cancel'})
                assert until(first, 'cancelled')['type'] == 'cancelled'
            assert until(second, 'event')['event']['name'] == 'next'
            assert until(second, 'event')['event']['type'] == 'completed'
    finally:
        first.close()
    lifecycle = [(e['type'], e.get('name')) for e in events(key)]
    assert lifecycle.index(('closed', 'abandoned')) < lifecycle.index(('submitted', 'next'))
    assert spawn.start_host(key, directory).pid == record.pid


def test_queued_disconnect_never_reaches_engine(launch, tmp_path):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    with authenticated(record) as first, authenticated(record) as queued:
        submit(first, 'active', release_path=str(tmp_path / 'release'))
        until(first, 'event')
        submit(queued, 'withdrawn')
        ipc.send_frame(queued, {'op': 'cancel'})
        until(queued, 'cancelled')
        (tmp_path / 'release').touch()
        until(first, 'event')
    assert [e['name'] for e in events(key) if e['type'] == 'submitted'] == ['active']


@pytest.mark.parametrize('failure', ['close_error', 'close_hang'])
def test_retirement_failure_exits_and_refuses_queued_work(launch, tmp_path, failure):
    key, directory, children = launch
    record = spawn.start_host(key, directory)
    first, second = authenticated(record), authenticated(record)
    try:
        submit(first, 'broken', release_path=str(tmp_path / 'never'), **{failure: True})
        until(first, 'event')
        submit(second, 'must-not-run')
        started = time.monotonic()
        first.shutdown(2)
        first.close()
        wait_until(lambda: children[0].returncode is not None, timeout=12)
        assert time.monotonic() - started < 12
        assert children[0].returncode == 0
        while (frame := ipc.receive_frame(second)) is not None:
            assert frame['type'] == 'heartbeat'
        assert r.read_record(record.identifier, directory) is None
        assert [e['name'] for e in events(key) if e['type'] == 'submitted'] == ['broken']
    finally:
        first.close()
        second.close()


def test_idle_engine_termination_hang_is_bounded(launch):
    key, directory, children = launch
    key['environment']['TEST_TERMINATE_HANG'] = '1'
    record = spawn.start_host(key, directory, idle_timeout=0.1)
    wait_until(lambda: children[0].returncode is not None, timeout=8)
    assert children[0].returncode == 0
    assert r.read_record(record.identifier, directory) is None
    assert events(key)[-1]['type'] == 'termination_hung'


def test_adoption_from_second_wg_process_preserves_provable_engine_identity(launch):
    key, directory, children = launch
    first = client.HostedWorker(key, directory=directory)
    try:
        first.ensure_started()
        report = first.adopt()
        code = '''
import json, sys
from pathlib import Path
from server.solver.beat_runtime.client import HostedWorker
worker = HostedWorker(json.loads(sys.argv[1]), directory=Path(sys.argv[2]))
worker.ensure_started()
print(json.dumps(worker.adopt()))
worker.detach()
'''
        result = subprocess.run([sys.executable, '-c', code, json.dumps(key), str(directory)],
                                capture_output=True, text=True, timeout=5, check=True)
        adopted = json.loads(result.stdout)
        assert adopted == report
        assert adopted['host_pid'] == children[0].pid
        assert adopted['engine_pid'] is None  # Official API cannot prove Julia PID continuity.
        assert adopted['worker_instance']
        assert sum(e['type'] == 'started' for e in events(key)) == 1
        assert len(children) == 1
    finally:
        first.detach()


def test_stream_and_startup_heartbeats_have_no_fixed_solve_deadline(launch, tmp_path, monkeypatch):
    key, directory, _ = launch
    gate = tmp_path / 'startup'
    key['environment']['TEST_START_GATE'] = str(gate)
    record = spawn.start_host(key, directory)
    with authenticated(record) as connection:
        ipc.send_frame(connection, {'op': 'ensure_started'})
        until(connection, 'queued')
        until(connection, 'status')
        # A startup longer than one control deadline remains live via heartbeats.
        start = time.monotonic()
        while time.monotonic() - start < 2.1:
            until(connection, 'heartbeat')
        gate.touch()
        until(connection, 'ready')
    monkeypatch.setattr(client, 'HEARTBEAT_TIMEOUT', 0.8)
    worker = client.HostedWorker(key, directory=directory)
    try:
        stream = worker.submit({'name': 'slow', 'release_path': str(tmp_path / 'solve')})
        assert next(stream)['type'] == 'result'
        # Engine reader blocks longer than the client's liveness interval.
        import threading

        timer = threading.Timer(1.6, lambda: (tmp_path / 'solve').touch())
        timer.start()
        try:
            assert next(stream)['type'] == 'completed'
        finally:
            timer.join(timeout=2)
    finally:
        worker.detach()


def test_engine_read_failure_cannot_hide_internal_retirement_failure(launch):
    key, directory, children = launch
    worker = client.HostedWorker(key, directory=directory)
    try:
        stream = worker.submit({'name': 'reader-failure', 'read_error': True, 'close_error': True})
        assert next(stream)['type'] == 'result'
        # Depending on shutdown timing, the failure frame or the closed
        # authenticated connection reports the error. Neither admits a successor.
        try:
            assert next(stream)['type'] == 'failed'
        except (RuntimeError, OSError):
            pass
        wait_until(lambda: children[0].returncode is not None)
        assert children[0].returncode == 0
        assert [e['name'] for e in events(key) if e['type'] == 'submitted'] == ['reader-failure']
    finally:
        worker.detach()


def test_authenticated_shutdown_wakes_queued_and_blocked_clients(launch, tmp_path):
    key, directory, children = launch
    first, second = client.HostedWorker(key, directory=directory), client.HostedWorker(key, directory=directory)
    try:
        active = first.submit({'name': 'active', 'release_path': str(tmp_path / 'never')})
        assert next(active)['type'] == 'result'
        queued = second.submit({'name': 'must-not-run'})
        from server.solver.beat_runtime.cleanup import cleanup_host

        assert cleanup_host(r.read_record(r.key_id(key), directory), key, directory, timeout=3)
        wait_until(lambda: children[0].returncode is not None)
        for stream in (active, queued):
            with pytest.raises((RuntimeError, OSError)):
                next(stream)
        assert [e['name'] for e in events(key) if e['type'] == 'submitted'] == ['active']
    finally:
        first.detach()
        second.detach()
