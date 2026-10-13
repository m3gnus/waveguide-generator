"""Readiness is bound to an update attempt, independent of settlement's lock."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from launchers import apply_update as updater, update_lock
from launchers.statusapp.healthy_start import HealthyStartSettlement
from server.tests.test_apply_update import _layout


@pytest.fixture(autouse=True)
def private_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_lock, "cache_root", lambda **_kwargs: tmp_path / "cache")


def ready_install(tmp_path: Path):
    bundle, resources, staged_app, staged_runtime = _layout(tmp_path)
    build = {"version": "2.0.1", "commit": "a" * 40, "runtimeId": "111111111111"}
    (staged_app / "APP-MANIFEST.json").write_text(json.dumps(build))
    (resources / "app" / "APP-MANIFEST.json").write_text(json.dumps(build | {"version": "2.0.0"}))
    return bundle, resources, staged_app, staged_runtime


class LiveChild:
    def poll(self):
        return None


def apply(tmp_path: Path, layout, relauncher):
    bundle, _resources, staged_app, staged_runtime = layout
    return updater.apply_update(
        bundle=bundle, data_dir=tmp_path / "data", staged_app=staged_app,
        staged_runtime=staged_runtime, parent_pid=1, platform_name="darwin",
        runner=lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
        waiter=lambda _pid: True, relauncher=relauncher, failure_reporter=lambda _message: None,
    )


def test_readiness_can_arrive_while_updater_still_holds_mutation_claim(tmp_path: Path) -> None:
    layout = ready_install(tmp_path)
    bundle, resources, *_ = layout
    settlement = HealthyStartSettlement(lambda: (bundle, resources, tmp_path / "data"))

    def relaunch(*_args, **_kwargs):
        # The startup proof arrives before settlement can acquire the held claim.
        assert not settlement.settle(ready=True, evidence="interface ready")
        assert updater.readiness_path(resources, response=True).is_file()
        return LiveChild()

    try:
        with update_lock.claim_update(resources):
            assert apply(tmp_path, layout, relaunch) == 0
        assert (resources / "app.previous").is_dir()
        assert not updater.readiness_path(resources).exists()
    finally:
        settlement.cancel()


def test_a_readiness_timeout_never_rolls_back_under_a_live_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = ready_install(tmp_path)
    _bundle, resources, *_ = layout
    monkeypatch.setattr(updater, "wait_for_readiness", lambda *_args, **_kwargs: False)
    assert apply(tmp_path, layout, lambda *_args, **_kwargs: LiveChild()) == 5
    assert (resources / "app" / "marker.txt").read_text() == "new app"
    assert (resources / "app.previous" / "marker.txt").read_text() == "old app"
    assert updater.read_journal(tmp_path / "data", resources)["state"] == "installed"
    assert not updater.readiness_path(resources).exists()


@pytest.mark.parametrize("field", ["nonce", "transaction", "build"])
def test_a_previous_attempt_or_different_build_cannot_satisfy_readiness(tmp_path: Path, field: str) -> None:
    resources = tmp_path / "resources"
    expected = {"nonce": "a" * 32, "transaction": "tx", "build": {"version": "2.0.1"}}
    stale = expected | {field: "stale"}
    updater._write_readiness(updater.readiness_path(resources, response=True), stale)
    assert not updater.wait_for_readiness(resources, expected, timeout=0)
    updater._write_readiness(updater.readiness_path(resources, response=True), expected | {"pid": 1})
    assert updater.wait_for_readiness(resources, expected, timeout=0)


def test_slow_start_is_confirmed_by_evidence_after_the_previous_six_second_window(tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    expected = {"nonce": "a" * 32, "transaction": "tx", "build": {"version": "2.0.1"}}
    now = 0.0

    def sleep(_seconds):
        nonlocal now
        now += 1
        if now == 8:
            updater._write_readiness(updater.readiness_path(resources, response=True), expected)

    assert updater.wait_for_readiness(resources, expected, timeout=20, clock=lambda: now, sleeper=sleep)
    assert now == 8


def test_an_unready_interface_does_not_publish_a_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = ready_install(tmp_path)
    bundle, resources, *_ = layout
    monkeypatch.setattr(updater, "wait_for_readiness", lambda *_args, **_kwargs: False)
    apply(tmp_path, layout, lambda *_args, **_kwargs: LiveChild())
    request = updater.prepare_readiness(resources, tmp_path / "data")
    assert request is not None
    settlement = HealthyStartSettlement(lambda: (bundle, resources, tmp_path / "data"))
    assert not settlement.settle(ready=False, evidence="wrong serving build")
    assert not updater.readiness_path(resources, response=True).exists()


def test_a_late_attempt_cannot_clear_the_new_attempts_proof(tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    earlier, later = {"nonce": "a" * 32}, {"nonce": "b" * 32}
    updater._write_readiness(updater.readiness_path(resources), later)
    updater._write_readiness(updater.readiness_path(resources, response=True), later)
    updater.clear_readiness(resources, earlier)
    assert updater._read_readiness(updater.readiness_path(resources)) == later
    assert updater._read_readiness(updater.readiness_path(resources, response=True)) == later
