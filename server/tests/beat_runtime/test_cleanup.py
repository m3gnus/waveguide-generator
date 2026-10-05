from __future__ import annotations

import json
import os
import socket
import struct

import pytest

from server.solver.beat_runtime import cleanup as c, ipc, registry as r


class FakeHost:
    def __init__(self, record):
        self.reply = {**r.hello_message(record), "type": "hello_ok", "host_pid": record.pid}
        self.sent = []
        self.wire = b""
        self.closed = False

    def sendall(self, wire):
        message = json.loads(wire[4:])
        self.sent.append(message)
        reply = self.reply if message["op"] == "hello" else {"type": "shutdown_ok"}
        body = json.dumps(reply).encode()
        self.wire += struct.pack("!I", len(body)) + body

    def recv(self, count):
        chunk, self.wire = self.wire[:count], self.wire[count:]
        return chunk

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
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("Cleanup signalled a PID"))
    return record, peer


def test_authenticated_shutdown_and_dead_record_cleanup(tmp_path, host):
    record, peer = host
    assert c.cleanup_host(record, record.key, tmp_path)
    assert [x["op"] for x in peer.sent] == ["hello", "shutdown"]
    assert peer.sent[0]["token"] == record.token
    assert peer.closed
    assert r.read_record(record.identifier, tmp_path) is None
    assert not c.cleanup_host(record, record.key, tmp_path)
    assert r.spawn_lock_path(record.identifier, tmp_path).exists()


@pytest.mark.parametrize(("field", "value"), [
    ("provider", "hornlab-beat-bem"), ("protocol", "beat-worker"),
    ("protocol_version", 2), ("token", "foreign"), ("key", {}),
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


def test_unreachable_live_record_is_refused_and_retained(tmp_path, host, monkeypatch):
    record, peer = host

    def unreachable(*args):
        raise ConnectionRefusedError("nobody answers")

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
        raise ConnectionRefusedError("dead host")

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
    record = r.HostRecord(key, 12345, r.new_token(), endpoint)
    monkeypatch.setattr(c, "pid_alive", lambda pid: False)
    monkeypatch.chdir(tmp_path)

    def unreachable(*args):
        raise ConnectionRefusedError("dead host")

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


def test_dead_pid_does_not_authorize_cleanup_of_foreign_responder(tmp_path, host, monkeypatch):
    record, peer = host
    monkeypatch.setattr(c, "pid_alive", lambda pid: False)
    peer.reply["host_pid"] = record.pid + 1
    with pytest.raises(r.RecordRefused, match="returned PID"):
        c.cleanup_host(record, record.key, tmp_path)
    assert [x["op"] for x in peer.sent] == ["hello"]
    assert r.read_record(record.identifier, tmp_path) == record
