"""The automatic frame suggestion: cached per snapshot, preselected, never binding.

A confirmed frame whose identity matches always wins; a snapshot that clearly
faces another way than the confirmed frame gets a notice and nothing changes.
The suggestion is cached by snapshot and algorithm version, and a survey that
could not run asks without being cached.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
import numpy as np
import pytest

from server.cadlink import frame_infer
from server.cadlink.solver_frame import (
    AXES,
    confirm_frame,
    ensure_frame_suggestion,
    frame_preview,
    record_solver_frame,
)
from server.cadlink.solver_frame_api import router
from server.cadlink.store import CadLinkStore
from server.mesh.artifact import mesh_text_sha256
from server.mesh.gmsh_worker import _run_in_gmsh_session

import cad_frame_fixtures as fx
from test_cad_solver_frame_api import _Client

pytest.importorskip("gmsh")

_PARTY: list[fx.FixtureMesh] = []


def _party() -> fx.FixtureMesh:
    if not _PARTY:
        def party_meh_like() -> list:
            return fx.party_meh_like(0.25)
        _PARTY.append(_run_in_gmsh_session(fx.mesh_fixture, party_meh_like, size_mm=9.0))
    return _PARTY[0]


def _names(mesh: fx.FixtureMesh) -> tuple[dict[int, str], dict[str, dict[str, Any]]]:
    names = {1: "wg-import-v1|rigid"}
    tag_map: dict[str, dict[str, Any]] = {"1": {"source_id": None, "instance_id": None, "role": "rigid"}}
    for source in mesh.sources:
        names[source.tag] = (
            f"wg-import-v1|tag={source.tag}|source_id={source.source_id}|instance_id=null|role={source.role}"
        )
        tag_map[str(source.tag)] = {"source_id": source.source_id, "instance_id": None, "role": source.role}
    return names, tag_map


def _record(
    root: Path,
    facing: str,
    *,
    sha: str = "a",
    lineage: str | None = "wgl_project",
    domain: bool = False,
) -> dict[str, Any]:
    """An as-modelled (+z) record of the PartyMEH look-alike placed facing ``facing``."""

    mesh = _party()
    points = mesh.points_mm @ fx.POSES[facing].T
    names, tag_map = _names(mesh)
    text = fx.msh22_text(points, mesh.triangles, mesh.tags, names)
    path = root / f"{sha}.msh"
    path.write_text(text, encoding="utf-8")
    manifest: dict[str, Any] = {"assembly": {}, "instances": []}
    if domain:
        manifest["assembly"]["domain"] = {"kind": "half", "cut_planes": ["x0"]}
    frame = record_solver_frame(manifest, "+z")
    return {
        "ingest_id": "wgi_" + sha.upper() * 26,
        "manifest_sha256": "sha256:" + sha * 64,
        "mesh_store_path": str(path),
        "mesh_content_sha256": mesh_text_sha256(text),
        "anchor": {"instance_id": None, "design_id": None, "throat_frame": None},
        "normalisation": {"anchor_instance_id": None, "matrix": frame["matrix"], "solver_frame": frame},
        "project": {"lineage_id": lineage} if lineage else None,
        "tag_map": tag_map,
        "findings": [],
        "symmetry": {"domain_planes": [], "cut_planes": []},
    }


def test_the_suggestion_is_computed_once_per_snapshot_and_algorithm(tmp_path: Path, monkeypatch) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    record = _record(tmp_path, "+x")
    first = ensure_frame_suggestion(store, record)
    assert first["status"] == "automatic" and first["axis"] == "+x"
    assert first["algorithm"] == frame_infer.ALGORITHM_VERSION
    assert first["snapshotSha256"] == record["manifest_sha256"]
    # Cached: the mesh is not read again.
    Path(record["mesh_store_path"]).unlink()
    assert ensure_frame_suggestion(store, record) == first
    # A new algorithm version is a new cache entry, computed afresh (and here
    # the mesh is gone, so it asks -- and that is not cached).
    monkeypatch.setattr(frame_infer, "ALGORITHM_VERSION", "frame-infer-test")
    fresh = ensure_frame_suggestion(store, record)
    assert fresh["status"] == "unavailable" and "Pick the front" in fresh["reason"]
    assert store.get_frame_suggestion(record["manifest_sha256"], "frame-infer-test") is None


def test_an_unsurveyable_record_asks_and_preselects_nothing(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    record = _record(tmp_path, "+x")
    Path(record["mesh_store_path"]).write_text("damaged", encoding="utf-8")
    preview = frame_preview(store, record)
    assert preview["suggestion"]["status"] == "unavailable"
    assert preview["preselected"] is None
    assert preview["differs"] is None


def test_an_automatic_suggestion_is_preselected_but_not_confirmed(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    record = _record(tmp_path, "-y")
    preview = frame_preview(store, record)
    assert preview["preselected"] == {"axis": "-y", "source": "suggested"}
    assert preview["confirmed"] is None
    assert store.get_frame_confirmation("lineage:wgl_project") is None
    assert "Radiates along -y" in preview["suggestion"]["reason"]


def test_a_matching_confirmed_frame_wins_and_a_disagreement_is_only_a_notice(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    first = _record(tmp_path, "+z")
    ensure_frame_suggestion(store, first)
    row = confirm_frame(store, first, "+z")
    assert row["frame"]["provenance"] == "suggested"
    assert frame_preview(store, first)["differs"] is None

    # The next export of the same project was turned in CAD: it faces +x now.
    turned = _record(tmp_path, "+x", sha="b")
    preview = frame_preview(store, turned)
    assert preview["suggestion"]["axis"] == "+x"
    assert preview["preselected"] == {"axis": "+z", "source": "confirmed"}
    assert preview["differs"]["confirmedAxis"] == "+z"
    assert preview["differs"]["suggestedAxis"] == "+x"
    assert "+x" in preview["differs"]["message"] and "+z" in preview["differs"]["message"]
    # Never switched: the confirmation is untouched.
    assert store.get_frame_confirmation("lineage:wgl_project")["axis"] == "+z"
    assert store.get_frame_confirmation("lineage:wgl_project")["confirmed_at"] == row["confirmed_at"]


def test_a_choice_against_the_suggestion_is_recorded_as_chosen(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    record = _record(tmp_path, "+x")
    suggestion = ensure_frame_suggestion(store, record)
    row = confirm_frame(store, record, "-x")
    assert row["frame"]["provenance"] == "chosen"
    assert row["frame"]["suggestion"]["axis"] == "+x"
    assert row["frame"]["suggestion"]["algorithm"] == suggestion["algorithm"]


def test_a_declared_half_facing_elsewhere_is_asked_about(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    record = _record(tmp_path, "-y", domain=True)
    preview = frame_preview(store, record)
    suggestion = preview["suggestion"]
    assert suggestion["status"] == "ask"
    assert suggestion["reasonCode"] == "unsupported-axis"
    assert suggestion["unrestrictedAxis"] == "-y"
    assert suggestion["axis"] is None
    assert preview["preselected"] is None


def test_the_route_returns_the_suggestion_and_what_is_preselected(tmp_path: Path, monkeypatch) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    record = _record(tmp_path, "+y")
    monkeypatch.setattr("server.cadlink.solver_frame_api.get_ingestion_record", lambda _store, _id: record)
    app = FastAPI()
    app.include_router(router)
    app.state.cadlink_store = store
    client = _Client(app)
    response = client.get("/api/cadlink/solver-frame", {"ingestId": record["ingest_id"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggestion"]["axis"] == "+y" and body["suggestion"]["status"] == "automatic"
    assert body["suggestion"]["confidence"] >= frame_infer.MIN_LEAD
    assert body["suggestion"]["algorithm"] == frame_infer.ALGORITHM_VERSION
    assert body["preselected"] == {"axis": "+y", "source": "suggested"}
    assert [item["axis"] for item in body["axes"]] == list(AXES)
    put = client.put("/api/cadlink/solver-frame", {"ingestId": record["ingest_id"], "axis": "+y"})
    assert put.status_code == 200, put.text
    assert put.json()["preselected"] == {"axis": "+y", "source": "confirmed"}
    assert put.json()["confirmed"]["frame"]["provenance"] == "suggested"


def test_a_linked_record_gets_no_suggestion(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    linked = _record(tmp_path, "+z")
    linked["anchor"] = {"instance_id": "i", "design_id": "d", "throat_frame": {}}
    assert ensure_frame_suggestion(store, linked) is None


def test_the_import_command_schedules_the_survey(tmp_path: Path) -> None:
    from server.cadlink import api

    store = CadLinkStore(tmp_path / "cadlink.db")
    record = _record(tmp_path, "-x")

    async def run() -> None:
        api._schedule_frame_suggestion(store, record)
        api._schedule_frame_suggestion(store, record)  # one survey per snapshot in flight
        assert len(api._FRAME_SUGGESTIONS) == 1
        await asyncio.gather(*api._FRAME_SUGGESTIONS.values())

    asyncio.run(run())
    cached = store.get_frame_suggestion(record["manifest_sha256"], frame_infer.ALGORITHM_VERSION)
    assert cached is not None and cached["axis"] == "-x"
    assert json.dumps(cached)  # stored as JSON
    assert np.isfinite(cached["confidence"])
