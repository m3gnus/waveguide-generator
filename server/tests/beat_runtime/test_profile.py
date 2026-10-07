"""Opt-in timing marks never touch clocks on disabled sweeps."""

from server.solver.beat_runtime import profile


def test_disabled_profile_never_reads_clock(monkeypatch):
    monkeypatch.delenv("WG2_BEAT_PROFILE", raising=False)
    monkeypatch.setattr(profile, "perf_counter", lambda: (_ for _ in ()).throw(AssertionError("clock read")))
    assert profile.start_profile(lambda message: None, "wg") is None


def test_timing_sequence_marks_first_and_last_results(monkeypatch):
    monkeypatch.setenv("WG2_BEAT_PROFILE", "1")
    stamps = iter(range(100, 120))
    monkeypatch.setattr(profile, "perf_counter", lambda: next(stamps))
    messages = []
    marks = profile.start_profile(messages.append, "host")
    for name in ("statuses done", "request built", "worker acquired", "submitted"):
        marks.mark(name)
    marks.event({"type": "status"})
    marks.event({"type": "result"})
    marks.event({"type": "result"})
    marks.event({"type": "completed"})
    marks.mark("mapped")
    marks.mark("closed")
    names = [message.split(": ")[1].split(" elapsed_s=")[0] for message in messages]
    assert names == ["solve start", "statuses done", "request built", "worker acquired", "submitted",
                     "first event", "first result", "last result", "completed", "mapped", "closed"]
    assert "elapsed_s=7.000000" in messages[names.index("last result")]


def test_detached_host_logs_matching_timing_marks(launch, monkeypatch, caplog):
    import logging
    caplog.set_level(logging.INFO)
    from server.solver.beat_runtime import client, registry
    from server.tests.beat_runtime.fake_host_worker import wait_until

    monkeypatch.setenv("WG2_BEAT_PROFILE", "1")
    key, directory, _ = launch
    worker = client.HostedWorker(key, directory=directory)
    try:
        stream = worker.submit({"name": "profile"})
        assert list(stream)[-1]["type"] == "completed"
        latency_logs = [record.message for record in caplog.records if record.message.startswith("BEAT relay wg:")]
        assert any("event=result count=1" in message for message in latency_logs)
        assert any("event=completed count=2" in message for message in latency_logs)
        assert any("summary count=2 median_s=" in message and "p90_s=" in message and "max_s=" in message
                   for message in latency_logs)
        log_path = registry.log_path(registry.key_id(key), directory)
        wait_until(lambda: "host: closed" in log_path.read_text(), timeout=2)
        names = [line.split("host: ")[1].split(" elapsed_s=")[0]
                 for line in log_path.read_text().splitlines() if "BEAT profile host:" in line]
        assert set(names) == {"solve start", "statuses done", "request built", "worker acquired", "submitted",
                              "first event", "first result", "last result", "completed", "mapped", "closed"}
    finally:
        worker.detach()


def test_relay_latency_logs_each_event_and_summary(monkeypatch):
    monkeypatch.setattr(profile, "monotonic", lambda: 100.)
    messages = []
    latency = profile.RelayLatency(messages.append)
    for index, kind in enumerate(["result"] * 4 + ["completed"]):
        latency.observe({"host_relay_monotonic": 100. - index, "event": {"type": kind}})
    latency.finish()
    assert len(messages) == 6
    assert "event=completed count=5 latency_s=4.000000" in messages[-2]
    assert "count=5 median_s=2.000000 p90_s=3.600000 max_s=4.000000" in messages[-1]


def test_disabled_stream_latency_never_reads_clock(monkeypatch):
    from types import SimpleNamespace
    from server.solver.beat_runtime import client

    monkeypatch.delenv("WG2_BEAT_PROFILE", raising=False)
    monkeypatch.setattr(profile, "monotonic", lambda: (_ for _ in ()).throw(AssertionError("clock read")))
    peer = SimpleNamespace(shutdown=lambda *a: None, close=lambda: None)
    worker = SimpleNamespace(_closed=SimpleNamespace(is_set=lambda: False))
    stream = client._RemoteStream(worker, peer, None)
    monkeypatch.setattr(client, "_receive_frame", lambda *a, **kw: {
        "type": "event", "event": {"type": "completed"}, "host_relay_monotonic": 1.})
    assert next(stream) == {"type": "completed"}
    assert stream._latency is None
    stream.close()


def test_host_stamps_frame_only_on_enabled_profile(monkeypatch):
    monkeypatch.setenv("WG2_BEAT_PROFILE", "1")
    monkeypatch.setattr(profile, "monotonic", lambda: 42.5)
    marks = profile.start_profile(lambda message: None, "host")
    frame = {"type": "event", "event": {"type": "result"}}
    marks.relay_frame(frame)
    assert frame["host_relay_monotonic"] == 42.5
