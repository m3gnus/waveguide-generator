from __future__ import annotations

import builtins
import ctypes
import errno
import os
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from server.solver.beat_runtime import cleanup, host, ipc, registry as r, spawn


def test_production_spawn_ignores_test_worker_environment(launch, monkeypatch):
    key, directory, children = launch
    monkeypatch.setattr(spawn, 'HOST_MODULE', 'server.solver.beat_runtime.host')
    monkeypatch.setenv('WG2_BEAT_TEST_WORKER', 'builtins:dict')
    monkeypatch.setenv('WG2_BEAT_TEST_OTHER', 'injected')
    observed = []
    original_import = builtins.__import__
    original_popen = spawn.subprocess.Popen

    class RealFactoryReached(Exception):
        pass

    def sentinel(**kwargs):
        observed.append(kwargs)
        raise RealFactoryReached

    def importing(name, *args, **kwargs):
        if name == 'beat_engine':
            return SimpleNamespace(EngineWorker=sentinel)
        return original_import(name, *args, **kwargs)

    def launching(command, **options):
        if command[0] != sys.executable:
            return original_popen(command, **options)
        assert command[2] == 'server.solver.beat_runtime.host'
        assert not any(name.upper().startswith('WG2_BEAT_TEST_') for name in options['env'])
        # Run the selected production entry with an import sentinel; the optional
        # engine is never loaded, and the environment cannot select builtins.dict.
        host.main(command[3:])

    monkeypatch.setattr(builtins, '__import__', importing)
    monkeypatch.setattr(spawn.subprocess, 'Popen', launching)
    with pytest.raises(RealFactoryReached):
        spawn.start_host(key, directory)
    assert observed[0]['julia_threads'] == key['julia_threads']
    assert not children
    assert 'beat_engine' not in sys.modules


def test_reused_tcp_port_authentication_refusal_prunes_only_gone_host(launch):
    key, directory, children = launch
    directory = r.private_directory(directory)
    stopping = threading.Event()
    answered = []
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(0.1)
        endpoint = ipc.Endpoint('tcp', port=listener.getsockname()[1])
        stale = r.HostRecord(key, os.getpid(), 'stale', endpoint, 'reused-start')
        r.write_record(stale, directory)

        def answer():
            while not stopping.is_set():
                try:
                    peer, _ = listener.accept()
                except socket.timeout:
                    continue
                with peer:
                    message = ipc.receive_frame(peer, deadline=time.monotonic() + 1)
                    answered.append(message['op'])
                    ipc.send_frame(peer, {'type': 'hello_refused'})

        thread = threading.Thread(target=answer)
        thread.start()
        try:
            record = spawn.start_host(key, directory, timeout=2)
            assert record != stale
            assert len(children) == 1
            assert answered == ['hello', 'hello']
            assert listener.fileno() >= 0  # The other listener was never shut down.
        finally:
            stopping.set()
            thread.join(timeout=1)
        assert not thread.is_alive()


@pytest.mark.parametrize('refusal', ['authentication_eof', 'connection_refused'])
def test_closing_live_host_refusal_retries_within_start_deadline(launch, monkeypatch, refusal):
    key, directory, children = launch
    key['environment']['TEST_TERMINATE_DELAY'] = '0.3'
    old = spawn.start_host(key, directory)
    original = spawn.connect_authenticated
    cleanup_original = spawn.cleanup_host
    refusals, cleanups = [], []

    def connecting(record, *args, **kwargs):
        if record == old:
            if not refusals:
                refusals.append(record)
                children[0].terminate()  # This test's owned Popen, never a record PID.
            # Model an endpoint already refusing admission until the process
            # exits. SIGTERM delivery alone can race a successful live probe.
            if refusal == 'authentication_eof':
                raise r.RecordRefused('Host closed before authentication')
            raise ConnectionError(errno.ECONNREFUSED, 'fixture closing listener')
        return original(record, *args, **kwargs)

    def cleaning(*args, **kwargs):
        assert kwargs['lock'].held
        assert kwargs['prune_only'] is True
        cleanups.append(args[0])
        return cleanup_original(*args, **kwargs)

    monkeypatch.setattr(spawn, 'connect_authenticated', connecting)
    monkeypatch.setattr(spawn, 'cleanup_host', cleaning)
    started = time.monotonic()
    replacement = spawn.start_host(key, directory, timeout=2)
    assert replacement.pid != old.pid
    assert cleanups and all(record == old for record in cleanups)
    assert time.monotonic() - started < 2
    assert children[0].returncode is not None
    assert len(children) == 2


