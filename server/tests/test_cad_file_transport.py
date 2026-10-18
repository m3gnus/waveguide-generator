"""File transport after removal of the HTTP session, including old claims."""
from datetime import datetime, timedelta, timezone
import asyncio
import json
from types import SimpleNamespace

import pytest

from server.app import create_app
from server.cadlink.fusion import addin_update
from server.cadlink.joblane import preparation
from server.cadlink import api as cadlink_api
from server.cadlink.intake import solve_command
from server.cadlink.api import post_reconcile_cad_operation
from server.cadlink.fusion.fusion_delivery import capabilities
from server.cadlink.ipc import ipc_folder
from server.cadlink.fusion.fusion_outcomes import LEGACY_LIVE_CLAIM_MAX_AGE, settle_abandoned_live_claims, settle_from_heartbeat
from server.cadlink.fusion.fusion_status import select_heartbeat
from server.cadlink.store import CadLinkStore
from server.cadlink.intake.ingest import retained_snapshot_path
from server.cadlink.operations import RECEIVE_SNAPSHOT
from server.tests.cad_file_harness import STARTUP, _snapshot_digest, wg
from server.tests.test_cad_preparation import _write_return
from server.tests.test_cad_inbox_stall import V4_SNAPSHOT, drop, fixture
from server.tests.test_cad_fusion_outcomes import _accept, _heartbeat

NOW = datetime.now(timezone.utc)


def _legacy(store, kind='update_link', stage='executing', *, age=timedelta(hours=1), claim=True):
    row = _accept(store, 'old-claim', kind)
    store.claim(row['operation_id'], 0)
    # Historical record from an older WG; new WG cannot create an HTTP claim.
    with store._lock, store._transaction() as conn:
        conn.execute("UPDATE cad_operations SET stage = ?, updated_at = ?, claim_json = ? WHERE operation_id = ?",
                     (stage, (NOW - age).isoformat(), json.dumps({'liveSessionId': 'old-session', 'claimId': 'claim'}) if claim else None,
                      row['operation_id']))
    return row['operation_id']


@pytest.mark.parametrize('kind,state', [('insert_link', 'recovery_required'), ('update_link', 'recovery_required'), ('request_return', 'rejected')])
@pytest.mark.parametrize('stage', ['adapter-received', 'queued-for-fusion', 'executing'])
def test_old_live_claims_finish_without_repeating_mutations(tmp_path, kind, state, stage):
    store = CadLinkStore.for_data_dir(tmp_path)
    operation_id = _legacy(store, kind, stage)
    assert settle_abandoned_live_claims(store, now=NOW) == 1
    result = store.get_operation(operation_id)
    assert result['state'] == state
    assert result['attempt_generation'] == 1
    assert 'interrupted' in json.loads(result['outcome_json'])['message']
    assert settle_abandoned_live_claims(store, now=NOW) == 0
    store.close()
    reopened = CadLinkStore.for_data_dir(tmp_path)
    assert reopened.get_operation(operation_id)['state'] == state
    reopened.close()


@pytest.mark.parametrize('age,claim', [(LEGACY_LIVE_CLAIM_MAX_AGE - timedelta(seconds=1), True), (timedelta(hours=1), False), (timedelta(seconds=-1), True)])
def test_recent_or_file_claims_are_not_timed_out(tmp_path, age, claim):
    store = CadLinkStore.for_data_dir(tmp_path)
    _legacy(store, age=age, claim=claim)
    assert settle_abandoned_live_claims(store, now=NOW) == 0
    assert store.get_operation('old-claim')['state'] == 'processing'
    store.close()


