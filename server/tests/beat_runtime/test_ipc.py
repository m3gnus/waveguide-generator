from __future__ import annotations

import errno
import json
import os
import socket
import struct
import threading
import time

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


@pytest.mark.parametrize("prefix", [b"", b"\x00", frame(b'{"a":1}')[:6]])
def test_cancellation_interrupts_silent_and_partial_frames(prefix):
    sender, receiver = socket.socketpair()
    cancel, reading = threading.Event(), threading.Event()
    errors = []

    def cancelled():
        reading.set()
        return cancel.is_set()

    def receive():
        try:
            ipc.receive_frame(receiver, deadline=time.monotonic() + 10, cancelled=cancelled)
        except ConnectionAbortedError as exc:
            errors.append(exc)

    with sender, receiver:
        sender.sendall(prefix)
        thread = threading.Thread(target=receive, daemon=True)
        thread.start()
        try:
            assert reading.wait(1)
            started = time.monotonic()
            cancel.set()
            thread.join(0.5)
            assert not thread.is_alive() and len(errors) == 1
            assert time.monotonic() - started < 0.5
        finally:
            cancel.set()
            thread.join(1)


def test_poll_timeout_preserves_partial_frame_and_original_deadline():
    class Peer(FragmentedPeer):
        timeouts = 0

        def settimeout(self, seconds):
            assert 0 < seconds <= 0.05

        def recv(self, count):
            self.timeouts += 1
            if self.timeouts in {2, 7}:
                raise TimeoutError
            return super().recv(count)

    assert ipc.receive_frame(Peer(frame(b'{"a":1}')), deadline=time.monotonic() + 1,
                             cancelled=lambda: False) == {"a": 1}
    sender, receiver = socket.socketpair()
    with sender, receiver, pytest.raises(TimeoutError, match="deadline"):
        ipc.receive_frame(receiver, deadline=time.monotonic() + 0.05, cancelled=lambda: False)


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
    for transport in ((None, "unix", "tcp") if os.name == "posix" else (None, "tcp")):
        assert ipc.endpoint_for("0123456789abcdef", deep, transport=transport).kind == "tcp"
    if os.name == "nt":
        with pytest.raises(ValueError, match="Unix sockets unavailable"):
            ipc.endpoint_for("0123456789abcdef", deep, transport="unix")
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


def test_oversized_control_header_refused_before_body_allocation():
    class HeaderOnly:
        def __init__(self):
            self.calls = 0

        def recv(self, count):
            self.calls += 1
            assert self.calls == 1, "Oversized frame body read"
            assert count == 4
            return struct.pack("!I", ipc.CONTROL_FRAME_BYTES + 1)

    with pytest.raises(ipc.FrameError, match="length"):
        ipc.receive_frame(HeaderOnly())
    assert ipc.receive_frame(FragmentedPeer(frame(b'{"a":1}')), max_bytes=7) == {"a": 1}


