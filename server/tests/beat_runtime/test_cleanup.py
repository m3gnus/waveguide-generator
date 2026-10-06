from __future__ import annotations

import json
import errno
import os
import socket
import struct

import pytest

from server.solver.beat_runtime import cleanup as c, ipc, registry as r


class FakeHost:
    def __init__(self, record):
        self.record = record
        self.reply = {}
        self.shutdown_reply = {}
        self.sent = []
        self.wire = b""
        self.closed = False

    def sendall(self, wire):
        message = json.loads(wire[4:])
        self.sent.append(message)
        reply = r.auth_reply(self.record, message)
        reply.update(self.reply if message["op"] == "hello" else self.shutdown_reply)
        body = json.dumps(reply).encode()
        self.wire += struct.pack("!I", len(body)) + body

    def recv(self, count):
        chunk, self.wire = self.wire[:count], self.wire[count:]
        return chunk

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@pytest.fixture
def host(tmp_path, monkeypatch):
    record = r.HostRecord(r.host_key({"backend": "cpu"}), os.getpid(), r.new_token(),
                          ipc.Endpoint("tcp", port=1234))
    r.write_record(record, tmp_path)
    peer = FakeHost(record)
    monkeypatch.setattr(ipc.Endpoint, "connect", lambda self, timeout: peer)
    monkeypatch.setattr(c, "pid_alive", lambda pid: not any(x["op"] == "shutdown" for x in peer.sent))
    monkeypatch.setattr(c, "process_start_identity", lambda pid: record.pid_start)
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("Cleanup signalled a PID"))
    return record, peer


def test_authenticated_shutdown_and_dead_record_cleanup(tmp_path, host):
    record, peer = host
    assert c.cleanup_host(record, record.key, tmp_path)
    assert [x["op"] for x in peer.sent] == ["hello", "shutdown"]
    assert all("token" not in message and record.token not in json.dumps(message) for message in peer.sent)
    assert peer.sent[0]["nonce"] != peer.sent[1]["nonce"]
    assert peer.closed
    assert r.read_record(record.identifier, tmp_path) is None
    assert not c.cleanup_host(record, record.key, tmp_path)
    assert r.spawn_lock_path(record.identifier, tmp_path).exists()


@pytest.mark.parametrize(("field", "value"), [
    ("provider", "hornlab-beat-bem"), ("protocol", "beat-worker"),
    ("protocol_version", 2), ("proof", "foreign"), ("key", {}),
    ("key_id", "foreign"), ("host_pid", 999999), ("host_pid", True),
    ("type", "hello_refused"),
])
def test_reused_pid_or_unauthenticated_reply_never_shutdown_or_signal(tmp_path, host, field, value):
    record, peer = host
    peer.reply[field] = value
    with pytest.raises(r.RecordRefused):
        c.cleanup_host(record, record.key, tmp_path)
    assert [x["op"] for x in peer.sent] == ["hello"]
    assert peer.closed
    assert r.read_record(record.identifier, tmp_path) == record


def test_same_process_start_unreachable_live_host_refused_and_retained(tmp_path, host, monkeypatch):
    record, peer = host

    def unreachable(*args):
        raise ConnectionRefusedError(errno.ECONNREFUSED, "nobody answers")

    monkeypatch.setattr(ipc.Endpoint, "connect", unreachable)
    with pytest.raises(r.RecordRefused, match="Unverified live"):
        c.cleanup_host(record, record.key, tmp_path)
    assert not peer.sent
    assert r.read_record(record.identifier, tmp_path) == record


def test_foreign_expected_key_refused_before_connect_or_lock(tmp_path, host):
    record, peer = host
    with pytest.raises(r.RecordRefused):
        c.cleanup_host(record, {**record.key, "provider": "foreign"}, tmp_path)
    assert not peer.sent
    assert not r.spawn_lock_path(record.identifier, tmp_path).exists()


def test_successor_token_is_retained_before_connect(tmp_path, host):
    record, peer = host
    successor = r.HostRecord(record.key, record.pid, r.new_token(), record.endpoint)
    r.write_record(successor, tmp_path)
    with pytest.raises(r.RecordRefused, match="changed"):
        c.cleanup_host(record, record.key, tmp_path)
    assert not peer.sent
    assert r.read_record(record.identifier, tmp_path) == successor


def test_live_host_that_acknowledges_but_does_not_exit_is_retained(tmp_path, host, monkeypatch):
    record, peer = host
    monkeypatch.setattr(c, "pid_alive", lambda pid: True)
    with pytest.raises(r.RecordRefused, match="still live"):
        c.cleanup_host(record, record.key, tmp_path, timeout=0.01)
    assert [x["op"] for x in peer.sent] == ["hello", "shutdown"]
    assert r.read_record(record.identifier, tmp_path) == record


