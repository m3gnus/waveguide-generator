"""Onshape route contracts and link-state persistence.

The bundle-building half of a send is already covered by
``test_wglink_export.py``; these tests own the Onshape half -- what gets stored,
what a poll costs, and what happens when the linked document disappears.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from fastapi import HTTPException
import pytest

from server.cadlink.identity import SaveIdentity, design_hash
from server.cadlink.onshape import api as onshape_api
from server.cadlink.onshape.adapter import OnshapeAdapterError
from server.cadlink.onshape.client import OnshapeClient
from server.cadlink.onshape.credentials import OnshapeCredentials
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig
from server.design.textcfg import serialize

from test_onshape_adapter import FakeTransport, _send_transport


def _design(coverage: float = 45.0) -> DesignConfig:
    return DesignConfig.model_validate({"formula": "OSSE", "L": 120, "a": coverage})


def _store(tmp_path: Path) -> CadLinkStore:
    store = CadLinkStore(tmp_path / "cadlink.db")
    store.initialize()
    return store


def _saved(store: CadLinkStore, design: DesignConfig):
    return store.save(
        requested=None,
        design_hash=design_hash(design),
        filename="demo-horn.cfg",
        snapshot_builder=lambda identity: serialize(design, cadlink=identity),
        saved_at="2026-08-13T09:00:00Z",
    )


def _request(store: CadLinkStore, tmp_path: Path, transport: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                cadlink_store=store,
                data_dir=str(tmp_path),
                onshape_transport=transport,
            )
        )
    )


def _client(transport: FakeTransport) -> OnshapeClient:
    return OnshapeClient(
        OnshapeCredentials(access_key="A", secret_key="B"), transport=transport
    )


# -- store -----------------------------------------------------------------


def test_link_round_trips_and_updates_in_place(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = _saved(store, _design())["identity"]

    first = store.save_onshape_link(
        design_id=identity.design_id,
        account_id="ACC",
        document_id="DID",
        workspace_id="WID",
        blob_element_id="BLOB",
        part_studio_element_id="PART",
        variable_studio_element_id="VARS",
        document_name="Demo Horn",
        is_public=True,
        last_export_id="wge_1",
        last_sequence=1,
        last_design_hash="hash-1",
        last_geometry_hash="geo-1",
        saved_at="2026-08-13T09:05:00Z",
    )
    assert first["is_public"] == 1

    second = store.save_onshape_link(
        design_id=identity.design_id,
        account_id="ACC",
        document_id="DID",
        workspace_id="WID",
        blob_element_id="BLOB",
        part_studio_element_id="PART",
        variable_studio_element_id="VARS",
        document_name="Demo Horn",
        is_public=True,
        last_export_id="wge_2",
        last_sequence=2,
        last_design_hash="hash-2",
        last_geometry_hash="geo-2",
        saved_at="2026-08-13T09:30:00Z",
    )
    assert second["last_sequence"] == 2
    assert second["created_at"] == first["created_at"], "the link kept its identity"
    assert store.get_onshape_link(identity.design_id, "ACC")["last_design_hash"] == "hash-2"

    rows = store.find_onshape_links_for_lineage(identity.lineage_id)
    assert [row["document_id"] for row in rows] == ["DID"]
    assert store.delete_onshape_link(identity.design_id, "ACC") is True
    assert store.get_onshape_link(identity.design_id) is None


def test_a_forked_design_still_finds_its_lineage_link(tmp_path: Path) -> None:
    """A conflicting save mints a new design_id; the Onshape document is one."""

    store = _store(tmp_path)
    original = _saved(store, _design())["identity"]
    store.save_onshape_link(
        design_id=original.design_id,
        account_id="ACC",
        document_id="DID",
        workspace_id="WID",
        blob_element_id="BLOB",
        part_studio_element_id=None,
        variable_studio_element_id=None,
        document_name="Demo Horn",
        is_public=True,
        last_export_id=None,
        last_sequence=1,
        last_design_hash="hash-1",
        last_geometry_hash=None,
    )
    at_version_one = SaveIdentity(
        designId=original.design_id,
        lineageId=original.lineage_id,
        baseEditVersion=original.edit_version,
    )
    # Move the head to edit version 2 ...
    store.save(
        requested=at_version_one,
        design_hash=design_hash(_design(coverage=50.0)),
        filename="demo-horn.cfg",
        snapshot_builder=lambda identity: "text",
    )
    # ... then save again from the stale base. That conflict is what forks a
    # design into a new design_id inside the same lineage.
    forked = store.save(
        requested=at_version_one,
        design_hash=design_hash(_design(coverage=57.0)),
        filename="demo-horn.cfg",
        snapshot_builder=lambda identity: "text",
    )
    assert forked["forked"] is True
    assert store.get_onshape_link(forked["identity"].design_id, "ACC") is None
    rows = store.find_onshape_links_for_lineage(forked["identity"].lineage_id, "ACC")
    assert [row["document_id"] for row in rows] == ["DID"]


# -- status ----------------------------------------------------------------


def test_status_is_not_linked_before_a_send(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    identity = _saved(store, _design())["identity"]
    monkeypatch.setattr(
        onshape_api,
        "_credentials_state",
        lambda: {"configured": True, "credentialsPath": "/x", "detail": None, "insecureKeyFile": False},
    )
    payload = onshape_api.OnshapeStatusRequest.model_validate(
        {
            "design": _design().model_dump(mode="json"),
            "identity": {
                "designId": identity.design_id,
                "lineageId": identity.lineage_id,
                "baseEditVersion": identity.edit_version,
            },
        }
    )
    result = asyncio.run(onshape_api.status(payload, _request(store, tmp_path)))
    assert result["state"] == "not_linked"
    assert result["link"] is None
    assert result["currentFormula"] == "osse"


def test_status_reports_current_then_stale(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    design = _design()
    identity = _saved(store, design)["identity"]
    monkeypatch.setattr(
        onshape_api,
        "_credentials_state",
        lambda: {"configured": True, "credentialsPath": "/x", "detail": None, "insecureKeyFile": False},
    )
    store.save_onshape_link(
        design_id=identity.design_id,
        account_id="ACC",
        document_id="DID",
        workspace_id="WID",
        blob_element_id="BLOB",
        part_studio_element_id="PART",
        variable_studio_element_id="VARS",
        document_name="Demo Horn",
        is_public=True,
        last_export_id="wge_1",
        last_sequence=1,
        last_design_hash=design_hash(design),
        last_geometry_hash="geo-1",
    )

    def _status(config: DesignConfig) -> dict[str, Any]:
        payload = onshape_api.OnshapeStatusRequest.model_validate(
            {
                "design": config.model_dump(mode="json"),
                "identity": {
                    "designId": identity.design_id,
                    "lineageId": identity.lineage_id,
                    "baseEditVersion": identity.edit_version,
                },
            }
        )
        return asyncio.run(onshape_api.status(payload, _request(store, tmp_path)))

    current = _status(design)
    assert current["state"] == "current"
    assert current["wgChangesAvailable"] is False
    assert current["link"]["documentUrl"] == "https://cad.onshape.com/documents/DID/w/WID"
    assert current["link"]["isPublic"] is True

    changed = _status(_design(coverage=57.0))
    assert changed["state"] == "stale"
    assert changed["wgChangesAvailable"] is True


def test_status_never_calls_onshape(tmp_path: Path, monkeypatch) -> None:
    """The panel polls this. Spending rate limit on a poll would be a defect."""

    store = _store(tmp_path)
    identity = _saved(store, _design())["identity"]
    monkeypatch.setattr(
        onshape_api,
        "_credentials_state",
        lambda: {"configured": True, "credentialsPath": "/x", "detail": None, "insecureKeyFile": False},
    )
    transport = FakeTransport()
    payload = onshape_api.OnshapeStatusRequest.model_validate(
        {
            "design": _design().model_dump(mode="json"),
            "identity": {
                "designId": identity.design_id,
                "lineageId": identity.lineage_id,
                "baseEditVersion": identity.edit_version,
            },
        }
    )
    asyncio.run(onshape_api.status(payload, _request(store, tmp_path, transport)))
    assert transport.calls == []


def test_status_without_credentials_says_so(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(
        onshape_api,
        "_credentials_state",
        lambda: {
            "configured": False,
            "credentialsPath": "/x/onshape.env",
            "detail": "No Onshape API key pair was found.",
            "insecureKeyFile": False,
        },
    )
    payload = onshape_api.OnshapeStatusRequest.model_validate(
        {"design": _design().model_dump(mode="json")}
    )
    result = asyncio.run(onshape_api.status(payload, _request(store, tmp_path)))
    assert result["state"] == "not_configured"
    assert "/x/onshape.env" in result["credentials"]["credentialsPath"]


# -- push ------------------------------------------------------------------


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "demo.wglink"
    bundle.mkdir()
    (bundle / "waveguide.step").write_text("ISO-10303-21;\n", encoding="utf-8")
    (bundle / "wglink.json").write_text(
        json.dumps(
            {"parameters": [{"name": "wg_demo_depth", "value": 190.0, "unit": "mm", "role": "interface"}]}
        ),
        encoding="utf-8",
    )
    return bundle


def test_push_records_the_link_it_created(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = _saved(store, _design())["identity"]
    transport = _send_transport()
    transport.route("GET", "/users/sessioninfo", 200, {"id": "ACC", "name": "Tester"})

    pushed = onshape_api._push(
        store,
        _client(transport),
        bundle_path=str(_bundle(tmp_path)),
        design_id=identity.design_id,
        document_name="Demo Horn",
        step_filename="demo.step",
        export_id="wge_1",
        sequence=1,
        design_hash_value="hash-1",
        geometry_hash_value="geo-1",
        allow_public=False,
    )
    assert pushed["createdDocument"] is True
    assert pushed["documentUrl"] == "https://cad.onshape.com/documents/DID/w/WID"
    assert pushed["variablesPushed"] == 1

    stored = store.get_onshape_link(identity.design_id, "ACC")
    assert stored["document_id"] == "DID"
    assert stored["blob_element_id"] == "BLOB"
    assert stored["variable_studio_element_id"] == "VARS"
    assert stored["last_sequence"] == 1


def test_push_reuses_the_stored_document_on_the_second_send(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = _saved(store, _design())["identity"]
    common = dict(
        bundle_path=str(_bundle(tmp_path)),
        design_id=identity.design_id,
        document_name="Demo Horn",
        step_filename="demo.step",
        allow_public=False,
    )
    first_transport = _send_transport()
    first_transport.route("GET", "/users/sessioninfo", 200, {"id": "ACC", "name": "Tester"})
    onshape_api._push(
        store,
        _client(first_transport),
        export_id="wge_1",
        sequence=1,
        design_hash_value="hash-1",
        geometry_hash_value="geo-1",
        **common,
    )

    second_transport = _send_transport()
    second_transport.route("GET", "/users/sessioninfo", 200, {"id": "ACC", "name": "Tester"})
    second_transport.route(
        "GET", "/translations/TID", 200, {"requestState": "DONE", "resultElementIds": None}
    )
    result = onshape_api._push(
        store,
        _client(second_transport),
        export_id="wge_2",
        sequence=2,
        design_hash_value="hash-2",
        geometry_hash_value="geo-2",
        **common,
    )
    assert result["createdDocument"] is False
    assert not [call for call in second_transport.calls if call["path"] == "/documents"]
    assert store.get_onshape_link(identity.design_id, "ACC")["last_sequence"] == 2


def test_push_unlinks_a_document_that_no_longer_exists(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = _saved(store, _design())["identity"]
    store.save_onshape_link(
        design_id=identity.design_id,
        account_id="ACC",
        document_id="GONE",
        workspace_id="WID",
        blob_element_id="BLOB",
        part_studio_element_id=None,
        variable_studio_element_id=None,
        document_name="Demo Horn",
        is_public=True,
        last_export_id=None,
        last_sequence=1,
        last_design_hash="hash-1",
        last_geometry_hash=None,
    )
    transport = FakeTransport()
    transport.route("GET", "/users/sessioninfo", 200, {"id": "ACC", "name": "Tester"})
    transport.route("GET", "/documents/GONE", 404, {"message": "Not found"})

    with pytest.raises(OnshapeAdapterError, match="unlinked"):
        onshape_api._push(
            store,
            _client(transport),
            bundle_path=str(_bundle(tmp_path)),
            design_id=identity.design_id,
            document_name="Demo Horn",
            step_filename="demo.step",
            export_id="wge_2",
            sequence=2,
            design_hash_value="hash-2",
            geometry_hash_value=None,
            allow_public=False,
        )
    assert store.get_onshape_link(identity.design_id, "ACC") is None


def test_consent_is_required_before_a_public_document(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = _saved(store, _design())["identity"]
    transport = FakeTransport()
    transport.route("GET", "/users/sessioninfo", 200, {"id": "ACC", "name": "Tester"})
    transport.route("GET", "/users/current", 200, {"activePlan": {"group": "Free"}})

    with pytest.raises(Exception) as caught:
        onshape_api._push(
            store,
            _client(transport),
            bundle_path=str(_bundle(tmp_path)),
            design_id=identity.design_id,
            document_name="Demo Horn",
            step_filename="demo.step",
            export_id="wge_1",
            sequence=1,
            design_hash_value="hash-1",
            geometry_hash_value=None,
            allow_public=False,
        )
    # 428 Precondition Required: the client must confirm and retry.
    assert onshape_api._onshape_error(caught.value).status_code == 428
    assert store.get_onshape_link(identity.design_id) is None


# -- unlink ----------------------------------------------------------------


def test_unlink_forgets_the_link(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = _saved(store, _design())["identity"]
    store.save_onshape_link(
        design_id=identity.design_id,
        account_id="ACC",
        document_id="DID",
        workspace_id="WID",
        blob_element_id="BLOB",
        part_studio_element_id=None,
        variable_studio_element_id=None,
        document_name="Demo Horn",
        is_public=True,
        last_export_id=None,
        last_sequence=1,
        last_design_hash="hash-1",
        last_geometry_hash=None,
    )
    payload = onshape_api.OnshapeUnlinkRequest.model_validate({"designId": identity.design_id})
    assert asyncio.run(onshape_api.unlink(payload, _request(store, tmp_path))) == {"unlinked": True}
    assert asyncio.run(onshape_api.unlink(payload, _request(store, tmp_path))) == {"unlinked": False}


# -- connection ------------------------------------------------------------


def test_connection_reports_the_account_and_caches_it(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(
        onshape_api,
        "_credentials_state",
        lambda: {"configured": True, "credentialsPath": "/x", "detail": None, "insecureKeyFile": False},
    )
    monkeypatch.setattr(
        onshape_api, "load_credentials", lambda: OnshapeCredentials(access_key="A", secret_key="B")
    )
    transport = FakeTransport()
    transport.route("GET", "/users/sessioninfo", 200, {"id": "ACC", "name": "Tester"})
    transport.route(
        "GET", "/users/current", 200, {"activePlan": {"group": "Free", "description": "public only"}}
    )
    request = _request(store, tmp_path, transport)

    result = asyncio.run(onshape_api.connection(request))
    assert result["reachable"] is True
    assert result["account"] == {"id": "ACC", "name": "Tester"}
    assert result["plan"]["publicOnly"] is True
    calls = len(transport.calls)

    again = asyncio.run(onshape_api.connection(request))
    assert again["account"] == {"id": "ACC", "name": "Tester"}
    assert len(transport.calls) == calls, "a cached check spends no rate limit"

    asyncio.run(onshape_api.connection(request, refresh=True))
    assert len(transport.calls) > calls


def test_connection_without_credentials_does_not_call_onshape(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(
        onshape_api,
        "_credentials_state",
        lambda: {
            "configured": False,
            "credentialsPath": "/x/onshape.env",
            "detail": "No Onshape API key pair was found.",
            "insecureKeyFile": False,
        },
    )
    transport = FakeTransport()
    result = asyncio.run(onshape_api.connection(_request(store, tmp_path, transport)))
    assert result["reachable"] is False
    assert result["configured"] is False
    assert transport.calls == []


def test_connection_reports_an_unreachable_account_without_failing(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(
        onshape_api,
        "_credentials_state",
        lambda: {"configured": True, "credentialsPath": "/x", "detail": None, "insecureKeyFile": False},
    )
    monkeypatch.setattr(
        onshape_api, "load_credentials", lambda: OnshapeCredentials(access_key="A", secret_key="B")
    )
    transport = FakeTransport()
    transport.route("GET", "/users/sessioninfo", 401, {"message": "Not authorized"})
    result = asyncio.run(onshape_api.connection(_request(store, tmp_path, transport)))
    assert result["reachable"] is False
    assert "dev-portal" in result["detail"]
