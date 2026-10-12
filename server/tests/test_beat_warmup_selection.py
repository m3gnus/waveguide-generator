"""Warm-up selection follows provider readiness without starting engines."""

from __future__ import annotations


import pytest

from server.solver import beat, bempp, metal, official_beat, warmup


@pytest.mark.parametrize("backend", ["cpu", "metal", "cuda", "rocm", None])
def test_boot_warmup_selects_provider_readiness(monkeypatch, backend):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    warmed = []
    probes = []
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": False})
    monkeypatch.setattr(bempp, "bempp_status", lambda: {"available": True})
    monkeypatch.setattr(warmup, "_warm_beat", warmed.append)
    monkeypatch.setattr(warmup, "_warm_bempp", lambda status: warmed.append("bempp"))

    def statuses():
        probes.append("official")
        return {name: {"available": name == backend, "backend": name, "reason": "test"}
                for name in beat.BEAT_BACKENDS}

    monkeypatch.setattr(official_beat, "production_statuses", statuses)
    monkeypatch.setattr(beat, "beat_status", lambda: pytest.fail("warm-up probed retired engine"))
    monkeypatch.setattr(beat, "beat_backend_statuses", lambda: pytest.fail("warm-up probed retired engine rows"))
    warmup._run_warmup()
    assert warmed == [backend or "bempp"]
    assert set(probes) == {"official"}


def test_legacy_engine_prewarm_uses_selected_readiness(monkeypatch):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    warmed = []
    monkeypatch.setattr(warmup, "_warm_beat", warmed.append)
    monkeypatch.setattr(official_beat, "production_statuses", lambda: {
        "cpu": {"available": True, "backend": "cpu"},
        **{name: {"available": False, "backend": name} for name in beat.BEAT_BACKENDS if name != "cpu"}})
    monkeypatch.setattr(beat, "beat_status", lambda: pytest.fail("legacy prewarm probed retired engine"))
    assert warmup.prewarm_beat_worker_for_engine("beat")
    assert warmed == ["cpu"]


def test_unavailable_legacy_prewarm_respects_official_readiness(monkeypatch):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    monkeypatch.setattr(official_beat, "production_statuses", lambda: {
        backend: {"available": False, "reason": "not proved"} for backend in beat.BEAT_BACKENDS})
    monkeypatch.setattr(beat, "beat_status", lambda: {"available": False, "backend": None})
    warmed = []
    monkeypatch.setattr(warmup, "_warm_beat", warmed.append)
    assert warmup.prewarm_beat_worker_for_engine("beat") is (not True)
    assert warmed == ([])
