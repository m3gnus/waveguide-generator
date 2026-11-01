"""Outbound delivery evidence, delayed receipts and command-only returns."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from server.cadlink import api
from server.cadlink.fusion.fusion_outcomes import settle_reported_outcomes
from server.cadlink.fusion.fusion_status import read_fusion_status
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
        runtime=SimpleNamespace(start=start), job_store=None))
    return store, state, events


def _operation(state):
    return asyncio.run(api.get_cad_operation('update-1', SimpleNamespace(app=SimpleNamespace(state=state))))


def test_operation_endpoint_reports_waiting_claimed_and_correlated_result(monkeypatch, tmp_path):
    store, state, events = _state(tmp_path, monkeypatch)
    _report(tmp_path)
    result = _operation(state)
    assert result['fusionDelivery'] == 'waiting' and result['state'] == 'received'
    assert 'Finish any active' in result['message']
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
    assert not events and store.get_operation('update-1')['state'] == 'received'
    # No browser read for 45 seconds; terminal receipt survives the live TTL.
    _report(tmp_path, age=45, outcome='applied')
    assert asyncio.run(api._publish_request_status(state, None)) is None
    assert events[-1]['kind'] == 'cadOperation'
    assert events[-1]['operation']['fusionDelivery'] == 'applied'
    assert store.get_operation('update-1')['state'] == 'accepted'


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


@pytest.mark.parametrize('age', [0, 60])
def test_return_route_refuses_command_observation_with_send_remedy(monkeypatch, tmp_path, age):
    _, state, _ = _state(tmp_path, monkeypatch)
    _report(tmp_path, age=age)
    monkeypatch.setattr(api, 'fusion_process_running', lambda: False)
    monkeypatch.setattr(api, 'publish_return_request', lambda *_, **__: pytest.fail('must not publish'))
    payload = api.FusionReturnRequest(designId='wgd_1', documentId='fusion:doc-1',
                                      instanceId='instance-1', expectedReturnStateHash='sha256:x')
    with pytest.raises(HTTPException) as raised:
        asyncio.run(api.request_fusion_return(payload, SimpleNamespace(app=SimpleNamespace(state=state))))
    assert raised.value.status_code == 409 and 'Press Send in Fusion' in raised.value.detail


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
