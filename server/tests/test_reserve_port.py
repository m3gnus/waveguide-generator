"""Direct tests of ``reserve_port`` against real sockets on 127.0.0.1."""

from __future__ import annotations

import random
import socket
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from server.platform import instance

HOST = "127.0.0.1"
SPAN = instance.PORT_SCAN_COUNT + 1


def _listen(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        instance._configure_port_bind(sock)
        sock.bind((HOST, port))
        sock.listen()
    except OSError:
        sock.close()
        raise
    return sock


def _acquire_run(count: int) -> list[socket.socket]:
    """Listen on ``count`` consecutive free ports and return the sockets."""

    rng = random.Random()
    for _ in range(200):
        base = rng.randrange(20000, 60000)
        held: list[socket.socket] = []
        try:
            for port in range(base, base + count):
                held.append(_listen(port))
        except OSError:
            for sock in held:
                sock.close()
            continue
        return held
    pytest.skip("could not find a run of free local ports")


@contextmanager
def _free_run(count: int = SPAN) -> Iterator[tuple[int, list[socket.socket]]]:
    """Yield the first port of a free run and its sockets, all closed on exit.

    The sockets are held so the run stays reserved while the test chooses
    which ports to free or keep busy.
    """

    held = _acquire_run(count)
    try:
        yield held[0].getsockname()[1], held
    finally:
        for sock in held:
            sock.close()


def test_free_base_port_is_used() -> None:
    with _free_run(1) as (base, held):
        held[0].close()
        listener, port = instance.reserve_port(base, host=HOST)
        try:
            assert port == base
            assert listener.getsockname() == (HOST, base)
        finally:
            listener.close()


def test_busy_base_port_falls_back_to_the_next_free_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with _free_run() as (base, held):
        # Base, base+1 and base+3 stay busy; base+2 is the first free port.
        held[2].close()
        for sock in held[4:]:
            sock.close()
        with caplog.at_level("WARNING", logger=instance.log.name):
            listener, port = instance.reserve_port(base, host=HOST)
        try:
            assert port == base + 2
            assert listener.getsockname() == (HOST, base + 2)
            assert f"Port {base} is busy; using port {base + 2} instead" in caplog.text
        finally:
            listener.close()


def test_fallback_reaches_the_last_port_of_the_range() -> None:
    with _free_run() as (base, held):
        held[-1].close()
        listener, port = instance.reserve_port(base, host=HOST)
        try:
            assert port == base + instance.PORT_SCAN_COUNT
        finally:
            listener.close()


def test_all_ports_busy_names_the_range_and_what_to_do() -> None:
    with _free_run() as (base, _held):
        last = base + instance.PORT_SCAN_COUNT
        with pytest.raises(OSError) as raised:
            instance.reserve_port(base, host=HOST)
        message = str(raised.value)
        assert f"Ports {base} through {last} are all busy on {HOST}" in message
        assert "Stop an existing server" in message
        assert "--port PORT" in message


def test_the_scan_does_not_go_past_the_range() -> None:
    # Eleven ports in a row: the first ten busy, the eleventh free. The scan
    # covers base..base+9 only, so it must fail rather than take the eleventh.
    with _free_run(SPAN + 1) as (base, held):
        held[-1].close()
        with pytest.raises(OSError, match="are all busy"):
            instance.reserve_port(base, host=HOST)
