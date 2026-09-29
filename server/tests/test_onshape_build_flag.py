"""The Onshape adapter is parked behind one build flag.

Off everywhere unless ``WG2_ENABLE_ONSHAPE=1``. When off the routes are absent
and ``/api/capabilities`` says so; stored Onshape links are simply not offered.
"""

from __future__ import annotations

from pathlib import Path

import json


from server.app import create_app
from server.cadlink.build_flags import ONSHAPE_ENV, onshape_enabled

from test_app_batch_e import TestClient


def test_default_is_off_and_only_one_enables(tmp_path: Path) -> None:
    assert onshape_enabled(environ={}) is False
    assert onshape_enabled(environ={ONSHAPE_ENV: ""}) is False
    for value in ("0", "true", "on", "yes", "maybe"):
        assert onshape_enabled(environ={ONSHAPE_ENV: value}) is False
    assert onshape_enabled(environ={ONSHAPE_ENV: "1"}) is True
    assert ONSHAPE_ENV == "WG2_ENABLE_ONSHAPE"


def _paths(client: TestClient) -> set[str]:
    return set(client.app.openapi()["paths"])


def test_flag_on_mounts_routes_and_advertises_them(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ONSHAPE_ENV, "1")
    client = TestClient(create_app(data_dir=tmp_path))
    assert any(path.startswith("/api/cadlink/onshape/") for path in _paths(client))
    assert client.get("/api/capabilities").json()["onshape"] is True


def test_flag_off_removes_routes_and_advertises_false(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(ONSHAPE_ENV, raising=False)
    client = TestClient(create_app(data_dir=tmp_path))
    assert not any("onshape" in path for path in _paths(client))
    assert client.get("/api/cadlink/onshape/status").status_code == 404
    assert client.get("/api/capabilities").json()["onshape"] is False
    # The Fusion side of the CAD link is untouched.
    assert any(path.startswith("/api/cadlink/") for path in _paths(client))


def _post(client: TestClient, body: dict[str, object]):
    return client.post(
        "/api/cadlink/ingest",
        json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )


def test_flag_off_refuses_an_onshape_origin_ingest(tmp_path: Path, monkeypatch) -> None:
    body = {
        "bundlePath": "x.wgreturn",
        "bundleOrigin": "onshape",
        "mesh": {"rigidSizeMm": 10, "transitionMm": 20, "sourceSizeMm": {"a": 5}},
    }
    monkeypatch.delenv(ONSHAPE_ENV, raising=False)
    off = _post(TestClient(create_app(data_dir=tmp_path / "off")), body)
    assert off.status_code == 404
    assert "not available in this build" in off.text
    monkeypatch.setenv(ONSHAPE_ENV, "1")
    on = _post(TestClient(create_app(data_dir=tmp_path / "on")), body)
    assert "not available in this build" not in on.text
