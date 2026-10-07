from types import SimpleNamespace

from server.solver.beat_runtime import host, ipc, registry as r
from server.tests.beat_runtime.fake_host_worker import EngineWorker


def test_host_expires_on_first_poll_after_suspend(launch, monkeypatch):
    key, directory, _ = launch
    idle_now = [100.0]
    monkeypatch.setattr(host, "suspend_aware_monotonic", lambda: idle_now[0])
    monkeypatch.setattr(host.time, "monotonic", lambda: 10.0)
    owner = host.WorkerHost(key, directory, idle_timeout=1800, engine_factory=EngineWorker)
    assert owner._last_activity == 100.0
    idle_now[0] = 200.0  # serve() must reset the constructor's idle timestamp.

    class SleepListener:
        calls = 0

        def settimeout(self, value):
            assert value == 0.1

        def accept(self):
            self.calls += 1
            assert self.calls == 1, "Host should exit at the next idle check"
            assert owner._last_activity == 200.0
            idle_now[0] += owner.idle_timeout + 1
            raise TimeoutError("resumed from laptop sleep")

    owner._server = listener = SleepListener()
    owner.serve()
    assert listener.calls == 1
    assert owner._stopping.is_set()
    assert host.time.monotonic() == 10.0
    assert "idle exit" in r.log_path(owner.identifier, directory).read_text()


def test_accept_auth_control_and_disconnect_keep_separate_clocks(launch, monkeypatch):
    key, directory, _ = launch
    idle_now = [100.0]
    monkeypatch.setattr(host, "suspend_aware_monotonic", lambda: idle_now[0])
    monkeypatch.setattr(host.time, "monotonic", lambda: 10.0)
    owner = host.WorkerHost(key, directory, engine_factory=EngineWorker)
    owner.bind()
    original_listener = owner._server
    monkeypatch.setattr(r, "new_token", lambda: "c" * 64)
    deadlines, replies = [], []

    class Peer:
        def settimeout(self, timeout):
            assert timeout == host.CONTROL_TIMEOUT

        def close(self):
            pass

    peer = Peer()

    class SleepListener:
        calls = 0

        def settimeout(self, timeout):
            pass

        def accept(self):
            self.calls += 1
            if self.calls == 1:
                idle_now[0] += owner.idle_timeout + 1
                return peer, None
            owner._stopping.set()
            raise TimeoutError("finished fixture")

        def close(self):
            original_listener.close()

    def receive(connection, *, deadline):
        assert connection is peer
        deadlines.append(deadline)
        if len(deadlines) == 1:
            assert owner._last_activity == idle_now[0]  # accept refreshed idle age
        # Suspend again while each short deadline is already outstanding.
        idle_now[0] += owner.idle_timeout + 1
        assert ipc.remaining_time(deadline) > 0
        if len(deadlines) == 1:
            return r.hello_message(owner.record)
        if len(deadlines) == 2:
            return {**r.host_key({}), "op": "authenticate", "key": key,
                    "key_id": owner.identifier, "nonce": "c" * 64,
                    "proof": r.auth_proof(owner.record, "c" * 64, "client_auth")}
        idle_now[0] += 20.0  # Disconnect refreshes the suspend-aware clock too.
        return None

    # Run the connection inline to exercise all deadlines deterministically.
    monkeypatch.setattr(host.threading, "Thread", lambda *, target, args=(), **kwargs:
                        SimpleNamespace(start=lambda: target(*args)))
    monkeypatch.setattr(host, "receive_frame", receive)
    monkeypatch.setattr(host, "send_frame", lambda connection, message: replies.append(message))
    monkeypatch.setattr(host.select, "select", lambda *args: ([peer], [], []))
    owner._server = SleepListener()
    try:
        owner.serve()
        assert deadlines == [10.0 + host.PREAUTH_TIMEOUT, 10.0 + host.AUTH_TIMEOUT,
                             10.0 + host.CONTROL_TIMEOUT]
        assert [reply["type"] for reply in replies] == ["hello_ok", "authenticated"]
        assert owner._last_activity == idle_now[0]
        assert owner._clients == 0
    finally:
        owner.close()


def test_peer_mid_handshake_or_admitted_client_survives_a_long_sleep(launch, monkeypatch):
    key, directory, _ = launch
    idle_now = [100.0]
    monkeypatch.setattr(host, "suspend_aware_monotonic", lambda: idle_now[0])
    monkeypatch.setattr(host.time, "monotonic", lambda: 10.0)
    owner = host.WorkerHost(key, directory, idle_timeout=1800, engine_factory=EngineWorker)
    handshaking = object()

    class SleepListener:
        calls = 0

        def settimeout(self, value):
            pass

        def accept(self):
            self.calls += 1
            if self.calls == 1:
                # Sleep far past the idle deadline while a peer is mid-handshake.
                idle_now[0] += 10 * owner.idle_timeout
                owner._pending[handshaking] = 10.0 + host.PREAUTH_TIMEOUT
            elif self.calls == 2:
                assert not owner._stopping.is_set()
                owner._pending.pop(handshaking)
                owner._clients = 1  # now an admitted client holds the host
            elif self.calls == 3:
                assert not owner._stopping.is_set()
                owner._clients = 0  # both gone: the next idle check may exit
            else:
                raise AssertionError("Host should exit once nothing holds it")
            raise TimeoutError("poll")

    owner._server = listener = SleepListener()
    owner.serve()
    assert listener.calls == 3
    assert owner._stopping.is_set()
