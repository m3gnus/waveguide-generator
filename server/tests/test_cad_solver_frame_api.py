"""The solver frame routes: preview and confirmation, keyed by the snapshot's record."""

from __future__ import annotations

import json
from pathlib import Path

import asyncio
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI
import numpy as np

from server.cadlink.solver_frame import AXES, CONTRACT, frame_matrix
from server.cadlink.solver_frame_api import router
from server.cadlink.store import CadLinkStore
from test_cad_operations_store import SOLVE, _solve_request


MANIFEST = "sha256:" + "a" * 64
ARTIFACT = "sha256:" + "b" * 64


def _record(*, linked: bool = False, axis: str = "+z", allowed: list[str] | None = None) -> dict:
    record = {
        "manifest_sha256": MANIFEST,
        "artifact_sha256": ARTIFACT,
        "anchor": {"instance_id": "i" if linked else None, "design_id": None, "throat_frame": None},
        "normalisation": {"anchor_instance_id": "i" if linked else None},
        "project": {"lineage_id": "wgl_authored"},
    }
    if not linked:
        record["normalisation"]["solver_frame"] = {
            "contract": CONTRACT,
            "axis": axis,
            "requirement": {"contract": CONTRACT, "export_frame": "root-component"},
            "allowed_axes": allowed if allowed is not None else list(AXES),
            "matrix": frame_matrix(axis).tolist(),
        }
    return record


class _Response:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.text = body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.text)


class _Client:
    """A dependency-free ASGI call, as ``test_app_batch_e.TestClient``, with a query."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    def _call(self, method: str, path: str, query: dict[str, str], body: bytes) -> _Response:
        async def run() -> _Response:
            sent: list[dict[str, Any]] = []
            delivered = False

            async def receive() -> dict[str, Any]:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message: dict[str, Any]) -> None:
                sent.append(message)

            await self.app(
                {
                    "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": method, "scheme": "http", "path": path,
                    "raw_path": path.encode("ascii"),
                    "query_string": urlencode(query).encode("ascii"), "root_path": "",
                    "headers": [(b"host", b"127.0.0.1"), (b"content-type", b"application/json")],
                    "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
                },
                receive,
                send,
            )
            start = next(item for item in sent if item["type"] == "http.response.start")
            return _Response(
                start["status"],
                b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body"),
            )

        return asyncio.run(run())

    def get(self, path: str, params: dict[str, str] | None = None) -> _Response:
        return self._call("GET", path, params or {}, b"")

    def put(self, path: str, json: dict[str, Any]) -> _Response:  # noqa: A002
        import json as json_module

        return self._call("PUT", path, {}, json_module.dumps(json).encode("utf-8"))


def _client(tmp_path: Path) -> tuple[_Client, CadLinkStore]:
    app = FastAPI()
    app.include_router(router)
    store = CadLinkStore(tmp_path / "cadlink.db")
    app.state.cadlink_store = store
    return _Client(app), store


_SEQUENCE = iter(range(1, 1000))


def _ingest(store: CadLinkStore, record: dict) -> str:
    # Each record its own snapshot, as each ingestion is.
    record = {**record, "manifest_sha256": "sha256:" + f"{next(_SEQUENCE):064x}"}
    row = store.allocate_ingest(
        manifest_sha256=record["manifest_sha256"],
        artifact_sha256=record["artifact_sha256"],
        record_builder=lambda ingest_id, created_at: json.dumps(
            {**record, "ingest_id": ingest_id, "created_at": created_at}
        ),
    )
    return str(row["ingest_id"])


def test_preview_then_confirm_by_ingest(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    ingest_id = _ingest(store, _record())
    preview = client.get("/api/cadlink/solver-frame", params={"ingestId": ingest_id})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["linked"], body["recordAxis"], body["confirmed"]) == (False, "+z", None)
    assert [item["axis"] for item in body["axes"]] == list(AXES)

    confirmed = client.put("/api/cadlink/solver-frame", json={"ingestId": ingest_id, "axis": "-y"})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed"]["axis"] == "-y"
    # Keyed by the record's project, never by anything the request said.
    stored = store.get_frame_confirmation("lineage:wgl_authored")
    assert stored["axis"] == "-y"
    assert stored["requirement"] == {"contract": CONTRACT, "export_frame": "root-component"}
    item = {entry["axis"]: entry for entry in confirmed.json()["axes"]}["-y"]
    assert np.allclose(item["previewFromRecord"], frame_matrix("-y"))


def test_preview_and_confirm_through_an_operations_preparation(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    ingest_id = _ingest(store, _record(axis="+x"))
    target, inputs, digest = _solve_request()
    store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
    no_preparation = client.get("/api/cadlink/solver-frame", params={"operationId": "cmd-1"})
    assert no_preparation.status_code == 409
    generation = store.claim("cmd-1", 0)
    assert generation is not None
    assert store.record_preparation(
        "cmd-1", generation, preparation_id=ingest_id, snapshot_sha256=ARTIFACT,
        setup_revision_id=None, ingest_id=ingest_id, report_sha256=None,
        blocking_finding_ids=[],
    ) is not None
    preview = client.get("/api/cadlink/solver-frame", params={"operationId": "cmd-1"})
    assert preview.status_code == 200 and preview.json()["recordAxis"] == "+x"
    put = client.put("/api/cadlink/solver-frame", json={"operationId": "cmd-1", "axis": "+x"})
    assert put.status_code == 200 and put.json()["confirmed"]["axis"] == "+x"


def test_refusals(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    linked = _ingest(store, _record(linked=True))
    half = _ingest(store, _record(allowed=["+z"]))
    assert client.get("/api/cadlink/solver-frame", params={"ingestId": linked}).json() == {"linked": True}
    assert client.put("/api/cadlink/solver-frame", json={"ingestId": linked, "axis": "+z"}).status_code == 422
    assert client.put("/api/cadlink/solver-frame", json={"ingestId": half, "axis": "+y"}).status_code == 422
    assert client.put("/api/cadlink/solver-frame", json={"ingestId": half, "axis": "up"}).status_code == 422
    assert client.put(
        "/api/cadlink/solver-frame", json={"ingestId": half, "operationId": "x", "axis": "+z"}
    ).status_code == 422
    assert client.put(
        "/api/cadlink/solver-frame", json={"ingestId": half, "axis": "+z", "key": "lineage:other"}
    ).status_code == 422
    assert client.get("/api/cadlink/solver-frame").status_code == 422
    assert client.get("/api/cadlink/solver-frame", params={"ingestId": "wgi_" + "9" * 26}).status_code == 404
    assert client.get("/api/cadlink/solver-frame", params={"operationId": "missing"}).status_code == 404
    assert store.get_frame_confirmation("lineage:wgl_authored") is None
