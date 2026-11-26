"""Picker changes and startup share the cancellable BEAT prewarm path."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest

import server.app as app_module
from server.app import create_app
from server.engines import warmup as engine_warmup
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

    async def fake(_registry, _settings, *, requested, cancellation_callback, admission_callback):
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


def test_running_solve_blocks_new_prewarm_but_does_not_cancel_an_admitted_one(app, monkeypatch):
    app.state.jobs_runtime = SimpleNamespace(running_job_ids=set())
    callbacks = []

    async def fake(_registry, _settings, *, cancellation_callback, admission_callback, **kwargs):
        callbacks.append((cancellation_callback, admission_callback))
        await asyncio.Event().wait()

    monkeypatch.setattr(app_module, "beat_worker_prewarm", fake)

    async def scenario():
        await app.state.prewarm_beat_worker("beat-cpu")
        async with asyncio.timeout(2):
            while not callbacks:
                await asyncio.sleep(0.001)
        cancel, admission = callbacks[0]
        app.state.jobs_runtime.running_job_ids.add("solve")
        cancel()  # An admitted warmup can finish and the solve waits for its worker.
        with pytest.raises(SessionCancelled, match="deferred"):
            admission()
        await app.state.prewarm_beat_worker("metal")
        with pytest.raises(SessionCancelled, match="cancelled"):
            cancel()
        task = app.state.beat_prewarm_task
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

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


def test_automatic_prewarm_starts_cpu_setup_only_after_running_solve(app, monkeypatch):
    from server.solver import beat_cpu_runtime

    started = []
    app.state.jobs_runtime = SimpleNamespace(running_job_ids={'real-solve'})
    monkeypatch.setattr(beat_cpu_runtime, 'cpu_preparation_in_flight', lambda: False)
    monkeypatch.setattr(beat_cpu_runtime, 'start_selected_provisioning',
                        lambda engine, **kwargs: started.append(engine))
    monkeypatch.setattr(app_module, 'resolve_prewarm_engine', lambda *a, **k: asyncio.sleep(0, 'bempp'))
    monkeypatch.setattr(engine_warmup, 'prewarm_bempp_worker_for_engine', lambda engine: None)

    async def scenario():
        status, _, _ = await _request_with_headers(app, 'POST', '/api/runtimes/beat/prewarm',
                                                  body={'engine': 'auto'})
        assert status == 202
        await asyncio.sleep(0.02)
        assert not started  # Never start setup, or change solver, under a running solve.
        app.state.jobs_runtime.running_job_ids = set()
        await app.state.beat_prewarm_task
        assert started == ['auto']

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ('namespace', 'saved'),
    [('solveOptions', '{"state":{"engine":"metal"}}'), ('enginePreference', '{"engine":"metal"}')],
    ids=['legacy-solve-options', 'engine-preference'],
)
def test_an_automatic_setup_does_not_start_cpu_setup_when_the_user_chose_metal(
    app, monkeypatch, caplog, namespace, saved,
):
    """23 Nov: opening a finished Metal CAD run posted its setup's Automatic.

    The user's solver was Metal, yet the prewarm started an 83 s BEAT CPU
    Julia compile. Automatic's CPU setup follows the user's own choice; the
    engine Automatic resolves to is still warmed.
    """
    import logging

    from server.solver import beat_cpu_runtime

    app.state.settings.put(namespace, saved)
    started, warmed = [], []
    monkeypatch.setattr(beat_cpu_runtime, 'cpu_preparation_in_flight', lambda: False)
    monkeypatch.setattr(beat_cpu_runtime, 'start_selected_provisioning',
                        lambda engine, **kwargs: started.append(engine))
    monkeypatch.setattr(app_module, 'resolve_prewarm_engine', lambda *a, **k: asyncio.sleep(0, 'metal'))

    async def prewarm_solver(engine=None):
        warmed.append(engine)

    monkeypatch.setattr(app_module, 'prewarm_solver', prewarm_solver)

    async def scenario():
        status, _, _ = await _request_with_headers(app, 'POST', '/api/runtimes/beat/prewarm',
                                                  body={'engine': 'auto'})
        assert status == 202
        await app.state.beat_prewarm_task

    with caplog.at_level(logging.INFO, logger='wg.solver.warmup'):
        asyncio.run(scenario())
    assert started == []
    assert warmed == ['metal']
    messages = [record.getMessage() for record in caplog.records]
    assert 'BEAT worker prewarm requested for auto' in messages
    assert 'Automatic BEAT CPU setup not started: the selected solver is metal' in messages


def test_choosing_automatic_starts_cpu_setup_before_the_settings_upload_lands(app, monkeypatch):
    """The picker posts at once; the page uploads the preference 400 ms later.

    The server still holds the previous choice (Metal) when the prewarm runs,
    so the page's own preference, carried by the request, decides.
    """
    from server.solver import beat_cpu_runtime

    app.state.settings.put('enginePreference', '{"engine":"metal"}')
    started = []
    monkeypatch.setattr(beat_cpu_runtime, 'cpu_preparation_in_flight', lambda: False)
    monkeypatch.setattr(beat_cpu_runtime, 'start_selected_provisioning',
                        lambda engine, **kwargs: started.append(engine))
    monkeypatch.setattr(app_module, 'resolve_prewarm_engine', lambda *a, **k: asyncio.sleep(0, 'metal'))

    async def prewarm_solver(engine=None):
        return None

    monkeypatch.setattr(app_module, 'prewarm_solver', prewarm_solver)

    async def scenario():
        status, _, _ = await _request_with_headers(app, 'POST', '/api/runtimes/beat/prewarm',
                                                  body={'engine': 'auto', 'preference': 'auto'})
        assert status == 202
        await app.state.beat_prewarm_task

    asyncio.run(scenario())
    assert started == ['auto']


def test_an_explicit_cpu_choice_still_starts_its_setup(app, monkeypatch):
    """Only Automatic defers to the user's choice; a named BEAT backend is the choice."""
    from server.solver import beat_cpu_runtime

    app.state.settings.put('enginePreference', '{"engine":"metal"}')
    started = []
    monkeypatch.setattr(beat_cpu_runtime, 'cpu_preparation_in_flight', lambda: False)
    monkeypatch.setattr(beat_cpu_runtime, 'start_selected_provisioning',
                        lambda engine, **kwargs: started.append(engine))

    async def fake(_registry, _settings, **kwargs):
        return None

    monkeypatch.setattr(app_module, 'beat_worker_prewarm', fake)

    async def scenario():
        await app.state.prewarm_beat_worker('beat-cpu')
        await app.state.beat_prewarm_task

    asyncio.run(scenario())
    assert started == ['beat-cpu']


