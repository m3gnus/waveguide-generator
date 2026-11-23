"""Outbound delivery evidence, delayed receipts and command-only returns."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from server.cadlink import api
from server.cadlink.fusion.fusion_outcomes import settle_reported_outcomes
from server.cadlink.fusion.fusion_status import read_fusion_status
from server.cadlink.fusion.recovery_api import recovery_notices
from test_cad_fusion_outcomes import _accept, _heartbeat, _ipc, _store


def _report(data, *, age=0, pickup=True, outcome=None, message=None):
    report = _heartbeat(recent=([{
        'channel': 'handoff', 'requestId': 'update-1', 'outcome': outcome,
        'message': message, 'exportId': 'wge_1', 'documentId': 'fusion:doc-1',
    }] if outcome else []), last={'channel': 'snapshot', 'correlationId': 'unrelated', 'outcome': 'requested'})
    report.update(schemaVersion=1, cadApplication='fusion360', sessionId='session',
                  updatedAt=(datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat())
    report['diagnostics']['activation'] = {
        'automaticCoordination': False, 'setting': 'default', 'settingsKey': 'automatic_coordination',
        **({'wgRequestPickup': 1} if pickup else {}),
    }
    path = _ipc(data) / '.fusion-status.json'
    path.write_text(json.dumps(report))
    return report


def _state(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _accept(store)
    events = []
    state = SimpleNamespace(data_dir=tmp_path, cadlink_store=store,
        jobs_runtime=SimpleNamespace(events=SimpleNamespace(publish=events.append)),
        cad_request_status=SimpleNamespace(tick=lambda _: None))
    async def start(): pass
    monkeypatch.setattr(api, '_preparation_context', lambda _: SimpleNamespace(
        store=store, runtime=SimpleNamespace(start=start), job_store=None))
    return store, state, events


def _operation(state):
    return asyncio.run(api.get_cad_operation('update-1', SimpleNamespace(app=SimpleNamespace(state=state))))


def test_operation_endpoint_reports_waiting_claimed_and_correlated_result(monkeypatch, tmp_path):
    store, state, events = _state(tmp_path, monkeypatch)
    _report(tmp_path)
    result = _operation(state)
    assert result['fusionDelivery'] == 'waiting' and result['state'] == 'received'
    assert 'Finish any active' in result['message']
    assert 'switch to Solid' in result['message']
    # A missing file has still not proved pickup.
    assert store.get_operation('update-1')['attempt_generation'] == 0
    _report(tmp_path, outcome='claimed')
    result = _operation(state)
    assert result['fusionDelivery'] == 'claimed' and result['attemptGeneration'] == 1
    _report(tmp_path, outcome='applied')
    assert _operation(state)['fusionDelivery'] == 'applied'


@pytest.mark.parametrize('outcome,delivery,state_value,reason', [
    ('refused', 'refused', 'rejected', 'adapter_refused'),
    ('expired', 'expired', 'cancelled', 'expired'),
    ('recoveryRequired', 'uncertain', 'recovery_required', None),
    ('alreadyCurrent', 'cancelled', 'cancelled', 'adapter_not_started'),
    ('failed', 'refused', 'rejected', 'adapter_failed'),
])
def test_retained_terminal_receipt_settles_after_heartbeat_expiry_and_restart(
        monkeypatch, tmp_path, outcome, delivery, state_value, reason):
    store, state, _ = _state(tmp_path, monkeypatch)
    _report(tmp_path, age=45, outcome=outcome, message='Finish or repair the link, then send again.')
    store.close()
    state.cadlink_store = _store(tmp_path)
    result = _operation(state)
    assert (result['fusionDelivery'], result['state'], result['reason']) == (delivery, state_value, reason)
    assert 'then send again' in result['message']
    assert result['message'] != 'Fusion refused this request.'


def test_existing_delivery_loop_reads_late_completion_without_status_endpoint(monkeypatch, tmp_path):
    store, state, events = _state(tmp_path, monkeypatch)
    assert api._DELIVERY_INTERVAL_S < 20
    assert asyncio.run(api._publish_request_status(state, None)) is None
    assert [event['operation']['fusionDelivery'] for event in events] == ['waiting']
    assert store.get_operation('update-1')['state'] == 'received'
    # No browser read for 45 seconds; terminal receipt survives the live TTL.
    _report(tmp_path, age=45, outcome='applied')
    assert asyncio.run(api._publish_request_status(state, None)) is None
    assert events[-1]['kind'] == 'cadOperation'
    assert events[-1]['operation']['fusionDelivery'] == 'applied'
    assert store.get_operation('update-1')['state'] == 'accepted'


def test_recovery_poll_settles_before_delivery_pass_pushes_applied_once(monkeypatch, tmp_path):
    store, state, events = _state(tmp_path, monkeypatch)
    assert asyncio.run(api._publish_request_status(state, None)) is None
    events.clear()
    _report(tmp_path, outcome='applied')
    recovery_notices(store, tmp_path)
    assert store.get_operation('update-1')['state'] == 'accepted'
    assert not events

    assert asyncio.run(api._publish_request_status(state, None)) is None
    assert [event['operation']['fusionDelivery'] for event in events] == ['applied']
    assert not state.cad_fusion_reported
    assert asyncio.run(api._publish_request_status(state, None)) is None
    assert len(events) == 1


@pytest.mark.parametrize('route', ['detail', 'list'])
def test_get_settles_before_delivery_pass_pushes_applied_once(monkeypatch, tmp_path, route):
    store, state, events = _state(tmp_path, monkeypatch)
    assert asyncio.run(api._publish_request_status(state, None)) is None
    events.clear()
    _report(tmp_path, outcome='applied')
    if route == 'detail':
        assert _operation(state)['fusionDelivery'] == 'applied'
    else:
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        result = asyncio.run(api.list_cad_operations(request, pending=False, limit=100))
        assert result['operations'][0]['fusionDelivery'] == 'applied'
    assert not events
    assert asyncio.run(api._publish_request_status(state, None)) is None
    assert [event['operation']['fusionDelivery'] for event in events] == ['applied']
    assert asyncio.run(api._publish_request_status(state, None)) is None
    assert len(events) == 1


def test_delivery_pass_pushes_received_to_claimed_once(monkeypatch, tmp_path):
    _, state, events = _state(tmp_path, monkeypatch)
    assert asyncio.run(api._publish_request_status(state, None)) is None
    events.clear()
    _report(tmp_path, outcome='claimed')
    assert asyncio.run(api._publish_request_status(state, None)) is None
    assert [event['operation']['fusionDelivery'] for event in events] == ['claimed']
    assert events[0]['operation']['attemptGeneration'] == 1
    assert asyncio.run(api._publish_request_status(state, None)) is None
    assert len(events) == 1


@pytest.mark.parametrize('prior_pass', [False, True])
def test_delivery_pass_discovers_operation_already_settled_before_first_seen(
        monkeypatch, tmp_path, prior_pass):
    store, state, events = _state(tmp_path, monkeypatch)
    if prior_pass:
        assert asyncio.run(api._publish_request_status(state, None)) is None
        _accept(store, operation_id='between-passes')
    report = _report(tmp_path, outcome='applied')
    operation_id = 'between-passes' if prior_pass else 'update-1'
    report['diagnostics']['recentOutcomes'][0]['requestId'] = operation_id
    (_ipc(tmp_path) / '.fusion-status.json').write_text(json.dumps(report))
    recovery_notices(store, tmp_path)
    assert store.get_operation(operation_id)['state'] == 'accepted'
    events.clear()

    assert asyncio.run(api._publish_request_status(state, None)) is None
    assert [(event['operation']['operationId'], event['operation']['fusionDelivery'])
            for event in events] == [(operation_id, 'applied')]
    assert operation_id not in state.cad_fusion_reported
    assert asyncio.run(api._publish_request_status(state, None)) is None
    assert len(events) == 1


def test_old_claim_report_is_not_treated_as_a_current_claim(monkeypatch, tmp_path):
    store, state, _ = _state(tmp_path, monkeypatch)
    _report(tmp_path, age=45, outcome='claimed')
    assert _operation(state)['fusionDelivery'] == 'waiting'


@pytest.mark.parametrize('pickup', [True, False])
def test_last_report_distinguishes_missing_pickup_capability_from_silence(monkeypatch, tmp_path, pickup):
    _, state, _ = _state(tmp_path, monkeypatch)
    _report(tmp_path, age=60, pickup=pickup)
    status = read_fusion_status(tmp_path, current_design_hash='', current_formula='', design_id=None,
                                process_running=False)
    assert status['wgRequestPickup'] == (1 if pickup else None)
    message = _operation(state)['message']
    assert ('Install the updated' in message) is (not pickup)
    assert ('Waiting for Fusion' in message) is pickup


def test_version_033_fresh_last_request_still_settles(monkeypatch, tmp_path):
    store, _, _ = _state(tmp_path, monkeypatch)
    report = _report(tmp_path, pickup=False)
    report['adapterVersion'] = '0.3.3'
    report['diagnostics']['lastRequest'] = {'channel': 'handoff', 'correlationId': 'update-1',
        'outcome': 'refused', 'message': 'Wrong document. Refresh CAD Link and send again.'}
    (_ipc(tmp_path) / '.fusion-status.json').write_text(json.dumps(report))
    assert settle_reported_outcomes(store, tmp_path) == 1
    assert store.get_operation('update-1')['state'] == 'rejected'


@pytest.mark.parametrize('automatic', [True, False])
def test_an_old_heartbeat_reporting_automatic_coordination_is_read_harmlessly(monkeypatch, tmp_path, automatic):
    """Pull from WG is retired, and with it the add-in's automatic coordination
    that served it. An add-in that still reports it is read like any other."""
    store, state, _ = _state(tmp_path, monkeypatch)
    report = _report(tmp_path, outcome='refused', message='Wrong document. Refresh CAD Link and send again.')
    report['diagnostics']['activation']['automaticCoordination'] = automatic
    (_ipc(tmp_path) / '.fusion-status.json').write_text(json.dumps(report))
    status = read_fusion_status(tmp_path, current_design_hash='', current_formula='', design_id=None,
                                process_running=True)
    assert status['running'] is True and status['wgRequestPickup'] == 1
    assert settle_reported_outcomes(store, tmp_path) == 1
    assert store.get_operation('update-1')['state'] == 'rejected'
    assert _operation(state)['fusionDelivery'] == 'refused'


def test_startup_history_publication_is_read_only_and_does_not_repeat(monkeypatch, tmp_path):
    store, state, events = _state(tmp_path, monkeypatch)
    _report(tmp_path, outcome='applied')
    assert settle_reported_outcomes(store, tmp_path) == 1
    before = store.get_operation('update-1')
    assert before['state'] == 'accepted'
    # This is the existing delivery pass, against a fresh process state.
    assert not hasattr(state, 'cad_fusion_cursors')
    for _ in range(3):
        assert asyncio.run(api._publish_request_status(state, None)) is None
    assert store.get_operation('update-1') == before
    assert len(events) == 1
    assert events[0]['operation']['fusionDelivery'] == 'applied'
    assert state.cad_fusion_reported == {}


def test_disabled_solve_intake_keeps_observing_outbound_results(monkeypatch, tmp_path):
    store, state, events = _state(tmp_path, monkeypatch)
    _report(tmp_path, age=45, outcome='applied')
    monkeypatch.setenv(api.CAD_DELIVERY_ENV, '0')
    async def no_intake(*_, **__):
        pytest.fail('disabled intake must not run')
    monkeypatch.setattr(api, 'run_delivery_pass', no_intake)
    async def exercise():
        reported = asyncio.Event()
        def publish(event):
            events.append(event)
            if event.get('kind') == 'cadOperation':
                reported.set()
        state.jobs_runtime.events.publish = publish
        await api._deliver_solve_commands(SimpleNamespace(state=state))()
        try:
            await asyncio.wait_for(reported.wait(), 2)
            assert store.get_operation('update-1')['state'] == 'accepted'
        finally:
            state.cad_delivery_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await state.cad_delivery_task
    asyncio.run(exercise())


def test_pending_list_asks_the_store_only_for_unfinished_non_solve_rows(monkeypatch, tmp_path):
    from server.cadlink.operations import PREPARE_AND_SOLVE, STATES, TERMINAL_STATES

    store, state, _events = _state(tmp_path, monkeypatch)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    assert [item['operationId'] for item in
            asyncio.run(api.list_cad_operations(request, pending=True, limit=100))['operations']] == ['update-1']
    _report(tmp_path, outcome='applied')
    asked: dict[str, set[str]] = {}
    page = store.operation_page

    def spy(*, kind, states, **kwargs):
        asked[kind] = set(states)
        return page(kind=kind, states=states, **kwargs)

    monkeypatch.setattr(store, 'operation_page', spy)
    assert asyncio.run(api.list_cad_operations(request, pending=True, limit=100))['operations'] == []
    # A solve's state comes from its job, so every solve row is still read.
    assert asked.pop(PREPARE_AND_SOLVE) == set(STATES)
    assert asked and all(states == set(STATES) - TERMINAL_STATES for states in asked.values())
    listed = asyncio.run(api.list_cad_operations(request, pending=False, limit=100))['operations']
    assert [item['operationId'] for item in listed] == ['update-1']
