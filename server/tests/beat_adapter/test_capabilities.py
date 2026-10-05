"""Observed adapter capabilities remain honest as request refusals change."""

from __future__ import annotations

import builtins
import json

import pytest

from server.solver.beat_adapter import capabilities


def test_report_runs_without_engine_or_hbb_and_counts_one_unit(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("beat_engine", "hornlab_beat_bem")):
            raise AssertionError(f"Unexpected optional import {name}")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    report = capabilities.capability_report()
    assert report["engine"] == "JWSound/BEAT_Engine"
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    scenarios = report["scenarios"]
    assert report["supported_count"] + report["refused_count"] == len(scenarios)
    for name in ("parametric.exterior", "imported.exterior", "route.beat-cpu.float64",
                 "route.beat-metal.float32", "frequencies.unordered", "frequencies.float32_alias"):
        assert scenarios[name]["supported"], (name, scenarios[name])
    for name in ("route.beat-metal.float64", "route.beat.None", "symmetry.xy", "symmetry.x",
                 "symmetry.y", "parametric.infinite_baffle", "imported.infinite_baffle",
                 "frequencies.empty", "frequencies.duplicate", "feature.coupled_fem_bem_lem"):
        assert not scenarios[name]["supported"]
        assert scenarios[name]["reason"] and scenarios[name]["exception"]
    assert all(scenarios[f"ground.{axis}"]["supported"] for axis in "xyz")
    assert all(not scenarios[f"ground.{axis}+reduction"]["supported"] for axis in "xyz")


def test_outcomes_and_reasons_come_from_builder_not_a_parallel_list(monkeypatch):
    original = capabilities.request.build_request

    def changed(*args, **kwargs):
        if kwargs.get("symmetry") == "xy":
            kwargs["symmetry"] = "full"
        if kwargs.get("quadrature_order") == 3:
            raise ValueError("new builder reason")
        return original(*args, **kwargs)

    monkeypatch.setattr(capabilities.request, "build_request", changed)
    scenarios = capabilities.capability_report()["scenarios"]
    assert scenarios["symmetry.xy"]["supported"]
    assert scenarios["quadrature.cpu.3"]["reason"] == "new builder reason"


def test_feature_name_validation_distinguishes_typo_from_refusal():
    assert not capabilities.probe_feature("near_correction")["supported"]
    # Previous catch-all mechanism treated a misspelling as a valid refusal.
    old = capabilities._outcome(lambda: capabilities.probe_request(near_corection=True))
    assert not old["supported"] and old["exception"] == "TypeError"
    with pytest.raises(ValueError, match="Unknown declared feature"):
        capabilities.probe_feature("near_corection")


def test_feature_probe_exercises_keyword_dispatch_instead_of_assuming_absence(monkeypatch):
    original = capabilities.request.build_request
    def supported(*args, **kwargs):
        kwargs.pop("near_correction", None)
        return original(*args, **kwargs)
    monkeypatch.setattr(capabilities.request, "build_request", supported)
    report = capabilities.capability_report()
    assert report["scenarios"]["feature.near_correction"]["supported"]
    assert not report["passed"]  # The declared refusal changed through keyword dispatch.
