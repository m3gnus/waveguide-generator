"""With the official BEAT provider unselected, user-visible behaviour is unchanged."""

from __future__ import annotations

from types import SimpleNamespace

from server.diagnostics import capabilities
from server.solver import beat_cpu_runtime


def test_default_provision_command_text_is_unchanged(monkeypatch):
    monkeypatch.delenv("WG2_BEAT_PROVIDER", raising=False)
    monkeypatch.setattr(beat_cpu_runtime.sys, "executable", "/Apps/Waveguide Generator.app/bin/python3")
    assert beat_cpu_runtime.provision_command() == (
        '"/Apps/Waveguide Generator.app/bin/python3" -m hornlab_beat_bem.provision --backend cpu'
    )


def test_default_status_lines_still_record_and_notify(monkeypatch):
    monkeypatch.delenv("WG2_BEAT_PROVIDER", raising=False)
    seen = []
    monkeypatch.setattr(beat_cpu_runtime, "_record_step", lambda message: seen.append(("step", message)))
    monkeypatch.setattr(beat_cpu_runtime, "_notify_readiness_listeners", lambda: seen.append(("notify",)))
    beat_cpu_runtime._provision_status("Precompiling")
    assert seen == [("step", "Precompiling"), ("notify",)]


def test_official_status_lines_do_not_notify(monkeypatch):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    seen = []
    monkeypatch.setattr(beat_cpu_runtime, "_notify_readiness_listeners", lambda: seen.append("notify"))
    beat_cpu_runtime._provision_status("Precompiling")
    assert seen == []


def test_capabilities_omit_official_runtime_unless_selected(monkeypatch):
    registry = SimpleNamespace(official_runtime_statuses={"cpu": "ready"})
    monkeypatch.delenv("WG2_BEAT_PROVIDER", raising=False)
    assert capabilities._official_beat_runtime(registry) == {}
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    assert capabilities._official_beat_runtime(registry) == {"officialBeatRuntime": {"cpu": "ready"}}