def test_unavailable_runtime_never_launches_a_warmup(monkeypatch):
    monkeypatch.delenv('WG2_SOLVER_WARMUP', raising=False)
    monkeypatch.setattr(official_beat, 'production_statuses', lambda **kwargs: {
        'metal': {'available': False, 'reason': 'not provisioned'}})
    monkeypatch.setattr(engine_warmup, '_warm_beat', lambda *a, **k: pytest.fail('unavailable runtime warmed'))
    assert not engine_warmup.prewarm_beat_worker_for_engine('beat-metal', cancellation_callback=lambda: None)


def test_named_selection_reuses_existing_backend_hook(monkeypatch):
    from server.engines.registry import EngineRegistry
    from server.solver import beat_cpu_runtime

    calls = []
    monkeypatch.setattr(beat_cpu_runtime, 'cpu_preparation_in_flight', lambda: False)
    monkeypatch.setattr(engine_warmup, 'prewarm_beat_worker_for_engine',
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

    monkeypatch.setattr(engine_warmup, '_warm_beat', warm)
    assert engine_warmup.prewarm_beat_worker_for_engine('beat-metal', cancellation_callback=lambda: None)
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


def test_picker_warms_metal_through_the_native_hook(app, monkeypatch):
    calls = []

    async def resolve(_registry, requested):
        return requested

    async def warm(engine):
        calls.append(engine)

    monkeypatch.setattr(app_module, "resolve_prewarm_engine", resolve)
    monkeypatch.setattr(app_module, "prewarm_solver", warm)

    async def scenario():
        status, _, _ = await _request_with_headers(app, 'POST', '/api/runtimes/beat/prewarm',
                                                  body={'engine': 'metal'})
        assert status == 202
        await app.state.beat_prewarm_task
        assert calls == ['metal']

    asyncio.run(scenario())
