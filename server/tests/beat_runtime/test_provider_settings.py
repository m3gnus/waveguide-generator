"""Retired settings warn once while every backend keeps the official route."""

import pytest

from server.solver.beat_runtime import provider
from server.solver import beat
from server.solver.context import SolverContext
from server.tests.test_official_beat_bridge import runtime


@pytest.mark.parametrize("value", [" hBb ", " Legacy "])
def test_retired_provider_warns_once(caplog, monkeypatch, value):
    monkeypatch.setattr(provider, "_warned_values", set())
    env = {provider.PROVIDER_ENV: value}
    provider.warn_removed_provider(env)
    provider.warn_removed_provider({provider.PROVIDER_ENV: value.strip().upper()})
    assert len(caplog.records) == 1
    assert "HBB was removed" in caplog.text
    assert "using the official BEAT engine" in caplog.text


@pytest.mark.parametrize("value", ["", "official", " Official "])
def test_supported_old_settings_are_quiet(caplog, value):
    provider.warn_removed_provider({provider.PROVIDER_ENV: value})
    assert not caplog.records


def test_unknown_provider_preserves_warning(caplog, monkeypatch):
    monkeypatch.setattr(provider, "_warned_values", set())
    for value in ["future-provider", " FUTURE-PROVIDER "]:
        provider.warn_removed_provider({provider.PROVIDER_ENV: value})
    assert len(caplog.records) == 1
    assert "using the default official provider" in caplog.text


@pytest.mark.parametrize("backend", ["cpu", "metal", "cuda", "rocm"])
@pytest.mark.parametrize("value", ["hbb", "legacy", "unknown"])
def test_obsolete_setting_cannot_change_solve_route(runtime, monkeypatch, backend, value):
    monkeypatch.setenv(provider.PROVIDER_ENV, value)
    result = beat.solve_beat_from_msh_text(
        beat.Path(beat.__file__).with_name("warmup_mesh.msh").read_text(),
        SolverContext(design=None, frequency_range=(300., 500.), num_frequencies=2),
        backend=backend,
    )
    assert result["metadata"]["solver_engine"]["package"] == "beat-engine"
    assert result["metadata"]["solver_engine"]["provider"] == "official"
    assert runtime.requests[0]["solver_options"]["bem_backend"] == backend