def test_exact_file_evidence_wins_over_legacy_timeout(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    _legacy(store)
    assert settle_from_heartbeat(store, _heartbeat(links=[{'operationId': 'old-claim', 'exportId': 'wge_1'}]), ipc_folder(tmp_path)) == 1
    assert settle_abandoned_live_claims(store, now=NOW) == 0
    assert store.get_operation('old-claim')['state'] == 'accepted'
    store.close()


@pytest.mark.parametrize('evidence', ['recent', 'last'])
def test_failed_return_is_settled_by_heartbeat_without_return_poll(tmp_path, evidence):
    store = CadLinkStore.for_data_dir(tmp_path)
    operation_id = _legacy(store, 'request_return')
    report = {'channel': 'returnRequest', 'outcome': 'failed',
              ('requestId' if evidence == 'recent' else 'correlationId'): operation_id}
    heartbeat = _heartbeat(**{evidence: [report] if evidence == 'recent' else report})
    assert settle_from_heartbeat(store, heartbeat, ipc_folder(tmp_path)) == 1
    assert store.get_operation(operation_id)['state'] == 'rejected'
    assert store.get_operation(operation_id)['reason'] == 'adapter_failed'
    store.close()


def test_startup_retires_endpoint_and_recovers_old_claim_without_heartbeat(tmp_path):
    app = create_app(data_dir=tmp_path, advertised_port=3100)
    _legacy(app.state.cadlink_store)
    endpoint = ipc_folder(tmp_path, create=True) / 'wg-endpoint.json'
    endpoint.write_text('{"liveProtocol":1}')
    handler = next(h for h in app.router.on_startup if h.__name__ == 'advertise_fusion_delivery_on_startup')
    asyncio.run(handler())
    assert app.state.cadlink_store.get_operation('old-claim')['state'] == 'recovery_required'
    assert not endpoint.exists()
    assert 'liveProtocol' not in json.loads((endpoint.parent / 'wg-capabilities.json').read_text())
    assert not any(route.path.startswith('/api/cadlink/live') for route in app.routes)
    assert not any(path.startswith('/api/cadlink/live') for path in app.openapi()['paths'])
    app.state.cadlink_store.close()


def test_reconcile_reaches_timeout_without_fusion_starting_again(tmp_path):
    app = create_app(data_dir=tmp_path)
    _legacy(app.state.cadlink_store)
    request = SimpleNamespace(app=app)
    result = asyncio.run(post_reconcile_cad_operation('old-claim', request))
    assert result.operation.state == 'recovery_required'
    app.state.cadlink_store.close()


def _status(tmp_path, **extra):
    status = {'schemaVersion': 1, 'cadApplication': 'fusion360', 'deliveryVersion': 3,
              'updatedAt': datetime.now(timezone.utc).isoformat(), 'adapterVersion': '0.2.1', **extra}
    (ipc_folder(tmp_path, create=True) / '.fusion-status.json').write_text(json.dumps(status))


def test_loaded_identity_uses_file_version_and_never_infers_installed_commit(tmp_path):
    _status(tmp_path)
    assert select_heartbeat(tmp_path)[1] == 'file'
    assert addin_update.loaded_addin_identity(tmp_path) == {
        'addinVersion': '0.2.1', 'sourceCommit': None, 'matchesPin': None}
    _status(tmp_path, updatedAt=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    assert addin_update.loaded_addin_identity(tmp_path) is None


def test_loaded_dev_identity_uses_only_reported_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(addin_update, 'pinned_commit', lambda _root: 'pinned')
    _status(tmp_path, diagnostics={'source': {'sourceCommit': 'pinned'}})
    assert addin_update.loaded_addin_identity(tmp_path)['matchesPin'] is True
    _status(tmp_path, diagnostics={'source': {'sourceCommit': 'other'}})
    assert addin_update.loaded_addin_identity(tmp_path)['matchesPin'] is False


def test_timed_out_mutation_is_discoverable_as_a_recovery_notice(tmp_path):
    from server.cadlink.fusion.recovery_api import recovery_notices
    store = CadLinkStore.for_data_dir(tmp_path)
    _legacy(store)
    settle_abandoned_live_claims(store, now=NOW)
    notices = recovery_notices(store, tmp_path).notices
    assert [(n.id, n.kind, n.document_id) for n in notices] == [('old-claim', 'update_link', 'fusion:doc-1')]
    store.close()


def test_retired_coordination_environment_cannot_enable_a_live_layer(tmp_path, monkeypatch):
    monkeypatch.setenv('WG2_CAD_COORDINATION', 'on')
    app = create_app(data_dir=tmp_path)
    assert not hasattr(app.state, 'cad_coordination')
    assert 'liveProtocol' not in capabilities()
    app.state.cadlink_store.close()


RECOVER = ("recover_cad_operations_on_startup",)


def _accepted_before_crash(app, bundle_path, manifest, operation_id="snap-1"):
    item = solve_command.DeliveredItem(
        operation_id=operation_id, kind=RECEIVE_SNAPSHOT, bundle_path=bundle_path,
        manifest_sha256=manifest,
    )
    assert solve_command.accept_delivery(app.store, item, retain=None).result == "created"


def test_snapshot_file_retains_before_ack_and_replays_without_preparing(tmp_path, monkeypatch):
    def no_ingest(*args, **kwargs):
        raise AssertionError("a received snapshot must never be ingested")

    monkeypatch.setattr(cadlink_api, "ingest_bundle", no_ingest)
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        body = fixture(V4_SNAPSHOT, bundlePath=bundle_path, manifestSha256=manifest)
        drop(app.data_dir, body)
        assert app.run_pass() == []
        row = app.row(body["operationId"])
        assert (row["kind"], row["state"]) == (RECEIVE_SNAPSHOT, "accepted")
        assert row["request_digest"] == _snapshot_digest(bundle_path, manifest)
        assert row["job_id"] is None and row["setup_revision_id"] is None
        assert retained_snapshot_path(app.data_dir, manifest).is_dir()
        ack_path = ipc_folder(app.data_dir) / ".wg-solve-acks" / (body["commandId"] + ".json")
        ack = json.loads(ack_path.read_text())
        assert ack["outcome"] == "accepted"
        drop(app.data_dir, {**body, "requestedAt": "2026-09-17T10:05:00Z"})
        assert app.run_pass() == []
        assert app.row(body["operationId"])["attempt_generation"] == row["attempt_generation"]
        assert app.ids() == [body["operationId"]]
        # A different digest cannot replace this terminal operation or its ack.
        drop(app.data_dir, {**body, "manifestSha256": "sha256:" + "0" * 64})
        refused = []
        solve_command.collect_solve_deliveries(app.data_dir, app.store, refuse=refused.append)
        assert refused and refused[0]["operationId"] == body["operationId"]
        assert app.row(body["operationId"])["request_digest"] == row["request_digest"]
        # A replay republishes the same outcome; only its second-resolution
        # timestamp may move, so the passes may straddle a second boundary.
        replayed = json.loads(ack_path.read_text())
        assert {k: v for k, v in replayed.items() if k != "at"} == {k: v for k, v in ack.items() if k != "at"}


def test_invalid_snapshot_file_is_rejected_without_retaining_or_preparing(tmp_path):
    with wg(tmp_path) as app:
        bundle_path, _ = _write_return(app.workspace)
        body = fixture(V4_SNAPSHOT, bundlePath=bundle_path, manifestSha256="sha256:" + "0" * 64)
        drop(app.data_dir, body)
        assert app.run_pass() == []
        row = app.row(body["operationId"])
        assert (row["state"], row["reason"]) == ("rejected", "snapshot_invalid")
        assert row["snapshot_json"] is None and row["job_id"] is None


def test_snapshot_settlement_fences_an_attempt_left_by_a_crash(tmp_path):
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        _accepted_before_crash(app, bundle_path, manifest)
        stale = app.store.claim("snap-1", 0)
        assert stale == 1
        assert app.settle() == ["snap-1"]
        assert app.store.record_outcome("snap-1", stale, "rejected", reason="snapshot_invalid") is None
        row = app.row("snap-1")
        assert (row["state"], row["attempt_generation"]) == ("accepted", 2)


@pytest.mark.parametrize("change", ["present", "missing", "changed"])
@pytest.mark.parametrize("claimed", [False, True], ids=["received", "processing"])
def test_snapshot_settles_after_reopening_store(tmp_path, change, claimed):
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        _accepted_before_crash(app, bundle_path, manifest)
        if claimed:
            assert app.store.claim("snap-1", 0) == 1
        workspace = app.workspace
    bundle = workspace / bundle_path
    if change == "missing":
        bundle.rename(workspace / "gone")
    elif change == "changed":
        manifest_file = bundle / "wgreturn.json"
        manifest_file.write_bytes(manifest_file.read_bytes() + b" ")
    with wg(tmp_path, select=False, startup=STARTUP + RECOVER) as restarted:
        row = restarted.row("snap-1")
        if change == "present":
            assert (row["state"], row["attempt_generation"]) == ("accepted", 2 if claimed else 1)
            assert retained_snapshot_path(restarted.data_dir, manifest).is_dir()
        elif change == "changed":
            assert (row["state"], row["reason"]) == ("rejected", "snapshot_invalid")
        else:
            assert row["state"] == ("processing" if claimed else "received")
            assert row["snapshot_json"] is None
            (workspace / "gone").rename(bundle)
            restarted.run_pass()
            assert restarted.row("snap-1")["state"] == "accepted"


def test_snapshot_settlement_reaches_readable_items_beyond_unreadable_page(tmp_path):
    with wg(tmp_path) as app:
        for index in range(preparation._SNAPSHOT_PAGE + 5):
            _accepted_before_crash(app, f"wgreturn/missing-{index}.wgreturn",
                                   "sha256:" + "e" * 64, f"old-{index:03d}")
        bundle_path, manifest = _write_return(app.workspace)
        _accepted_before_crash(app, bundle_path, manifest, "newest")
        assert app.settle() == ["newest"]
        assert app.row("newest")["state"] == "accepted"
        assert app.row("old-000")["state"] == "received"


def test_snapshot_unreadable_bound_survives_restart_and_file_replay(tmp_path, monkeypatch):
    wall = datetime(2026, 9, 17, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(preparation, "_wall_now", lambda: wall)
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        (app.workspace / bundle_path).rename(app.workspace / "gone")
        _accepted_before_crash(app, bundle_path, manifest)
        assert app.settle() == []
        first = app.row("snap-1")["snapshot_unreadable_since"]
        assert first == wall.isoformat()
        wall += timedelta(hours=23, minutes=59)
        assert app.settle() == []
        assert app.row("snap-1")["snapshot_unreadable_since"] == first
    wall += timedelta(minutes=1)
    with wg(tmp_path, select=False, startup=STARTUP + RECOVER) as restarted:
        row = restarted.row("snap-1")
        assert (row["state"], row["reason"]) == ("rejected", "snapshot_unavailable")
        body = fixture(V4_SNAPSHOT, operationId="snap-1", commandId="snap-1",
                       bundlePath=bundle_path, manifestSha256=manifest)
        drop(restarted.data_dir, body)
        assert restarted.run_pass() == []
        assert restarted.row("snap-1")["state"] == "rejected"
        assert restarted.row("snap-1")["reason"] == "snapshot_unavailable"


def test_status_settles_and_reports_one_file_heartbeat_when_file_changes(tmp_path, monkeypatch):
    from server.cadlink import api
    from server.cadlink.fusion import fusion_status

    wall = datetime.now(timezone.utc)
    selections, settled = [], []
    real_select = select_heartbeat
    real_settle = settle_from_heartbeat

    def select(*args, **kwargs):
        selected = real_select(*args, **kwargs)
        selections.append(selected)
        return selected

    def settle_then_change(store, heartbeat, ipc, **kwargs):
        nonlocal wall
        settled.append(heartbeat)
        _status(tmp_path, updatedAt=wall.isoformat(), document={'id': 'fusion:changed', 'links': []})
        result = real_settle(store, heartbeat, ipc, **kwargs)
        wall += fusion_status.FUSION_STATUS_TTL + timedelta(seconds=1)
        return result

    monkeypatch.setattr(api, 'select_heartbeat', select)
    monkeypatch.setattr(fusion_status, 'select_heartbeat', select)
    monkeypatch.setattr(api, 'settle_from_heartbeat', settle_then_change)
    monkeypatch.setattr(api, 'heartbeat_now', lambda: wall)
    monkeypatch.setattr(fusion_status, '_utc_now', lambda: wall)
    monkeypatch.setattr(api, 'fusion_process_running', lambda: False)
    monkeypatch.setattr(api, 'last_refresh', lambda: None)
    app = create_app(data_dir=tmp_path)
    try:
        _status(tmp_path, updatedAt=wall.isoformat(), document={'id': 'fusion:first', 'links': []})
        payload = api.FusionStatusRequest.model_validate({'design': {'formula': 'OSSE', 'L': 120, 'a': 45}})
        status = asyncio.run(api.fusion_status(payload, SimpleNamespace(app=app)))
        assert len(selections) == len(settled) == 1
        assert settled[0] is selections[0][0]
        assert (status['documentId'], status['state'], status['heartbeatTransport']) == ('fusion:first', 'not_linked', 'file')
    finally:
        app.state.cadlink_store.close()


@pytest.mark.parametrize('state', ['received', 'processing', 'cancelled'])
def test_legacy_hidden_handoff_is_never_delivered_at_startup(tmp_path, state):
    from server.cadlink.fusion.fusion_delivery import HANDOFFS, recover_staged_fusion_requests

    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        _accept(store, 'hidden-request')
        if state == 'processing':
            assert store.claim('hidden-request', 0) == 1
        elif state == 'cancelled':
            assert store.record_outcome('hidden-request', 0, 'cancelled', reason='publication_failed')
        directory = ipc_folder(tmp_path, create=True) / HANDOFFS.directory
        directory.mkdir()
        hidden = directory / '.hidden-request.json.live-old-claim.tmp'
        hidden.write_text(json.dumps({'schemaVersion': 3, 'requestId': 'hidden-request', 'operationId': 'hidden-request'}))
        from server.cadlink.fusion.fusion_outcomes import settle_unpublished_handoffs

        settle_unpublished_handoffs(store, ipc_folder(tmp_path))
        recovered = recover_staged_fusion_requests(tmp_path, lookup_operation=store.get_operation)
        assert recovered == ()
        assert not (directory / 'hidden-request.json').exists()
        assert not hidden.exists()
        assert store.get_operation('hidden-request')['state'] == ('cancelled' if state == 'received' else state)
    finally:
        store.close()


def test_exact_file_evidence_settles_recovery_after_timeout_and_restart(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    _legacy(store)
    assert settle_abandoned_live_claims(store, now=NOW) == 1
    store.close()
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        assert store.get_operation('old-claim')['state'] == 'recovery_required'
        assert settle_from_heartbeat(store, _heartbeat(), ipc_folder(tmp_path)) == 0
        assert store.get_operation('old-claim')['state'] == 'recovery_required'
        exact = _heartbeat(links=[{'operationId': 'old-claim', 'exportId': 'wge_1'}])
        assert settle_from_heartbeat(store, exact, ipc_folder(tmp_path)) == 1
        assert store.get_operation('old-claim')['state'] == 'accepted'
        assert settle_abandoned_live_claims(store, now=NOW) == 0
    finally:
        store.close()


@pytest.mark.parametrize('case', ['malformed', 'oversized', 'schema', 'stale', 'future'])
def test_file_heartbeat_rejects_invalid_stale_or_future_reports(tmp_path, case):
    from server.cadlink.fusion.fusion_status import FUSION_STATUS_MAX_SKEW, FUSION_STATUS_TTL

    now = datetime.now(timezone.utc)
    _status(tmp_path, updatedAt=now.isoformat())
    path = ipc_folder(tmp_path) / '.fusion-status.json'
    if case == 'malformed':
        path.write_text('{invalid json')
    elif case == 'oversized':
        path.write_text(path.read_text() + ' ' * (256 * 1024))
    elif case == 'schema':
        _status(tmp_path, schemaVersion=2)
    elif case == 'stale':
        _status(tmp_path, updatedAt=(now - FUSION_STATUS_TTL - timedelta(seconds=1)).isoformat())
    else:
        _status(tmp_path, updatedAt=(now + FUSION_STATUS_MAX_SKEW + timedelta(seconds=10)).isoformat())
    assert select_heartbeat(tmp_path, now) == (None, None)
    assert addin_update.loaded_addin_identity(tmp_path) is None


def test_cancelled_legacy_return_with_lost_completion_settles_durably(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    operation_id = _legacy(store, 'request_return')
    assert store.request_cancel(operation_id)['state'] == 'cancel_requested'
    with store._lock, store._transaction() as conn:
        conn.execute('UPDATE cad_operations SET updated_at = ? WHERE operation_id = ?',
                     ((NOW - LEGACY_LIVE_CLAIM_MAX_AGE).isoformat(), operation_id))
    assert settle_abandoned_live_claims(store, now=NOW) == 1
    assert store.get_operation(operation_id)['state'] == 'cancelled'
    assert settle_abandoned_live_claims(store, now=NOW) == 0
    store.close()
    reopened = CadLinkStore.for_data_dir(tmp_path)
    assert reopened.get_operation(operation_id)['state'] == 'cancelled'
    reopened.close()


@pytest.mark.parametrize('endpoint_kind', ['locked', 'directory'])
def test_capabilities_publish_before_stale_endpoint_cleanup(tmp_path, monkeypatch, caplog, endpoint_kind):
    from pathlib import Path
    from server.cadlink.fusion import fusion_delivery

    folder = ipc_folder(tmp_path, create=True)
    endpoint = folder / 'wg-endpoint.json'
    capability_file = folder / 'wg-capabilities.json'
    capability_file.write_text(json.dumps({'schemaVersion': 1, 'fusionRequestDelivery': 3}))
    if endpoint_kind == 'directory':
        endpoint.mkdir()
    else:
        endpoint.write_text('{"liveProtocol":1}')
    unlink = Path.unlink
    attempts = []

    def fail_cleanup(path, *args, **kwargs):
        if path == endpoint:
            # Assert the new contract is already visible at the cleanup attempt.
            attempts.append(json.loads(capability_file.read_text()))
            if endpoint_kind == 'locked':
                raise PermissionError('endpoint is held open')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_cleanup)
    fusion_delivery.advertise_fusion_delivery(tmp_path)
    assert attempts == [capabilities()]
    assert json.loads(capability_file.read_text()) == capabilities()
    assert endpoint.exists()
    assert 'Could not remove' in caplog.text
    assert 'Could not advertise' not in caplog.text


def test_endpoint_cleanup_still_runs_after_capability_write_failure(tmp_path, monkeypatch, caplog):
    from server.cadlink.fusion import fusion_delivery

    endpoint = ipc_folder(tmp_path, create=True) / 'wg-endpoint.json'
    endpoint.write_text('{"liveProtocol":1}')

    def fail_write(*args, **kwargs):
        raise PermissionError('capabilities is held open')

    monkeypatch.setattr(fusion_delivery, '_write_json', fail_write)
    fusion_delivery.advertise_fusion_delivery(tmp_path)
    assert not endpoint.exists()
    assert 'Could not advertise' in caplog.text


@pytest.mark.parametrize('entrypoint', ['startup', 'return-api', 'status', 'reconcile'])
def test_file_return_lost_slot_settlement_is_wired_to_backend_entrypoints(tmp_path, entrypoint):
    from server.cadlink import api
    from server.cadlink.fusion.fusion_return import get_return_request

    app = create_app(data_dir=tmp_path)
    workspace = tmp_path / 'cadlink-folder'  # <data>/workspace is the runs folder
    workspace.mkdir()
    app.state.cad_workspace.select(workspace)
    operation_id = _legacy(app.state.cadlink_store, 'request_return', claim=False)
    ipc_folder(tmp_path, create=True)  # Startup published the machine-local IPC folder.
    request = SimpleNamespace(app=app)
    try:
        if entrypoint == 'startup':
            handler = next(h for h in app.router.on_startup if h.__name__ == 'advertise_fusion_delivery_on_startup')
            asyncio.run(handler())
        elif entrypoint == 'return-api':
            assert asyncio.run(get_return_request(operation_id, request))['state'] == 'rejected'
        elif entrypoint == 'status':
            payload = api.FusionStatusRequest.model_validate({'design': {'formula': 'OSSE', 'L': 120, 'a': 45}})
            asyncio.run(api.fusion_status(payload, request))
        else:
            asyncio.run(post_reconcile_cad_operation(operation_id, request))
        assert app.state.cadlink_store.get_operation(operation_id)['state'] == 'rejected'
        assert app.state.cadlink_store.get_operation(operation_id)['reason'] == 'adapter_failed'
    finally:
        app.state.cadlink_store.close()


def test_returns_retains_constant_off_policy_for_frontend_clients(tmp_path, monkeypatch):
    from server.cadlink.api import list_returns

    monkeypatch.setenv('WG2_CAD_COORDINATION', 'on')
    app = create_app(data_dir=tmp_path)
    try:
        assert asyncio.run(list_returns(SimpleNamespace(app=app)))['coordination'] == 'off'
    finally:
        app.state.cadlink_store.close()


@pytest.mark.parametrize('evidence', ['missing', 'staged', 'visible', 'file_claim', 'store_claim'])
def test_startup_cancels_only_unpublished_unclaimed_handoffs(tmp_path, evidence):
    app = create_app(data_dir=tmp_path)
    store = app.state.cadlink_store
    operation_id = 'crash-before-file'
    _accept(store, operation_id)
    directory = ipc_folder(tmp_path, create=True) / '.fusion-handoffs'
    directory.mkdir(exist_ok=True)
    if evidence != 'missing':
        name = {
            'staged': f'.{operation_id}.json.staged.tmp',
            'visible': f'{operation_id}.json',
            'file_claim': f'.wglink-claim-{operation_id}-claim.json',
            'store_claim': 'unused',
        }[evidence]
        if evidence == 'store_claim':
            store.claim(operation_id, 0)
        else:
            (directory / name).write_text(json.dumps({
                'schemaVersion': 3, 'requestId': operation_id, 'operationId': operation_id,
            }))
    store.make_durable()
    handler = next(h for h in app.router.on_startup if h.__name__ == 'advertise_fusion_delivery_on_startup')
    try:
        asyncio.run(handler())
        row = store.get_operation(operation_id)
        if evidence in {'missing', 'staged'}:
            assert (row['state'], row['reason']) == ('cancelled', 'publication_failed')
            assert not list(directory.iterdir())
        else:
            assert row['state'] == ('processing' if evidence == 'store_claim' else 'received')
    finally:
        store.close()