def test_trickle_responder_exceeds_overall_receive_deadline(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(ipc.time, "monotonic", lambda: now[0])

    class Trickle(FragmentedPeer):
        def settimeout(self, timeout):
            assert 0 < timeout <= 1

        def recv(self, count):
            now[0] += 0.3  # Every byte arrives within the original per-recv timeout.
            return super().recv(count)

    with pytest.raises(TimeoutError, match="deadline"):
        ipc.receive_frame(Trickle(frame(b'{"a":1}')), deadline=1)
    assert now[0] < 1.5


@pytest.mark.parametrize("number", ["1e999", "-1e999", "NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_numbers_refused(number):
    with pytest.raises(ipc.FrameError):
        ipc.receive_frame(FragmentedPeer(frame(f'{{"a":{number}}}'.encode())))


@pytest.mark.skipif(os.name != "posix", reason="Unix socket residue")
@pytest.mark.parametrize("failure", ["chmod", "listen"])
def test_failed_post_bind_step_unlinks_only_own_socket(tmp_path, monkeypatch, failure):
    endpoint = ipc.Endpoint("unix", path=tmp_path / "bound.sock")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ipc.Endpoint, "_address", lambda self: self.path.name)

    def fail(*args):
        raise OSError("post-bind failure")

    if failure == "chmod":
        monkeypatch.setattr(ipc.os, "chmod", fail)
    else:
        original = endpoint._socket

        class FailedListen:
            def __init__(self):
                self.server = original()

            def bind(self, address):
                self.server.bind(address)

            def listen(self, backlog):
                fail()

            def close(self):
                self.server.close()

        monkeypatch.setattr(endpoint, "_socket", FailedListen)
    with pytest.raises(OSError, match="post-bind"):
        endpoint.listen()
    assert not endpoint.path.exists()
    endpoint.path.write_text("keep existing endpoint")
    with pytest.raises(OSError):
        endpoint.listen()
    assert endpoint.path.read_text() == "keep existing endpoint"


def test_windows_loopback_exclusive_address_use(monkeypatch):
    import types

    events = []
    server = types.SimpleNamespace(
        setsockopt=lambda *args: events.append(("exclusive", args)),
        bind=lambda *args: events.append(("bind", args)),
        listen=lambda *args: None, getsockname=lambda: ("127.0.0.1", 1234), close=lambda: None)
    monkeypatch.setattr(ipc, "os", types.SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(ipc.socket, "SO_EXCLUSIVEADDRUSE", -5, raising=False)
    monkeypatch.setattr(ipc.Endpoint, "_socket", lambda self: server)
    assert ipc.Endpoint("tcp").listen() is server
    assert events[0] == ("exclusive", (socket.SOL_SOCKET, -5, 1))
    assert events[1][0] == "bind"


@pytest.mark.parametrize("errors", [[errno.EAGAIN, None], [errno.EAGAIN] * 3, [errno.EACCES]])
def test_unix_connect_retries_full_accept_queue_with_one_deadline(monkeypatch, tmp_path, errors):
    import errno

    now = [0.0]
    calls, budgets, closed = [], [], []

    class Client:
        def settimeout(self, timeout):
            budgets.append(timeout)

        def connect(self, address):
            calls.append(address)
            error = errors[min(len(calls) - 1, len(errors) - 1)]
            if error is not None:
                raise BlockingIOError(error, "fixture queue full")

        def close(self):
            closed.append(True)

    client = Client()
    monkeypatch.setattr(ipc.Endpoint, "_socket", lambda self: client)
    monkeypatch.setattr(ipc.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ipc.time, "sleep", lambda duration: now.__setitem__(0, now[0] + duration))
    endpoint = ipc.Endpoint("unix", path=tmp_path / "host.sock")
    if errors[-1] is None:
        assert endpoint.connect(.025) is client
        assert len(calls) == 2 and not closed
        assert budgets[1] < budgets[0]
    elif errors[0] == errno.EAGAIN:
        with pytest.raises(TimeoutError, match="deadline"):
            endpoint.connect(.025)
        assert len(calls) == 3 and closed == [True]
        assert now[0] == .025
    else:
        with pytest.raises(BlockingIOError):
            endpoint.connect(.025)
        assert len(calls) == 1 and closed == [True] and now[0] == 0


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be", "utf-8-sig"])
def test_only_utf8_without_bom_is_accepted(encoding):
    with pytest.raises(ipc.FrameError):
        ipc.receive_frame(FragmentedPeer(frame('{"message":"hello"}'.encode(encoding))))


@pytest.mark.parametrize("tcp", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_stream_socket_requests_large_buffers_and_tcp_nodelay(tcp, fails):
    requested = []
    class Peer:
        def setsockopt(self, *option):
            requested.append(option)
            if fails:
                raise OSError("platform buffer cap")
    ipc.configure_stream_socket(Peer(), tcp=tcp)
    assert requested[:2] == [(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024),
                             (socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)]
    assert requested[2:] == ([(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)] if tcp else [])
