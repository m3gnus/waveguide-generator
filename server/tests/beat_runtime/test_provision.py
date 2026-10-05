from __future__ import annotations

from contextlib import contextmanager

import pytest

from server.solver.beat_runtime import discovery, locks, provision, state



def test_cpu_steps_and_independent_metal_state(cpu_provisioning):
    root, engine, julia, calls, options = cpu_provisioning
    ready = provision.provision_cpu(**options)
    assert ready["status"] == "ready" and ready["step"] == "done"
    assert ready == state.read_state(root, backend="cpu")
    metal = state.write_state(dict(ready, backend="metal"), root)
    assert discovery.recorded_julia(root) == str(julia)
    assert [code for code, _ in calls] == ["using Pkg; Pkg.instantiate()", "using Pkg; Pkg.precompile()", "probe"]
    assert all(call.get("project", call.get("julia_project")) == engine.project for _, call in calls)
    assert all(call["environment"]["BLAB_BEAT_ENGINE_GPU_BACKEND"] == "cpu" for _, call in calls)
    assert calls[-1][1]["julia_threads"] == 3
    assert ready["environment"]["JULIA_NUM_THREADS"] == "3"
    assert "SECRET" not in ready["environment"]
    assert not (root / "state.json").exists()
    calls.clear()
    assert provision.provision_cpu(**options) == ready
    assert calls == []
    assert state.read_state(root, backend="metal") == metal


def test_locked_recheck_adopts_completed_record(cpu_provisioning, monkeypatch):
    root, _, _, calls, options = cpu_provisioning
    ready = provision.provision_cpu(**options)
    (root / "state-cpu.json").unlink()
    calls.clear()
    locked = False
    original_read = state.read_state
    reads = []

    @contextmanager
    def lock(*args, **kwargs):
        nonlocal locked
        state.write_state(ready, root)  # A competing provisioner finished during the wait.
        locked = True
        yield
        locked = False

    def read(*args, **kwargs):
        reads.append(locked)
        return original_read(*args, **kwargs)

    monkeypatch.setattr(locks, "provisioning_lock", lock)
    monkeypatch.setattr(state, "read_state", read)
    assert provision.provision_cpu(**options)["status"] == "ready"
    assert calls == []
    assert reads == [False, True]


def test_raising_callback_is_reported_once_across_steps_and_lock(cpu_provisioning, monkeypatch, capsys):
    _, _, _, _, options = cpu_provisioning
    seen = []

    def console(message):
        seen.append(message)
        raise UnicodeEncodeError("cp1252", "\u2713", 0, 1, "unprintable")

    @contextmanager
    def lock(*args, status_cb, **kwargs):
        status_cb("Waiting")
        yield

    monkeypatch.setattr(locks, "provisioning_lock", lock)
    assert provision.provision_cpu(**dict(options, status_cb=console))["status"] == "ready"
    assert len(seen) >= 5
    assert capsys.readouterr().err.count("status callback failed (UnicodeEncodeError)") == 1


@pytest.mark.parametrize("failing", ["instantiate", "precompile", "cpu_probe", "resolve_julia"])
def test_failure_records_step_and_retry_reuses_julia(cpu_provisioning, failing):
    root, _, julia, _, options = cpu_provisioning
    metal_ready = provision.provision_cpu(**options)
    state.write_state(dict(metal_ready, backend="metal"), root)
    before = (root / "state-metal.json").read_bytes()

    def fail(*args, **kwargs):
        raise RuntimeError("offline or probe failed")

    def step(executable, code, **kwargs):
        if failing in code:
            fail()

    broken = dict(options, force=True, run_step=step)
    if failing == "cpu_probe":
        broken["probe"] = fail
    if failing == "resolve_julia":
        broken["ensure_julia"] = fail
    failed = provision.provision_cpu(**broken)
    assert failed["status"] == "failed" and failed["step"] == failing
    assert "offline or probe failed" in failed["error"]
    assert state.read_state(root, backend="cpu") == failed
    assert (root / "state-metal.json").read_bytes() == before
    assert discovery.recorded_julia(root) == str(julia)
    assert provision.provision_cpu(**options)["status"] == "ready"
    assert provision.provision_cpu(**dict(options, retry=True))["status"] == "ready"


@pytest.mark.parametrize("flag", ["force", "retry"])
def test_explicit_force_and_retry_run_again(cpu_provisioning, flag):
    _, _, _, calls, options = cpu_provisioning
    provision.provision_cpu(**options)
    calls.clear()
    assert provision.provision_cpu(**dict(options, **{flag: True}))["status"] == "ready"
    assert len(calls) == 3


@pytest.mark.parametrize("completion", [None, {}, {"finite": True, "nonzero": False, "terminal_count": 1},
                                      {"finite": True, "nonzero": True, "terminal_count": True},
                                      {"finite": True, "nonzero": True, "terminal_count": 2},
                                      {"finite": True, "nonzero": True, "terminal_count": 1, "pressure": float("nan")}])
def test_missing_or_vacuous_probe_never_ready(cpu_provisioning, completion):
    root, _, _, _, options = cpu_provisioning
    probe = None if completion is None else lambda **kwargs: completion
    result = provision.provision_cpu(**dict(options, probe=probe))
    assert result["status"] == "failed" and result["step"] == "cpu_probe"
    assert state.read_state(root, backend="cpu")["completion"] == {}


@pytest.mark.parametrize("write_fails", [False, True])
def test_lock_error_record_preserves_original_error(cpu_provisioning, monkeypatch, write_fails):
    root, _, _, calls, options = cpu_provisioning

    @contextmanager
    def fail_lock(*args, **kwargs):
        raise OSError("ENOLCK: unsupported locking")
        yield

    def fail_write(*args, **kwargs):
        raise OSError("diagnostic disk full")

    monkeypatch.setattr(locks, "provisioning_lock", fail_lock)
    if write_fails:
        monkeypatch.setattr(state, "write_state", fail_write)
    failed = provision.provision_cpu(**options)
    assert failed["step"] == "lock" and failed["status"] == "failed"
    assert "ENOLCK" in failed["error"] and "disk full" not in failed["error"]
    assert calls == []
    if not write_fails:
        assert state.read_state(root, backend="cpu") == failed
