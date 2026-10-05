from types import SimpleNamespace
import subprocess

import pytest

from server.solver import beat_threads
from server.solver.beat_runtime import threads


@pytest.fixture(autouse=True)
def clear_core_cache():
    threads._performance_core_count.cache_clear()
    yield
    threads._performance_core_count.cache_clear()


@pytest.mark.parametrize(("system", "total"), [("Linux", 8), ("Windows", 4), ("Linux", None), ("Linux", 0)])
def test_non_mac_falls_back_to_total_without_sysctl(monkeypatch, system, total):
    monkeypatch.setattr(threads.platform, "system", lambda: system)
    monkeypatch.setattr(threads.os, "cpu_count", lambda: total)

    def forbidden(*args, **kwargs):
        pytest.fail("sysctl should not run")

    monkeypatch.setattr(threads.subprocess, "run", forbidden)
    assert threads._performance_core_count() == (total or 1)


@pytest.mark.parametrize(("output", "code", "expected"), [
    ("8\n", 0, 8), ("20", 0, 10), ("0", 0, 10), ("-1", 0, 10),
    ("", 0, 10), ("unknown", 0, 10), ("8", 1, 8),
])
def test_mac_performance_cores_and_fallback(monkeypatch, output, code, expected):
    monkeypatch.setattr(threads.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(threads.os, "cpu_count", lambda: 10)
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=output, returncode=code)

    monkeypatch.setattr(threads.subprocess, "run", run)
    assert threads._performance_core_count() == expected
    assert threads._performance_core_count() == expected
    assert len(calls) == 1
    assert calls[0][0] == (["/usr/sbin/sysctl", "-n", "hw.perflevel0.logicalcpu"],)
    assert calls[0][1]["timeout"] == 5


@pytest.mark.parametrize("error", [OSError("missing"), subprocess.TimeoutExpired("sysctl", 5)])
def test_mac_sysctl_errors_fall_back(monkeypatch, error):
    monkeypatch.setattr(threads.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(threads.os, "cpu_count", lambda: 10)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(threads.subprocess, "run", fail)
    assert threads._performance_core_count() == 10


@pytest.mark.parametrize(("cores", "expected"), [(1, 1), (2, 2), (4, 3), (8, 6), (10, 8), (12, 9)])
def test_auto_metal_headroom_and_cpu_full_count(cores, expected):
    assert threads.resolve_julia_threads("metal", performance_cores=cores) == expected
    assert threads.resolve_julia_threads("cpu", performance_cores=cores) == cores


@pytest.mark.parametrize("count", [1, 6, 20, "1", " 6 ", "20"])
def test_explicit_counts_override_auto_policy(monkeypatch, count):
    def forbidden():
        pytest.fail("explicit count must not probe hardware")

    monkeypatch.setattr(threads, "_performance_core_count", forbidden)
    assert threads.resolve_julia_threads("metal", count) == int(count)
    assert threads.resolve_julia_threads("cpu", count) == int(count)


@pytest.mark.parametrize("count", [0, -1, "0", "-2", "bad", "", True, None, 2.5])
def test_invalid_explicit_count_is_an_error(count):
    with pytest.raises(ValueError, match="positive integer"):
        threads.resolve_julia_threads("cpu", count)


@pytest.mark.parametrize(("backend", "expected"), [("cpu", 8), ("metal", 6)])
def test_resolved_snapshot_is_identical_for_key_start_probe_and_warmup(monkeypatch, backend, expected):
    calls = []

    def cores():
        calls.append(1)
        return 8 if len(calls) == 1 else 4

    monkeypatch.setattr(threads, "_performance_core_count", cores)
    count = threads.resolve_julia_threads(backend)
    key = (backend, count)
    starts = []

    def fake_start(*, julia_threads):
        starts.append(julia_threads)

    for _purpose in ("probe", "warmup", "production"):
        fake_start(julia_threads=count)
    assert key[1] == expected
    assert starts == [key[1]] * 3
    assert calls == [1]


def test_legacy_facade_delegates_without_changing_hbb_cpu_auto(monkeypatch):
    calls = []
    monkeypatch.setattr(beat_threads, "_performance_core_count", lambda: 8)
    monkeypatch.setattr(beat_threads, "resolve_julia_threads", lambda *args, **kwargs: calls.append((args, kwargs)) or 6)
    assert beat_threads.beat_julia_threads("metal") == 6
    assert calls == [(("metal",), {"performance_cores": 8})]
    assert beat_threads.beat_julia_threads("cpu") == "auto"
    assert len(calls) == 1