def test_proven_dead_unreachable_record_pruned(tmp_path, host, monkeypatch):
    record, peer = host
    monkeypatch.setattr(c, "pid_alive", lambda pid: False)

    def unreachable(*args):
        raise ConnectionRefusedError(errno.ECONNREFUSED, "dead host")

    monkeypatch.setattr(ipc.Endpoint, "connect", unreachable)
    assert c.cleanup_host(record, record.key, tmp_path)
    assert not peer.sent


def test_foreign_record_refused_before_liveness_or_connect(tmp_path, host, monkeypatch):
    record, peer = host
    path = r.record_path(record.identifier, tmp_path)
    raw = record.as_dict()
    raw["provider"] = "hornlab-beat-bem"
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(c, "pid_alive", lambda pid: pytest.fail("Foreign PID queried"))
    with pytest.raises(r.RecordRefused):
        c.cleanup_host(record, record.key, tmp_path)
    assert json.loads(path.read_text()) == raw
    assert not peer.sent


@pytest.mark.skipif(os.name != "posix", reason="Unix socket residue")
def test_dead_unix_socket_pruned_but_regular_file_retained(tmp_path, monkeypatch):
    key = r.host_key({})
    endpoint = ipc.Endpoint("unix", path=tmp_path / f"{r.key_id(key)}.sock")
    record = r.HostRecord(key, 12345, r.new_token(), endpoint, "fixture-start")
    monkeypatch.setattr(c, "pid_alive", lambda pid: False)
    monkeypatch.chdir(tmp_path)

    def unreachable(*args):
        raise ConnectionRefusedError(errno.ECONNREFUSED, "dead host")

    monkeypatch.setattr(ipc.Endpoint, "connect", unreachable)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(endpoint.path.name)
    r.write_record(record, tmp_path)
    assert c.cleanup_host(record, key, tmp_path)
    assert not endpoint.path.exists()
    endpoint.path.write_text("keep")
    r.write_record(record, tmp_path)
    with pytest.raises(r.RecordRefused, match="not a socket"):
        c.cleanup_host(record, key, tmp_path)
    assert endpoint.path.read_text() == "keep"
    assert r.read_record(record.identifier, tmp_path) == record


def test_dead_pid_with_foreign_responder_prunes_record_without_shutdown(tmp_path, host, monkeypatch):
    record, peer = host
    monkeypatch.setattr(c, "pid_alive", lambda pid: False)
    peer.reply["host_pid"] = record.pid + 1
    # The responder fails authentication and is never asked to shut down; the
    # recorded host's PID is gone, so only the stale record goes.
    assert c.cleanup_host(record, record.key, tmp_path)
    assert [x["op"] for x in peer.sent] == ["hello"]
    assert r.read_record(record.identifier, tmp_path) is None


@pytest.mark.parametrize("operation", ["hello", "shutdown"])
def test_nonce_hmac_reflection_squatter_refused(tmp_path, host, monkeypatch, operation):
    record, peer = host
    original = peer.sendall

    def reflect(wire):
        request = json.loads(wire[4:])
        if request["op"] != operation:
            return original(wire)
        peer.sent.append(request)
        reply = {**request, "type": f"{operation}_ok", "host_pid": record.pid}
        body = json.dumps(reply).encode()
        peer.wire += struct.pack("!I", len(body)) + body

    monkeypatch.setattr(peer, "sendall", reflect)
    with pytest.raises(r.RecordRefused, match="proof"):
        c.cleanup_host(record, record.key, tmp_path)
    assert all("token" not in request for request in peer.sent)
    assert r.read_record(record.identifier, tmp_path) == record


def test_nonce_hmac_replay_from_previous_connection_refused(tmp_path, host, monkeypatch):
    record, peer = host
    previous = r.auth_reply(record, r.hello_message(record))
    peer.reply.update(previous)
    with pytest.raises(r.RecordRefused, match="authentication"):
        c.cleanup_host(record, record.key, tmp_path)
    assert [x["op"] for x in peer.sent] == ["hello"]


@pytest.mark.parametrize("error", [errno.ECONNREFUSED, errno.ENOENT])
def test_reused_pid_process_start_mismatch_pruned(tmp_path, host, monkeypatch, error):
    record, peer = host
    monkeypatch.setattr(c, "process_start_identity", lambda pid: "different-start")

    def refused(*args):
        raise OSError(error, "no listener")

    monkeypatch.setattr(ipc.Endpoint, "connect", refused)
    assert c.cleanup_host(record, record.key, tmp_path)
    assert not peer.sent
    assert r.read_record(record.identifier, tmp_path) is None


