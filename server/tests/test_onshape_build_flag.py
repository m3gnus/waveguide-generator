"""The Onshape adapter is parked behind one build flag.

Off in a packaged build (the ``APP-MANIFEST.json`` marker), on in a source
checkout, overridable with ``WG_ENABLE_ONSHAPE``. When off the routes are absent
and ``/api/capabilities`` says so; stored Onshape links are simply not offered.
"""

from __future__ import annotations

from pathlib import Path

import json

import pytest

from server.app import create_app
from server.cadlink.build_flags import ONSHAPE_ENV, is_packaged_build, onshape_enabled

from test_app_batch_e import TestClient


def _packaged_root(tmp_path: Path) -> Path:
    root = tmp_path / "layer"
    root.mkdir()
    (root / "APP-MANIFEST.json").write_text("{}", encoding="utf-8")
    return root


def test_default_is_on_for_a_source_tree_and_off_for_a_packaged_layer(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    assert not is_packaged_build(source)
    assert onshape_enabled(environ={}, root=source) is True
    packaged = _packaged_root(tmp_path)
    assert is_packaged_build(packaged)
    assert onshape_enabled(environ={}, root=packaged) is False


@pytest.mark.parametrize(("value", "expected"), [("1", True), ("on", True), ("0", False), ("off", False)])
def test_override_wins_either_way(tmp_path: Path, value: str, expected: bool) -> None:
    for root in (tmp_path, _packaged_root(tmp_path)):
        assert onshape_enabled(environ={ONSHAPE_ENV: value}, root=root) is expected


def test_unrecognised_override_falls_back_to_the_default(tmp_path: Path) -> None:
    assert onshape_enabled(environ={ONSHAPE_ENV: "maybe"}, root=tmp_path) is True
    assert onshape_enabled(environ={ONSHAPE_ENV: "maybe"}, root=_packaged_root(tmp_path)) is False


def _paths(client: TestClient) -> set[str]:
    return set(client.app.openapi()["paths"])


def test_flag_on_mounts_routes_and_advertises_them(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ONSHAPE_ENV, "1")
    client = TestClient(create_app(data_dir=tmp_path))
    assert any(path.startswith("/api/cadlink/onshape/") for path in _paths(client))
    assert client.get("/api/capabilities").json()["onshape"] is True


def test_flag_off_removes_routes_and_advertises_false(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ONSHAPE_ENV, "0")
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
    monkeypatch.setenv(ONSHAPE_ENV, "0")
    off = _post(TestClient(create_app(data_dir=tmp_path / "off")), body)
    assert off.status_code == 404
    assert "not available in this build" in off.text
    monkeypatch.setenv(ONSHAPE_ENV, "1")
    on = _post(TestClient(create_app(data_dir=tmp_path / "on")), body)
    assert "not available in this build" not in on.text