def test_empty_interpreter_reports_clear_launch_error(launch, monkeypatch):
    key, directory, children = launch
    monkeypatch.setattr(sys, 'executable', '')
    with pytest.raises(RuntimeError, match='sys.executable is empty'):
        spawn.start_host(key, directory)
    assert not children
    assert r.read_record(r.key_id(key), directory) is None


@pytest.mark.parametrize('owned', [False, True])
def test_failed_bootstrap_kills_verified_interpreter_reaps_stub_and_cleans_record(launch, monkeypatch, owned):
    key, directory, _ = launch
    actions = []
    child_pid, host_pid = 101, 102
    record = r.HostRecord(key, host_pid, 'secret', ipc.Endpoint('tcp', port=1234), 'host-start')
    raw = {'record': record.as_dict(), 'launcher_pid': child_pid if owned else 999,
           'launcher_start': 'launcher-start'}
    original_error = r.RecordRefused('fixture original authentication failure')
    live = {child_pid, host_pid}

    class Stub:
        pid = child_pid

        def poll(self):
            return None

        def terminate(self):
            actions.append('terminate_stub')

        def wait(self, timeout):
            actions.append('wait_stub')
            if child_pid in live:
                raise subprocess.TimeoutExpired('stub', timeout)
            return 0

        def kill(self):
            actions.append('kill_stub')
            live.discard(child_pid)

    def launching(*args):
        r._atomic_json(host.ready_path(record.identifier, directory), raw)
        return Stub()

    def terminate_interpreter(candidate):
        assert candidate == record
        actions.append('terminate_interpreter')
        live.discard(host_pid)

    def refusing(*args, **kwargs):
        raise original_error

    monkeypatch.setattr(spawn, '_launch', launching)
    monkeypatch.setattr(spawn, '_terminate_owned_interpreter', terminate_interpreter)
    monkeypatch.setattr(spawn, 'connect_authenticated', refusing)
    monkeypatch.setattr(cleanup, 'connect_authenticated', refusing)
    monkeypatch.setattr(cleanup, 'pid_alive', lambda pid: pid in live)
    monkeypatch.setattr(r, 'process_start_identity',
                        lambda pid: 'host-start' if pid == host_pid else 'launcher-start')
    if owned:
        with pytest.raises(r.RecordRefused) as raised:
            spawn.start_host(key, directory)
        assert raised.value is original_error
        assert actions == ['terminate_interpreter', 'terminate_stub', 'wait_stub', 'kill_stub', 'wait_stub']
    else:
        with pytest.raises(r.RecordRefused, match='process identity mismatch'):
            spawn.start_host(key, directory)
        assert 'terminate_interpreter' not in actions
    assert r.read_record(record.identifier, directory) is None
    assert not host.ready_path(record.identifier, directory).exists()


@pytest.mark.parametrize('creation', [123, 456, None])
def test_owned_windows_interpreter_termination_verifies_creation_on_same_handle(launch, monkeypatch, creation):
    key, _, _ = launch
    record = r.HostRecord(key, 101, 'secret', ipc.Endpoint('tcp', port=1234), 'windows:123')
    calls = []

    class Function:
        def __init__(self, action):
            self.action = action

        def __call__(self, *args):
            return self.action(*args)

    def get_times(handle, created, *rest):
        calls.append(('times', handle))
        if creation is None:
            return False
        created._obj.dwLowDateTime = creation
        created._obj.dwHighDateTime = 0
        return True

    kernel = SimpleNamespace(
        OpenProcess=Function(lambda access, inherit, pid: calls.append(('open', pid)) or 99),
        GetProcessTimes=Function(get_times),
        TerminateProcess=Function(lambda handle, code: calls.append(('terminate', handle)) or True),
        WaitForSingleObject=Function(lambda handle, timeout: calls.append(('wait', handle)) or 0),
        CloseHandle=Function(lambda handle: calls.append(('close', handle)) or True),
    )
    with monkeypatch.context() as windows:
        windows.setattr(spawn, 'os', SimpleNamespace(name='nt'))
        windows.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: kernel, raising=False)
        spawn._terminate_owned_interpreter(record)
    assert calls[0] == ('open', 101)
    assert calls[-1] == ('close', 99)
    assert (('terminate', 99) in calls) == (creation == 123)
    assert (('wait', 99) in calls) == (creation == 123)
