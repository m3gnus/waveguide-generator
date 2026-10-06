"""Warm-up selection follows provider readiness without starting engines."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from server.solver import beat, bempp, metal, official_beat, warmup


@pytest.mark.parametrize("hbb_present", [False, True])
@pytest.mark.parametrize("official", [False, True])
@pytest.mark.parametrize("backend", ["cpu", "metal", None])
def test_boot_warmup_selects_provider_readiness(monkeypatch, official, backend, hbb_present):
    monkeypatch.setitem(sys.modules, "hornlab_beat_bem", SimpleNamespace() if hbb_present else None)
    if official:
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
                for name in ("cpu", "metal")}

    monkeypatch.setattr(official_beat, "production_statuses", statuses)
    if official:
        monkeypatch.setattr(beat, "_load_api", lambda: pytest.fail("warm-up imported HBB"))
        monkeypatch.setattr(beat, "beat_status", lambda: pytest.fail("warm-up probed HBB"))
        monkeypatch.setattr(beat, "beat_backend_statuses", lambda: pytest.fail("warm-up probed HBB rows"))
    else:
        def hbb_status():
            probes.append("hbb")
            return {"available": backend is not None, "backend": backend}
        monkeypatch.setattr(beat, "beat_status", hbb_status)
        monkeypatch.setattr(beat, "beat_backend_statuses", lambda: {"cpu": {"available": backend == "cpu"}})
    warmup._run_warmup()
    assert warmed == [backend or "bempp"]
    assert set(probes) == {"official" if official else "hbb"}


@pytest.mark.parametrize("official", [False, True])
def test_legacy_engine_prewarm_uses_selected_readiness(monkeypatch, official):
    if official:
        monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    warmed = []
    monkeypatch.setattr(warmup, "_warm_beat", warmed.append)
    monkeypatch.setattr(official_beat, "production_statuses", lambda: {
        "cpu": {"available": True, "backend": "cpu"},
        "metal": {"available": False, "backend": "metal"}})
    if official:
        monkeypatch.setattr(beat, "beat_status", lambda: pytest.fail("legacy prewarm probed HBB"))
    else:
        monkeypatch.setattr(beat, "beat_status", lambda: {"available": True, "backend": "metal"})
    assert warmup.prewarm_beat_worker_for_engine("beat")
    assert warmed == ["cpu" if official else "metal"]


@pytest.mark.parametrize("official", [False, True])
def test_unavailable_legacy_prewarm_respects_official_readiness(monkeypatch, official):
    if official:
        monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    monkeypatch.setattr(official_beat, "production_statuses", lambda: {
        backend: {"available": False, "reason": "not proved"} for backend in ("cpu", "metal")})
    monkeypatch.setattr(beat, "beat_status", lambda: {"available": False, "backend": None})
    warmed = []
    monkeypatch.setattr(warmup, "_warm_beat", warmed.append)
    assert warmup.prewarm_beat_worker_for_engine("beat") is (not official)
    assert warmed == ([] if official else [beat.BEAT_FALLBACK_BACKEND])
