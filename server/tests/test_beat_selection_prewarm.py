"""Picker changes and startup share the cancellable BEAT prewarm path."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest

import server.app as app_module
from server.app import create_app
from server.solver import official_beat, warmup
from server.solver.beat_runtime.session import SessionCancelled
from server.tests.test_jobs_api import _request_with_headers


@pytest.fixture
def app(tmp_path, monkeypatch):
    static = tmp_path / 'static'
    static.mkdir()
    monkeypatch.setattr(app_module, 'FRONTEND_DIST', static)
    monkeypatch.delenv('WG2_SOLVER_WARMUP', raising=False)
    return create_app(data_dir=tmp_path / 'data')


def test_picker_schedules_once_without_waiting_and_deselect_cancels(app, monkeypatch):
    calls, callbacks = [], []

    async def fake(_registry, _settings, *, requested, cancellation_callback):
        calls.append(requested)
        callbacks.append(cancellation_callback)
        await asyncio.Event().wait()

    monkeypatch.setattr(app_module, 'beat_worker_prewarm', fake)

    async def scenario():
        status, _, _ = await _request_with_headers(app, 'POST', '/api/runtimes/beat/prewarm',
                                                  body={'engine': 'beat-metal'})
        assert status == 202
        task = app.state.beat_prewarm_task
        async with asyncio.timeout(2):
            while not calls:
                await asyncio.sleep(0.001)
        assert calls == ['beat-metal'] and not task.done()
        await app.state.prewarm_beat_worker('beat-metal')
        assert app.state.beat_prewarm_task is task
        await app.state.prewarm_beat_worker('metal')
        with suppress(asyncio.CancelledError):
            await task
        assert task.cancelled()
        with pytest.raises(SessionCancelled):
            callbacks[0]()
        assert calls == ['beat-metal']

    asyncio.run(scenario())


def test_selection_defers_during_running_solve_and_cancels_on_backend_switch(app, monkeypatch):
    calls = []
    app.state.jobs_runtime = SimpleNamespace(running_job_ids={'real-solve'})

    async def fake(_registry, _settings, **kwargs):
        calls.append(kwargs['requested'])

    monkeypatch.setattr(app_module, 'beat_worker_prewarm', fake)

    async def scenario():
        await app.state.prewarm_beat_worker('beat-metal')
        first = app.state.beat_prewarm_task
        await asyncio.sleep(0.02)
        assert not calls
        await app.state.prewarm_beat_worker('beat-cpu')
        with suppress(asyncio.CancelledError):
            await first
        assert first.cancelled()
        app.state.jobs_runtime.running_job_ids = set()
        await app.state.beat_prewarm_task
        assert calls == ['beat-cpu']

    asyncio.run(scenario())


def test_saved_beat_startup_and_same_picker_selection_join_one_task(app, monkeypatch):
    app.state.settings.put('solveOptions', '{"state":{"engine":"beat-metal"}}')
    entered = asyncio.Event()

    async def fake(_registry, _settings, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(app_module, 'beat_worker_prewarm', fake)

    async def scenario():
        startup = next(h for h in app.router.on_startup if h.__name__ == 'prewarm_beat_worker')
        await startup()
        await entered.wait()
        first = app.state.beat_prewarm_task
        await app.state.prewarm_beat_worker('beat-metal')
        assert app.state.beat_prewarm_task is first
        await app.state.prewarm_beat_worker('auto')
        with suppress(asyncio.CancelledError):
            await first

    asyncio.run(scenario())


def test_unavailable_runtime_never_launches_a_warmup(monkeypatch):
    monkeypatch.delenv('WG2_SOLVER_WARMUP', raising=False)
    monkeypatch.setattr(official_beat, 'production_statuses', lambda **kwargs: {
        'metal': {'available': False, 'reason': 'not provisioned'}})
    monkeypatch.setattr(warmup, '_warm_beat', lambda *a, **k: pytest.fail('unavailable runtime warmed'))
    assert not warmup.prewarm_beat_worker_for_engine('beat-metal', cancellation_callback=lambda: None)


def test_named_selection_reuses_existing_backend_hook(monkeypatch):
    from server.engines.registry import EngineRegistry
    from server.solver import beat_cpu_runtime

    calls = []
    monkeypatch.setattr(beat_cpu_runtime, 'cpu_preparation_in_flight', lambda: False)
    monkeypatch.setattr(warmup, 'prewarm_beat_worker_for_engine',
                        lambda engine, **kwargs: calls.append(engine) or True)
    asyncio.run(app_module.beat_worker_prewarm(EngineRegistry(detector=lambda: []),
                                              requested='beat-metal', cancellation_callback=lambda: None))
    assert calls == ['beat-metal']


def test_reselection_retries_busy_admission_after_cancelled_warmup_drains(monkeypatch):
    monkeypatch.delenv('WG2_SOLVER_WARMUP', raising=False)
    monkeypatch.setattr(official_beat, 'production_statuses', lambda **kwargs: {
        'metal': {'available': True}})
    attempts = []

    def warm(_backend, **kwargs):
        attempts.append(True)
        return len(attempts) > 1

    monkeypatch.setattr(warmup, '_warm_beat', warm)
    assert warmup.prewarm_beat_worker_for_engine('beat-metal', cancellation_callback=lambda: None)
    assert len(attempts) == 2


@pytest.mark.parametrize("engine", ["beat-cpu", "beat-metal", "metal", "auto"])
def test_launcher_reads_one_durable_selection_after_restart(tmp_path, monkeypatch, engine):
    from launch import serve
    from server.settings import SettingsStore
    from server.solver import beat_cpu_runtime

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    store = SettingsStore(tmp_path)
    store.put("solveOptions", '{"state":{"engine":"beat-metal"}}')
    store.put("enginePreference", {"engine": engine})
    assert warmup.persisted_engine_preference(SettingsStore(tmp_path)) == engine
    calls = []
    monkeypatch.setattr(beat_cpu_runtime, "start_selected_provisioning", calls.append)
    serve._start_beat_cpu_provisioning()
    assert calls == [engine]



def test_new_selection_cancels_old_preparation_before_provisioning(app, monkeypatch):
    from server.solver import beat_cpu_runtime

    calls = []
    active = True
    def cancel(engine):
        nonlocal active
        calls.append(("cancel", engine))
        active = False
    monkeypatch.setattr(beat_cpu_runtime, "cpu_preparation_in_flight", lambda: active)
    monkeypatch.setattr(beat_cpu_runtime, "cancel_unselected_preparation", cancel)
    monkeypatch.setattr(beat_cpu_runtime, "start_selected_provisioning", lambda engine, **kwargs: calls.append(("prepare", engine)))
    async def warm(*args, **kwargs):
        calls.append(("warm", kwargs["requested"]))
    monkeypatch.setattr(app_module, "beat_worker_prewarm", warm)
    async def scenario():
        await app.state.prewarm_beat_worker("beat-cpu")
        await app.state.beat_prewarm_task
    asyncio.run(scenario())
    assert calls == [("cancel", "beat-cpu"), ("prepare", "beat-cpu"), ("warm", "beat-cpu")]