@pytest.mark.parametrize("start", [None, "same-start"])
@pytest.mark.parametrize("error", [None, errno.ETIMEDOUT])
def test_tcp_connect_timeout_retains_live_or_unidentifiable_pid(tmp_path, host, monkeypatch, start, error):
    record, peer = host
    monkeypatch.setattr(c, "process_start_identity", lambda pid: record.pid_start if start else None)

    def timeout(*args):
        raise TimeoutError(error, "unverified endpoint")

    monkeypatch.setattr(ipc.Endpoint, "connect", timeout)
    with pytest.raises(r.RecordRefused):
        c.cleanup_host(record, record.key, tmp_path)
    assert r.read_record(record.identifier, tmp_path) == record


@pytest.mark.parametrize("alive", [False, True])
@pytest.mark.parametrize("error", [None, errno.ETIMEDOUT])
def test_tcp_connect_timeout_prunes_only_proven_gone_process(tmp_path, host, monkeypatch, alive, error):
    record, peer = host
    monkeypatch.setattr(c, "pid_alive", lambda pid: alive)
    monkeypatch.setattr(c, "process_start_identity", lambda pid: "different-start")

    def timeout(*args):
        raise TimeoutError(error, "closed loopback port")

    monkeypatch.setattr(ipc.Endpoint, "connect", timeout)
    assert c.cleanup_host(record, record.key, tmp_path)
    assert not peer.sent
    assert r.read_record(record.identifier, tmp_path) is None


def test_unix_connect_timeout_retains_even_reused_pid(tmp_path, host, monkeypatch):
    from dataclasses import replace

    record, peer = host
    original = record
    # Exercise the locked cleanup policy with a mocked Unix record, including
    # on Windows where the outer registry correctly rejects Unix records.
    record = replace(record, endpoint=ipc.Endpoint("unix", path=tmp_path / f"{record.identifier}.sock"))
    monkeypatch.setattr(c, "read_record", lambda *args: record)
    monkeypatch.setattr(c, "process_start_identity", lambda pid: "different-start")

    def timeout(*args):
        raise ConnectionError(errno.ETIMEDOUT, "unverified Unix endpoint")

    monkeypatch.setattr(c, "connect_authenticated", lambda *args, **kwargs: timeout())
    with pytest.raises(r.RecordRefused):
        c._cleanup_locked(record, record.key, tmp_path, float("inf"))
    assert not peer.sent
    assert r.read_record(record.identifier, tmp_path) == original


def test_tcp_connect_timeout_never_removes_successor_record(tmp_path, host, monkeypatch):
    from dataclasses import replace

    record, peer = host
    successor = replace(record, token=r.new_token())
    monkeypatch.setattr(c, "pid_alive", lambda pid: False)

    def timeout(*args):
        r.write_record(successor, tmp_path)
        raise TimeoutError("closed loopback port")

    monkeypatch.setattr(ipc.Endpoint, "connect", timeout)
    with pytest.raises(r.RecordRefused, match="Successor"):
        c.cleanup_host(record, record.key, tmp_path)
    assert not peer.sent
    assert r.read_record(record.identifier, tmp_path) == successor


def test_native_closed_loopback_port_prunes_proven_gone_record_with_short_deadline(tmp_path, monkeypatch):
    endpoint = ipc.Endpoint("tcp")
    listener = endpoint.listen()
    listener.close()
    record = r.HostRecord(r.host_key({}), os.getpid(), r.new_token(), endpoint, "previous-start")
    r.write_record(record, tmp_path)
    monkeypatch.setattr(c, "pid_alive", lambda pid: True)
    monkeypatch.setattr(c, "process_start_identity", lambda pid: "current-start")
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("Cleanup signalled a PID"))
    # Windows can expire this budget before the closed port reports refusal;
    # POSIX usually reports ECONNREFUSED immediately. Both prove the same policy.
    assert c.cleanup_host(record, record.key, tmp_path, timeout=0.05)
    assert r.read_record(record.identifier, tmp_path) is None


def test_cleanup_reuses_held_spawn_lock(tmp_path, host):
    record, peer = host
    with r.SpawnLock(r.spawn_lock_path(record.identifier, tmp_path)) as lock:
        assert c.cleanup_host(record, record.key, tmp_path, lock=lock)
        assert lock.held
    assert not lock.held


def test_cleanup_lock_busy_is_documented_refusal(tmp_path, host):
    record, peer = host
    with r.SpawnLock(r.spawn_lock_path(record.identifier, tmp_path)):
        with pytest.raises(r.LockBusy, match="timed out"):
            c.cleanup_host(record, record.key, tmp_path, timeout=0.01)
    assert not peer.sent
    assert r.read_record(record.identifier, tmp_path) == record


