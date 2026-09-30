"""Only an explicit choice or delivery transport can record CAD Link use."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from server.cadlink import usage, solve_command
from server.cadlink.operations import PREPARE_AND_SOLVE, RECEIVE_SNAPSHOT
from server.cadlink.store import CadLinkStore


@pytest.mark.parametrize("reason", ["setup-task", "install-action", "addin-delivery"])
def test_record_survives_restart_and_is_per_data_directory(tmp_path, reason):
    usage.record_usage(tmp_path / "data", reason)
    assert usage.read_usage(tmp_path / "data")["reason"] == reason
    assert usage.cadlink_in_use(data_dir=tmp_path / "data", owned_addin=False)
    assert not usage.cadlink_in_use(data_dir=tmp_path / "private", owned_addin=False)
    assert not (tmp_path / "private").exists()


def test_setup_written_record_without_an_installed_addin(tmp_path):
    path = usage.usage_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"schemaVersion":1,"reason":"setup-task","recordedAt":"2026-09-30T10:00:00Z"}')
    assert usage.cadlink_in_use(data_dir=tmp_path, owned_addin=False)


@pytest.mark.parametrize("raw", [
    "{", "null", "[]", '{"schemaVersion":2}',
    '{"schemaVersion":true,"reason":"setup-task","recordedAt":"2026-09-30T10:00:00Z"}',
    '{"schemaVersion":1,"reason":"panel-open","recordedAt":"2026-09-30T10:00:00Z"}',
    '{"schemaVersion":1,"reason":[],"recordedAt":"2026-09-30T10:00:00Z"}',
    '{"schemaVersion":1,"reason":"setup-task"}',
    '{"schemaVersion":1,"reason":"setup-task","recordedAt":"yesterday"}',
    '{"schemaVersion":1,"reason":"setup-task","recordedAt":"2026-09-30T10:00:00"}',
])
def test_invalid_records_grant_nothing(tmp_path, raw):
    path = usage.usage_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(raw)
    assert usage.read_usage(tmp_path) is None
    assert not usage.cadlink_in_use(data_dir=tmp_path, owned_addin=False)
    assert path.read_text() == raw


def test_atomic_write_failure_preserves_previous_record(tmp_path, monkeypatch):
    usage.record_usage(tmp_path, "setup-task")
    path = usage.usage_path(tmp_path)
    before = path.read_bytes()
    def fail(*args):
        raise OSError("replace failed")
    monkeypatch.setattr(usage.os, "replace", fail)
    with pytest.raises(OSError):
        usage.record_usage(tmp_path, "install-action")
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("kind", [PREPARE_AND_SOLVE, RECEIVE_SNAPSHOT])
@pytest.mark.parametrize("transport", ["inbox", "live"])
def test_accepted_delivery_records_usage(tmp_path, kind, transport):
    from server.tests.test_cad_solve_delivery import _file
    data = tmp_path / "data"
    store = CadLinkStore.for_data_dir(data)
    manifest = "sha256:" + "a" * 64
    item = solve_command.DeliveredItem("delivery", kind, "wgreturn/model.wgreturn", manifest, "wgr_1")
    try:
        assert not usage.cadlink_in_use(data_dir=data, owned_addin=False)
        if transport == "live":
            result = solve_command.deliver_live(store, item, retain=lambda _: None)
            assert result.status == solve_command.LIVE_ACCEPTED
        else:
            extras = {} if kind == PREPARE_AND_SOLVE else {"kind": kind}
            path = _file(data, "delivery", item.bundle_path, manifest, schema=4 if kind == RECEIVE_SNAPSHOT else 3, **extras)
            if kind == RECEIVE_SNAPSHOT:
                payload = json.loads(path.read_text())
                payload.pop("returnId")
                path.write_text(json.dumps(payload))
            solve_command.collect_solve_deliveries(data, store)
            assert store.get_operation("delivery") is not None
        assert usage.read_usage(data)["reason"] == "addin-delivery"
        assert usage.cadlink_in_use(data_dir=data, owned_addin=False)
    finally:
        store.close()


@pytest.mark.parametrize("transport", ["inbox", "live"])
def test_a_usage_record_that_cannot_be_written_never_fails_the_delivery(
    tmp_path, monkeypatch, caplog, transport
):
    """The operation is stored before the record; a held file must not undo that."""
    from server.tests.test_cad_solve_delivery import _file
    data = tmp_path / "data"
    store = CadLinkStore.for_data_dir(data)
    manifest = "sha256:" + "a" * 64
    item = solve_command.DeliveredItem(
        "delivery", PREPARE_AND_SOLVE, "wgreturn/model.wgreturn", manifest, "wgr_1"
    )

    def held(*_args, **_kwargs):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(usage, "record_usage", held)
    try:
        with caplog.at_level("WARNING", logger=solve_command.logger.name):
            if transport == "live":
                result = solve_command.deliver_live(store, item, retain=lambda _: None)
                assert result.status == solve_command.LIVE_ACCEPTED
            else:
                _file(data, "delivery", item.bundle_path, manifest)
                solve_command.collect_solve_deliveries(data, store)
        assert store.get_operation("delivery") is not None
        assert "Could not record CAD Link use" in caplog.text
        assert usage.read_usage(data) is None
    finally:
        store.close()


def test_direct_operation_and_conflicting_delivery_grant_nothing(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        first = solve_command.DeliveredItem("delivery", RECEIVE_SNAPSHOT, "wgreturn/first.wgreturn", "sha256:" + "a" * 64)
        target, inputs = first.request()
        store.accept_operation(first.operation_id, first.kind,
                               solve_command.request_digest(first.kind, target, inputs), target, inputs)
        assert not usage.cadlink_in_use(data_dir=tmp_path, owned_addin=False)
        conflict = solve_command.DeliveredItem("delivery", RECEIVE_SNAPSHOT, "wgreturn/other.wgreturn", first.manifest_sha256)
        assert solve_command.deliver_live(store, conflict, retain=lambda _: None).status == solve_command.LIVE_CONFLICT
        assert not usage.cadlink_in_use(data_dir=tmp_path, owned_addin=False)
    finally:
        store.close()


def test_install_endpoint_records_choice_before_deferred_or_failed_activation(tmp_path, monkeypatch):
    from server.cadlink.api import post_install_addin
    from server.cadlink import addin_update
    def activate(data):
        assert usage.read_usage(data)["reason"] == "install-action"
        return "pending", "Close Fusion to finish installing WGLink."
    monkeypatch.setattr(addin_update, "refresh_and_log", activate)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(data_dir=tmp_path)))
    result = asyncio.run(post_install_addin(request))
    assert result["verdict"] == "pending"
    assert usage.cadlink_in_use(data_dir=tmp_path, owned_addin=False)


@pytest.mark.parametrize("schema", [2, 3])
def test_refused_inbox_delivery_grants_nothing(tmp_path, schema):
    from server.tests.test_cad_solve_delivery import _file
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        # Version 2 is refused as outdated; version 3 cannot deliver a snapshot.
        extras = {} if schema == 2 else {"kind": RECEIVE_SNAPSHOT}
        _file(tmp_path, "refused", "wgreturn/model.wgreturn", "sha256:" + "a" * 64,
              schema=schema, **extras)
        solve_command.collect_solve_deliveries(tmp_path, store)
        assert not usage.cadlink_in_use(data_dir=tmp_path, owned_addin=False)
    finally:
        store.close()
