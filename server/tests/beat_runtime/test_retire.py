from __future__ import annotations

import ctypes
from types import SimpleNamespace
import uuid

import pytest

from server.solver.beat_runtime import retire


@pytest.fixture
def named_events(monkeypatch):
    """Record calls without loading kernel32 or touching a Windows event."""
    calls = []

    def create(*args):
        calls.append(("create", *args))
        return 1

    def wait(*args):
        calls.append(("wait", *args))
        return 0

    def close(*args):
        calls.append(("close", *args))
        return True

    kernel32 = SimpleNamespace(CreateEventW=create, WaitForSingleObject=wait, CloseHandle=close)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel32, raising=False)
    monkeypatch.setattr(retire, "os", SimpleNamespace(name="nt"))
    return calls


def test_production_retire_event_ignores_test_environment(named_events, monkeypatch):
    name = f"WaveguideGeneratorTestRetireIdleBeatHosts-{uuid.uuid4().hex}"
    monkeypatch.setenv("WG2_BEAT_TEST_RETIRE_EVENT", name)
    monkeypatch.setenv("BEAT_FAKE_HOST_RETIRE_EVENT", name)
    signal = retire.RetireSignal()
    signal.close()
    assert named_events == [
        ("create", None, True, False, "WaveguideGeneratorRetireIdleBeatHosts"), ("close", 1),
    ]


def test_retire_event_constant_is_resolved_at_construction(named_events, monkeypatch):
    names = [f"WaveguideGeneratorTestRetireIdleBeatHosts-{uuid.uuid4().hex}" for _ in range(2)]
    for name in names:
        monkeypatch.setattr(retire, "RETIRE_IDLE_EVENT", name)
        signal = retire.RetireSignal()
        assert signal.requested()
        signal.close()
        assert not signal.requested()
    assert named_events == [call for name in names for call in (
        ("create", None, True, False, name), ("wait", 1, 0), ("close", 1),
    )]


@pytest.mark.parametrize("name", [None, "", " ", "Global\\event", "Local\\event", "a\\b", "a\0b"])
def test_invalid_retire_event_is_refused_before_loading_windows_api(monkeypatch, name):
    def forbidden(*args, **kwargs):
        pytest.fail("An invalid retire name reached the Windows API")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(retire, "RETIRE_IDLE_EVENT", name)
    with pytest.raises(ValueError, match="non-empty session-local name"):
        retire.RetireSignal()
