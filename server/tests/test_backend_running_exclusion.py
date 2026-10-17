"""Backend startup holds native exclusion before taking the instance lock."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from launch import serve
from server.platform import running_exclusion


@pytest.fixture
def startup(monkeypatch):
    events = []
    # Keep the real OS module intact for pytest and pathlib, and isolate all
    # startup inputs without creating a real server or named mutex.
    monkeypatch.setattr(serve, "os", SimpleNamespace(environ={}))
    monkeypatch.setattr(serve, "ensure_data_layout", lambda: SimpleNamespace(
        root=Path("test-backend-data"), locks=Path("test-backend-data/locks"),
    ))
    monkeypatch.setattr(serve, "setup_logging", lambda _paths: None)
    monkeypatch.setattr(serve, "flush_logs", lambda: events.append("flush"))

    monkeypatch.setattr(serve, "requested_port", lambda _port: events.append("port") or 3100)
    monkeypatch.setattr(serve, "InstanceLock", lambda _path: SimpleNamespace(
        acquire=lambda _port: events.append("lock acquire"),
        release=lambda: events.append("lock release"),
    ))
    # Stop after lock acquisition; none of the wiring assertions needs Uvicorn.
    monkeypatch.setattr(serve, "_release_interface_error", lambda: "test interface refusal")
    return events


@pytest.mark.parametrize("lifetime", ["headless", "contained", "attached"])
def test_running_exclusion_precedes_instance_lock(startup, monkeypatch, lifetime):
    events = startup
    argv = ["--no-browser"]
    if lifetime == "headless":
        argv.append("--lifetime=headless")
    elif lifetime == "contained":
        serve.os.environ["WG2_BACKEND_CONTAINED"] = "1"
    monkeypatch.setattr(running_exclusion, "hold_running_exclusion",
                        lambda: events.append("hold") or True)

    assert serve.main(argv) == 1  # The deliberate interface refusal above.
    assert events.count("lock acquire") == 1
    if lifetime == "attached":
        assert "hold" not in events
    else:
        assert events.count("hold") == 1
        assert events.index("hold") < events.index("port") < events.index("lock acquire")


@pytest.mark.parametrize("lifetime", ["headless", "contained"])
def test_running_exclusion_refusal_stops_the_start(
    startup, monkeypatch, capsys, lifetime,
):
    events = startup
    argv = ["--no-browser", "--lifetime=headless"]
    if lifetime == "contained":
        serve.os.environ["WG2_BACKEND_CONTAINED"] = "1"

    def refuse():
        events.append("hold")
        raise running_exclusion.RunningExclusionError(5)

    monkeypatch.setattr(running_exclusion, "hold_running_exclusion", refuse)
    assert serve.main(argv) == 1
    error = capsys.readouterr().err
    assert "Waveguide Generator could not start:" in error
    assert "Win32 error 5" in error
    assert events == ["hold", "flush"]


@pytest.mark.parametrize("name", [
    "", "WaveguideGeneratorRunning", "WaveguideGeneratorTestRunning-",
    "WaveguideGeneratorTestRunning-   ", "WaveguideGeneratorTestRunning-x\\y",
    "WaveguideGeneratorTestRunning-x\0y", "Global\\WaveguideGeneratorTestRunning-x",
])
def test_harness_refuses_unsafe_mutex_before_native_calls(monkeypatch, name):
    from server.tests import running_backend_main as harness

    monkeypatch.setattr(harness, "os", SimpleNamespace(environ={
        "WG2_TEST_RUNNING_MUTEX": name,
        "WG2_TEST_RETIRE_EVENT": "WaveguideGeneratorTestRetireIdleBeatHosts-safe",
    }))
    monkeypatch.setattr(running_exclusion, "_hold", lambda _name: pytest.fail("unsafe native call"))
    monkeypatch.setattr(harness.multiprocessing, "get_context",
                        lambda _method: pytest.fail("unsafe child start"))
    with pytest.raises(ValueError, match="private test running mutex"):
        harness.main([])


@pytest.mark.parametrize("name", [
    "", "WaveguideGeneratorRetireIdleBeatHosts", "WaveguideGeneratorTestRetireIdleBeatHosts-",
    "WaveguideGeneratorTestRetireIdleBeatHosts-x\\y", "WaveguideGeneratorTestRetireIdleBeatHosts-x\0y",
])
def test_harness_refuses_unsafe_event_before_native_calls(monkeypatch, name):
    from server.tests import running_backend_main as harness

    monkeypatch.setattr(harness, "os", SimpleNamespace(environ={
        "WG2_TEST_RUNNING_MUTEX": "WaveguideGeneratorTestRunning-safe",
        "WG2_TEST_RETIRE_EVENT": name,
    }))
    monkeypatch.setattr(running_exclusion, "_hold", lambda _name: pytest.fail("unsafe native call"))
    monkeypatch.setattr(harness.multiprocessing, "get_context",
                        lambda _method: pytest.fail("unsafe child start"))
    with pytest.raises(ValueError, match="private test retire event"):
        harness.main([])
