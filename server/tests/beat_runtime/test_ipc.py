from __future__ import annotations

import json
import os
import socket
import struct

import pytest

from server.solver.beat_runtime import ipc


class FragmentedPeer:
    def __init__(self, body: bytes):
        self.body = body

    def recv(self, count: int) -> bytes:
        chunk, self.body = self.body[:1], self.body[1:]
        return chunk


def frame(body: bytes) -> bytes:
    return struct.pack("!I", len(body)) + body


def test_fragmented_and_multiple_frames():
    peer = FragmentedPeer(frame(b'{"a":1}') + frame(b'{"b":2}'))
    assert ipc.receive_frame(peer) == {"a": 1}
    assert ipc.receive_frame(peer) == {"b": 2}
    assert ipc.receive_frame(peer) is None


@pytest.mark.parametrize("wire", [
    b"\0", struct.pack("!I", 0), struct.pack("!I", ipc.MAX_FRAME_BYTES + 1),
    struct.pack("!I", 8) + b"{}", frame(b"invalid"), frame(b"[]"),
    frame(b"\xff"), frame(b'{"x":NaN}'), frame(b"null"),
])
def test_malformed_and_truncated_frames(wire):
    with pytest.raises(ipc.FrameError):
        ipc.receive_frame(FragmentedPeer(wire))


def test_send_and_receive_unicode_frames():
    sender, receiver = socket.socketpair()
    with sender, receiver:
        ipc.send_frame(sender, {"message": "λ"})
        assert ipc.receive_frame(receiver) == {"message": "λ"}


def test_send_rejects_nonobject_nonfinite_and_oversize(monkeypatch):
    class Peer:
        def sendall(self, wire):
            pytest.fail("Invalid payload was sent")

    with pytest.raises(ipc.FrameError):
        ipc.send_frame(Peer(), [])
    with pytest.raises(ValueError):
        ipc.send_frame(Peer(), {"x": float("inf")})
    monkeypatch.setattr(ipc, "MAX_FRAME_BYTES", 2)
    with pytest.raises(ipc.FrameError):
        ipc.send_frame(Peer(), {"long": "payload"})


@pytest.mark.parametrize("transport", ["unix", "tcp"])
def test_local_endpoint_roundtrip_and_no_second_bind(tmp_path, transport, monkeypatch):
    if transport == "unix" and os.name != "posix":
        pytest.skip("POSIX Unix sockets")
    endpoint = ipc.endpoint_for("0123456789abcdef", tmp_path, transport=transport)
    if transport == "unix":
        # Exercise Unix operations even when pytest's absolute root is too long.
        # The socket remains inside tmp_path; overflow selection is tested below.
        endpoint = ipc.Endpoint("unix", path=tmp_path / "0123456789abcdef.sock")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(ipc.Endpoint, "_address", lambda self: self.path.name)
    with endpoint.listen() as server:
        restored = ipc.Endpoint.from_dict(endpoint.as_dict())
        with restored.connect(1) as client:
            accepted, _ = server.accept()
            with accepted:
                payload = {"message": "λ", "block": [1, 2, 3]}
                ipc.send_frame(client, payload)
                assert ipc.receive_frame(accepted) == payload
                client.shutdown(socket.SHUT_WR)
                assert ipc.receive_frame(accepted) is None
        with pytest.raises(OSError):
            restored.listen()
    if endpoint.kind == "unix":
        assert endpoint.path.stat().st_mode & 0o777 == 0o600


def test_encoded_unix_overflow_and_windows_fall_back(tmp_path, monkeypatch):
    deep = tmp_path / ("λ" * 90)
    for transport in (None, "unix", "tcp"):
        assert ipc.endpoint_for("0123456789abcdef", deep, transport=transport).kind == "tcp"
    assert not deep.exists()
    monkeypatch.delattr(ipc.socket, "AF_UNIX", raising=False)
    assert ipc.endpoint_for("0123456789abcdef", tmp_path).kind == "tcp"


@pytest.mark.parametrize("raw", [
    {"kind": "tcp", "host": "example.com", "port": 123},
    {"kind": "tcp", "host": "127.0.0.1", "port": True},
    {"kind": "tcp", "host": "127.0.0.1", "port": 65536},
    {"kind": "unix", "path": "relative.sock"}, {"kind": "other"},
])
def test_foreign_or_malformed_endpoints_refused(raw):
    with pytest.raises(ValueError):
        ipc.Endpoint.from_dict(json.loads(json.dumps(raw)))


def test_identifier_cannot_escape_registry(tmp_path):
    with pytest.raises(ValueError):
        ipc.endpoint_for("../foreign", tmp_path)