def test_cleanup_rejects_unheld_or_wrong_slot_spawn_lock(tmp_path, host):
    record, peer = host
    unheld = r.SpawnLock(r.spawn_lock_path(record.identifier, tmp_path))
    with pytest.raises(r.RecordRefused, match="held spawn lock"):
        c.cleanup_host(record, record.key, tmp_path, lock=unheld)
    with r.SpawnLock(tmp_path / "another.lock") as wrong:
        with pytest.raises(r.RecordRefused, match="held spawn lock"):
            c.cleanup_host(record, record.key, tmp_path, lock=wrong)
    assert not peer.sent


@pytest.mark.parametrize("phase", ["hello", "shutdown"])
def test_control_trickle_deadline_spans_handshake_and_shutdown(tmp_path, host, monkeypatch, phase):
    record, peer = host
    now = [0.0]
    monkeypatch.setattr(c.time, "monotonic", lambda: now[0])
    original = peer.recv

    def recv(count):
        now[0] += 0.3 if peer.sent[-1]["op"] == phase else 0.01
        # Trickle one byte within the per-recv timeout each time.
        return original(1)

    monkeypatch.setattr(peer, "recv", recv)
    with pytest.raises(r.RecordRefused, match="deadline"):
        c.cleanup_host(record, record.key, tmp_path, timeout=1)
    assert peer.closed
    assert r.read_record(record.identifier, tmp_path) == record


def test_shutdown_shares_remaining_handshake_deadline(tmp_path, host, monkeypatch):
    record, peer = host
    now = [0.0]
    monkeypatch.setattr(c.time, "monotonic", lambda: now[0])
    original = peer.recv

    def recv(count):
        now[0] += 0.3
        return original(count)  # Header and body consume 0.6 s per exchange.

    monkeypatch.setattr(peer, "recv", recv)
    with pytest.raises(r.RecordRefused, match="shutdown.*deadline"):
        c.cleanup_host(record, record.key, tmp_path, timeout=1)
    assert [x["op"] for x in peer.sent] == ["hello", "shutdown"]


@pytest.mark.skipif(os.name != "posix", reason="Unix socket residue")
def test_locked_orphan_socket_sweep_recovers_refused_endpoint(tmp_path, monkeypatch):
    identifier = r.key_id(r.host_key({}))
    path = tmp_path / f"{identifier}.sock"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ipc.Endpoint, "_address", lambda self: self.path.name)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(path.name)
    with r.SpawnLock(r.spawn_lock_path(identifier, tmp_path)) as lock:
        assert c.sweep_orphan_socket(identifier, tmp_path, lock=lock)
        assert not path.exists()
    assert not c.sweep_orphan_socket(identifier, tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="Unix socket residue")
def test_orphan_socket_sweep_retains_listener_nonregular_and_record(tmp_path, monkeypatch):
    identifier = r.key_id(r.host_key({}))
    path = tmp_path / f"{identifier}.sock"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ipc.Endpoint, "_address", lambda self: self.path.name)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(path.name)
        server.listen(1)
        with pytest.raises(r.RecordRefused, match="listening"):
            c.sweep_orphan_socket(identifier, tmp_path)
        assert path.exists()
    path.unlink()
    path.write_text("keep")
    with pytest.raises(r.RecordRefused, match="not a socket"):
        c.sweep_orphan_socket(identifier, tmp_path)
    assert path.read_text() == "keep"
    path.unlink()
    record = r.HostRecord(r.host_key({}), 12345, r.new_token(), ipc.Endpoint("tcp", port=1234), "fixture-start")
    r.write_record(record, tmp_path)
    with pytest.raises(r.RecordRefused, match="host record"):
        c.sweep_orphan_socket(identifier, tmp_path)
    assert r.read_record(identifier, tmp_path) == record


def test_cleanup_record_unlink_missing_ok_race(tmp_path, host, monkeypatch):
    record, peer = host
    original = c.unlink_record

    def already_removed(path):
        path.unlink()
        original(path)

    monkeypatch.setattr(c, "unlink_record", already_removed)
    assert c.cleanup_host(record, record.key, tmp_path)


@pytest.mark.parametrize(("start", "pruned"), [("different-start", True), (None, False)])
def test_port_squatter_after_host_death_prunes_only_a_proven_gone_host(
    tmp_path, host, monkeypatch, start, pruned
):
    record, peer = host
    monkeypatch.setattr(c, "process_start_identity", lambda pid: start)
    peer.reply.update({"proof": "0" * 64})  # an unrelated listener cannot prove the token
    if pruned:
        assert c.cleanup_host(record, record.key, tmp_path)
        assert r.read_record(record.identifier, tmp_path) is None
    else:
        with pytest.raises(r.RecordRefused):
            c.cleanup_host(record, record.key, tmp_path)
        assert r.read_record(record.identifier, tmp_path) == record
    assert [x["op"] for x in peer.sent] == ["hello"]
