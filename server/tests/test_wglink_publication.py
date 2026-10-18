"""Crash boundaries and monotonic locators use immutable, checksummed artifacts."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException

from server.cadlink.store import CadLinkStore
from server.exports import api, cad_handoff, wglink_publication as publication
from test_wglink_export import _design, _saved, _request, _install_fake_write

pytestmark = pytest.mark.usefixtures("in_process_mesher")


def setup(monkeypatch, tmp_path):
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, _design())
    writes = _install_fake_write(monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return store, saved, workspace, writes


def locator(result):
    return Path(result["bundlePath"]).parent / f"{result['identity']['designId']}.latest.json"


def test_bundle_without_row_recovers_original_reservation_after_b(monkeypatch, tmp_path):
    store, saved, workspace, writes = setup(monkeypatch, tmp_path)
    real_publish = api.publish_bundle

    def crash(*args, **kwargs):
        real_publish(*args, **kwargs)
        raise OSError("crash after bundle")

    monkeypatch.setattr(api, "publish_bundle", crash)
    with pytest.raises(HTTPException):
        api._export_wglink_sync(_request(saved), store, workspace, "A")
    reservation = store.publication_reservation_for_key("A")
    assert store.get_export(reservation["export_id"]) is None
    monkeypatch.setattr(api, "publish_bundle", real_publish)
    b = api._export_wglink_sync(_request(saved), store, workspace, "B")
    assert b["sequence"] == 2
    # A new store simulates process startup; no geometry is rebuilt.
    recovered = CadLinkStore(store.db_path)
    assert publication.recover_publications(recovered) == []
    a = api._export_wglink_sync(_request(saved), recovered, workspace, "A")
    assert a["sequence"] == 1
    assert a["exportId"] == reservation["export_id"]
    assert len(writes) == 2
    assert json.loads(locator(b).read_text())["exportId"] == b["exportId"]
    assert Path(a["bundlePath"]).is_dir()


def test_row_without_locator_recovers_on_retry(monkeypatch, tmp_path):
    store, saved, workspace, writes = setup(monkeypatch, tmp_path)
    real_atomic = publication.atomic_json

    def fail(path, payload):
        if path.name.endswith(".latest.json"):
            raise OSError("locator unavailable")
        return real_atomic(path, payload)

    monkeypatch.setattr(publication, "atomic_json", fail)
    with pytest.raises(HTTPException) as error:
        api._export_wglink_sync(_request(saved), store, workspace, "A")
    assert error.value.status_code == 503
    row = store.find_export_by_idempotency_key("A")
    assert row and Path(row["bundle_path"]).is_dir()
    assert not list((workspace / "wglink").glob("*.latest.json"))
    monkeypatch.setattr(publication, "atomic_json", real_atomic)
    a = api._export_wglink_sync(_request(saved), store, workspace, "A")
    assert json.loads(locator(a).read_text())["exportId"] == a["exportId"]
    assert len(writes) == 1


def test_restart_defers_handoff_until_live_same_key_retry(monkeypatch, tmp_path):
    store, saved, workspace, writes = setup(monkeypatch, tmp_path)
    real_handoff = cad_handoff.publish_fusion_handoff

    def crash(*args, **kwargs):
        raise OSError("crash before handoff")

    monkeypatch.setattr(cad_handoff, "publish_fusion_handoff", crash)
    with pytest.raises(HTTPException) as error:
        api._export_wglink_sync(
            _request(saved),
            store,
            workspace,
            "A",
            handoff={
                "dataDir": str(tmp_path / "data"),
                "documentId": "doc",
                "instanceId": "instance",
                "returnStateHash": "sha256:baseline",
            },
        )
    assert error.value.status_code == 503
    row = store.find_export_by_idempotency_key("A")
    assert list((workspace / "wglink").glob("*.latest.json"))
    monkeypatch.setattr(cad_handoff, "publish_fusion_handoff", real_handoff)
    recovered = CadLinkStore(store.db_path)
    assert publication.recover_publications(recovered) == []
    folder = tmp_path / "data" / "ipc" / "wglink" / ".fusion-handoffs"
    assert not list(folder.glob("*.json"))
    journal = publication._journal_folder(recovered) / f"{row['export_id']}.json"
    assert json.loads(journal.read_text())["handoffState"] == "not_delivered"
    retry = api._export_wglink_sync(_request(saved), recovered, workspace, "A")
    assert retry["cadHandoff"] == "published"
    handoffs = list(folder.glob("*.json"))
    assert len(handoffs) == 1
    payload = json.loads(handoffs[0].read_text())
    assert payload["bundlePath"] == row["bundle_path"]
    assert payload["bundleLayout"] == "immutable-v1"
    assert payload["expectedInstanceId"] == "instance"
    handoffs[0].unlink()  # Add-in consumed it; recovery must not replay.
    assert publication.recover_publications(recovered) == []
    assert not list(folder.glob("*.json"))
    assert len(writes) == 1


def test_locator_contention_never_regresses_and_is_exact(monkeypatch, tmp_path):
    store, saved, workspace, _ = setup(monkeypatch, tmp_path)
    a = api._export_wglink_sync(_request(saved), store, workspace, "A")
    b = api._export_wglink_sync(_request(saved), store, workspace, "B")
    rows = [store.get_export(a["exportId"]), store.get_export(b["exportId"])] * 10
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda row: publication.publish_locator(store, row), rows))
    value = json.loads(locator(b).read_text())
    assert value == {
        "schemaVersion": 1,
        "designId": b["identity"]["designId"],
        "exportId": b["exportId"],
        "bundleId": b["bundleId"],
        "sequence": 2,
        "bundlePath": f"{b['exportId']}.wglink",
    }
    assert not list(locator(b).parent.glob("*.tmp"))


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_windows_replace_failure_keeps_old_locator(monkeypatch, tmp_path, winerror):
    path = tmp_path / "design.latest.json"
    publication.atomic_json(path, {"sequence": 1})
    old = path.read_bytes()
    calls = []

    def denied(src, dst):
        calls.append((src, dst))
        error = OSError("sharing violation")
        error.winerror = winerror
        raise error

    monkeypatch.setattr(publication, "_WINDOWS", True)
    monkeypatch.setattr(publication.os, "replace", denied)
    monkeypatch.setattr(publication.time, "sleep", lambda _: None)
    with pytest.raises(OSError):
        publication.atomic_json(path, {"sequence": 2})
    assert len(calls) == 10
    assert path.read_bytes() == old
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("damage", ["missing", "content", "identity"])
def test_original_missing_or_corrupt_is_409_without_rebuild(monkeypatch, tmp_path, damage):
    store, saved, workspace, writes = setup(monkeypatch, tmp_path)
    a = api._export_wglink_sync(_request(saved), store, workspace, "A")
    path = Path(a["bundlePath"])
    if damage == "missing":
        (path / "waveguide.step").unlink()
    elif damage == "content":
        (path / "waveguide.step").write_text("changed")
    else:
        manifest = json.loads((path / "wglink.json").read_text())
        manifest["export"]["id"] = "other"
        (path / "wglink.json").write_text(json.dumps(manifest))
    with pytest.raises(HTTPException) as error:
        api._export_wglink_sync(_request(saved), store, workspace, "A")
    assert error.value.status_code == 409
    assert len(writes) == 1


def test_retention_keeps_registered_legacy_and_unknown_orphans(monkeypatch, tmp_path):
    store, saved, workspace, _ = setup(monkeypatch, tmp_path)
    a = api._export_wglink_sync(_request(saved), store, workspace, "A")
    b = api._export_wglink_sync(_request(saved), store, workspace, "B")
    root = workspace / "wglink"
    for name in ("legacy.wglink", "unregistered.wglink", ".wg2-wglink-unknown"):
        (root / name).mkdir()
        (root / name / "unknown").write_text("may be referenced")
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert publication.recover_publications(CadLinkStore(store.db_path)) == []
    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert store.get_export(a["exportId"]) and store.get_export(b["exportId"])


def test_idempotency_identity_conflict_is_409(monkeypatch, tmp_path):
    store, saved, workspace, writes = setup(monkeypatch, tmp_path)
    api._export_wglink_sync(_request(saved), store, workspace, "A")
    changed = _request(saved)
    changed.design = _design().model_copy(deep=True)
    changed.base_name = "another-name"
    changed.identity = _request(_saved(store, _design())).identity
    with pytest.raises(HTTPException) as error:
        api._export_wglink_sync(changed, store, workspace, "A")
    assert error.value.status_code == 409
    assert len(writes) == 1


def test_publication_order_is_durable_bundle_row_locator_handoff(monkeypatch, tmp_path):
    store, saved, workspace, _ = setup(monkeypatch, tmp_path)
    events = []
    real_durable = store.make_durable
    real_json = publication.atomic_json
    real_handoff = cad_handoff.publish_fusion_handoff

    def durable():
        events.append("durable")
        real_durable()

    def atomic(path, payload):
        if path.name.endswith(".latest.json"):
            row = store.get_export(payload["exportId"])
            assert row is not None
            assert Path(row["bundle_path"]).is_dir()
            assert events[-1] == "durable"
            events.append("locator")
        real_json(path, payload)

    def handoff(*args, **kwargs):
        assert "locator" in events
        events.append("handoff")
        return real_handoff(*args, **kwargs)

    monkeypatch.setattr(store, "make_durable", durable)
    monkeypatch.setattr(publication, "atomic_json", atomic)
    monkeypatch.setattr(cad_handoff, "publish_fusion_handoff", handoff)
    api._export_wglink_sync(
        _request(saved), store, workspace, "A", handoff={"dataDir": str(tmp_path / "data")}
    )
    assert events.index("locator") < events.index("handoff")


def test_geometry_is_submitted_to_the_killable_child(monkeypatch, tmp_path):
    store, saved, workspace, _ = setup(monkeypatch, tmp_path)
    real_run = api.run_mesh_build
    submissions = []

    async def run(fn, *args):
        from server.exports.wglink_geometry import build_bundle

        assert fn is build_bundle
        assert all(not isinstance(arg, CadLinkStore) for arg in args)
        assert ".wg2-wglink-" in args[1]
        submissions.append(fn)
        return await real_run(fn, *args)

    monkeypatch.setattr(api, "run_mesh_build", run)
    api._export_wglink_sync(_request(saved), store, workspace, "A")
    assert len(submissions) == 1


def test_mount_registers_startup_recovery(monkeypatch, tmp_path):
    import asyncio
    from fastapi import FastAPI

    store = CadLinkStore(tmp_path / "cadlink.db")
    app = FastAPI()
    app.state.cadlink_store = store
    calls = []
    monkeypatch.setattr(api, "recover_publications", lambda registry: calls.append(registry))
    api.mount_exports(app)
    for handler in app.router.on_startup:
        asyncio.run(handler())
    assert calls == [store]


def test_checkpoint_failure_keeps_locator_retryable(monkeypatch, tmp_path):
    import sqlite3

    store, saved, workspace, _ = setup(monkeypatch, tmp_path)
    a = api._export_wglink_sync(_request(saved), store, workspace, "A")
    before = locator(a).read_bytes()
    real = store.make_durable

    def busy():
        raise sqlite3.OperationalError("checkpoint busy")

    monkeypatch.setattr(store, "make_durable", busy)
    with pytest.raises(HTTPException) as error:
        api._export_wglink_sync(_request(saved), store, workspace, "B")
    assert error.value.status_code == 503
    assert locator(a).read_bytes() == before
    monkeypatch.setattr(store, "make_durable", real)
    b = api._export_wglink_sync(_request(saved), store, workspace, "B")
    assert b["sequence"] == 2


@pytest.mark.parametrize("value", [[], {}, {"designId": "wrong"}])
def test_malformed_original_manifest_is_409(monkeypatch, tmp_path, value):
    store, saved, workspace, _ = setup(monkeypatch, tmp_path)
    a = api._export_wglink_sync(_request(saved), store, workspace, "A")
    (Path(a["bundlePath"]) / "wglink.json").write_text(json.dumps(value))
    with pytest.raises(HTTPException) as error:
        api._export_wglink_sync(_request(saved), store, workspace, "A")
    assert error.value.status_code == 409


def test_crash_after_handoff_does_not_republish_consumed_operation(monkeypatch, tmp_path):
    store, saved, workspace, writes = setup(monkeypatch, tmp_path)
    real_json = publication.atomic_json

    def fail_done(path, payload):
        if payload.get("done"):
            raise OSError("crash after handoff")
        return real_json(path, payload)

    monkeypatch.setattr(publication, "atomic_json", fail_done)
    with pytest.raises(HTTPException) as error:
        api._export_wglink_sync(
            _request(saved), store, workspace, "A", handoff={"dataDir": str(tmp_path / "data")}
        )
    assert error.value.status_code == 503
    folder = tmp_path / "data" / "ipc" / "wglink" / ".fusion-handoffs"
    requests = list(folder.glob("*.json"))
    assert len(requests) == 1
    request_id = requests[0].stem
    requests[0].unlink()
    monkeypatch.setattr(publication, "atomic_json", real_json)
    assert publication.recover_publications(store) == []
    assert not list(folder.glob("*.json"))
    assert store.get_operation(request_id) is not None
    assert len(writes) == 1


@pytest.mark.parametrize("update", [False, True])
def test_failed_handoff_restart_and_retry_after_newer_export_are_superseded(monkeypatch, tmp_path, update):
    store, saved, workspace, writes = setup(monkeypatch, tmp_path)
    handoff = {"dataDir": str(tmp_path / "data")}
    if update:
        handoff.update(documentId="doc", instanceId="instance", returnStateHash="sha256:baseline")
    real_handoff = cad_handoff.publish_fusion_handoff
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise OSError("handoff unavailable")

    monkeypatch.setattr(cad_handoff, "publish_fusion_handoff", fail)
    with pytest.raises(HTTPException) as error:
        api._export_wglink_sync(_request(saved), store, workspace, "A", handoff=handoff)
    assert error.value.status_code == 503
    a = store.find_export_by_idempotency_key("A")
    recovered = CadLinkStore(store.db_path)
    assert publication.recover_publications(recovered) == []
    assert len(calls) == 1  # Startup must not even attempt a handoff.
    monkeypatch.setattr(cad_handoff, "publish_fusion_handoff", real_handoff)
    b = api._export_wglink_sync(_request(saved), recovered, workspace, "B", handoff=handoff)
    folder = tmp_path / "data" / "ipc" / "wglink" / ".fusion-handoffs"
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    retry = api._export_wglink_sync(_request(saved), recovered, workspace, "A", handoff=handoff)
    assert retry["exportId"] == a["export_id"]
    assert retry["cadHandoff"] == "failed"
    assert retry["cadHandoffReason"] == "superseded"
    assert "cadHandoffRequestId" not in retry
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    assert recovered.get_operation(b["cadHandoffRequestId"])["state"] == "received"
    assert json.loads(locator(b).read_text())["exportId"] == b["exportId"]
    assert len(writes) == 2


@pytest.mark.parametrize("journal", ["missing", "onshape"])
def test_journalless_or_onshape_retry_reports_no_handoff(monkeypatch, tmp_path, journal):
    import asyncio
    from types import SimpleNamespace

    store, saved, workspace, writes = setup(monkeypatch, tmp_path)
    original = api._export_wglink_sync(_request(saved), store, workspace, "A")
    if journal == "missing":
        (publication._journal_folder(store) / f"{original['exportId']}.json").unlink()
    app = SimpleNamespace(state=SimpleNamespace(
        cadlink_store=store, data_dir=tmp_path / "data",
        cad_workspace=SimpleNamespace(selected_path=lambda: workspace),
    ))
    monkeypatch.setattr(api, "focus_cad", lambda: False)
    monkeypatch.setattr(api, "_handoff_waiting_notice", lambda _: None)
    result = asyncio.run(api.export_wglink(_request(saved), SimpleNamespace(app=app), "A"))
    assert result["exportId"] == original["exportId"]
    assert result["cadHandoff"] == "failed"
    assert result["cadHandoffReason"] == "not_published"
    assert "cadHandoffRequestId" not in result
    assert len(writes) == 1


def test_windows_access_denied_is_retried_then_replaced(monkeypatch, tmp_path):
    path = tmp_path / "design.latest.json"
    publication.atomic_json(path, {"sequence": 1})
    replace = publication.os.replace
    calls = []

    def busy_once(src, dst):
        calls.append(dst)
        if len(calls) == 1:
            error = PermissionError("access denied")
            error.winerror = 5
            raise error
        replace(src, dst)

    monkeypatch.setattr(publication, "_WINDOWS", True)
    monkeypatch.setattr(publication.os, "replace", busy_once)
    monkeypatch.setattr(publication.time, "sleep", lambda _: None)
    publication.atomic_json(path, {"sequence": 2})
    assert len(calls) == 2
    assert json.loads(path.read_text()) == {"sequence": 2}


def test_a_crash_between_accept_and_file_is_reissued_only_by_a_live_retry(monkeypatch, tmp_path):
    """Startup never delivers a handoff and leaves the accepted row as it was:
    cancelling it there could cancel one the add-in already ran while WG was
    down. Only a live same-key retry re-issues it."""
    from server.cadlink.fusion import fusion_delivery
    from server.cadlink.ipc import ipc_folder

    class Crash(BaseException):
        pass

    store, saved, workspace, writes = setup(monkeypatch, tmp_path)
    data = tmp_path / "data"
    replace = fusion_delivery.os.replace

    def crash(src, dst):
        if Path(dst).parent.name == ".fusion-handoffs":
            raise Crash()
        replace(src, dst)

    monkeypatch.setattr(fusion_delivery.os, "replace", crash)
    with pytest.raises(Crash):
        api._export_wglink_sync(_request(saved), store, workspace, "A", handoff={"dataDir": str(data)})
    row = store.find_export_by_idempotency_key("A")
    journal = publication._journal_folder(store) / f"{row['export_id']}.json"
    old_id = json.loads(journal.read_text())["requestId"]
    assert store.get_operation(old_id)["state"] == "received"
    monkeypatch.setattr(fusion_delivery.os, "replace", replace)
    recovered = CadLinkStore(store.db_path)
    assert publication.recover_publications(recovered) == []
    handoffs = ipc_folder(data) / ".fusion-handoffs"
    assert not (handoffs / f"{old_id}.json").exists()
    assert recovered.get_operation(old_id)["state"] == "received"
    # Recovery did not rebuild the bundle either.
    assert len(writes) == 1
    # A live same-key retry re-issues the never-published handoff with a fresh
    # id, as every retry did before immutable bundles, and never a dead one.
    retry = api._export_wglink_sync(_request(saved), recovered, workspace, "A")
    assert retry["exportId"] == row["export_id"]
    assert retry["cadHandoff"] == "published"
    new_id = retry["cadHandoffRequestId"]
    assert new_id != old_id
    assert (handoffs / f"{new_id}.json").exists()
    assert recovered.get_operation(old_id)["reason"] == "publication_failed"
    again = api._export_wglink_sync(_request(saved), recovered, workspace, "A")
    assert again["cadHandoffRequestId"] == new_id
    assert len(writes) == 1
