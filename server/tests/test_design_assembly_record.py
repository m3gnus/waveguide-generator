"""A design assembly compiled into an immutable prepared mesh: the third producer.

The real design mesher builds every geometry here (in process, as tests run
it); nothing is mocked between the design and the sealed record.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import numpy as np
import pytest

from server.cadlink.intake import design_assembly
from server.cadlink.intake.design_assembly import (
    AssemblyBuildFence,
    AssemblySuperseded,
    compile_design_assembly,
)
from server.cadlink.operations import TERMINAL_STATES
from server.cadlink.store import CadLinkStore, design_model_id
from server.contracts.canonical import canonical_ascii_strict_bytes, canonical_normalized_bytes
from server.design.schema import DesignConfig
from server.jobs.store import JobStore
from server.mesh.builder import AssemblyRefusal, _build_sync
from server.mesh.file_import import mesh_text, parse_file_mesh, prepare_file_mesh
from server.mesh.prepared_gc import collect_prepared_meshes
from server.mesh.prepared_record import check_prepared_record, seal_prepared_record, solve_model_sha256

UID = "wgu_01JABCDEFGHJKMNPQRSTVWXYZ0"
W1 = "00000000-0000-4000-8000-0000000000aa"
W2 = "00000000-0000-4000-8000-0000000000ab"
G_HF = "00000000-0000-4000-8000-0000000000b1"
G_LF = "00000000-0000-4000-8000-0000000000b2"
G_2 = "00000000-0000-4000-8000-0000000000b3"

HORN = {
    "formula": "R-OSSE", "R": 100, "r0": 12.7, "a0": 15.5, "a": 25, "k": 2, "m": 0.85,
    "b": 0.2, "r": 0.4, "q": 3.4, "tmax": 1,
    "mesh": {"quadrants": 1234, "throat_resolution": 8, "mouth_resolution": 20,
             "rear_resolution": 40, "angular_segments": 32, "length_segments": 12},
    "enclosure": {"depth": 200, "space_l": 30, "space_t": 30, "space_r": 30, "space_b": 250,
                  "front_resolution": 30, "back_resolution": 50, "edge_radius": 10},
}


def woofer(identity=W1, *, shape="piston", enabled=True, anchor="below", gap=10, **extra):
    return {"id": identity, "label": f"Woofer {identity[-2:]}", "radius_mm": 80,
            "shape": {"kind": shape}, "placement": {"anchor": anchor, "gap_mm": gap},
            "resolution_mm": 25, "enabled": enabled, **extra}


def assembly(*woofers, groups=None):
    ids = [item["id"] for item in woofers]
    return {
        "woofers": list(woofers),
        "signal_groups": groups or [
            {"id": G_HF, "label": "HF", "members": [{"radiator_id": "horn-throat"}]},
            *([{"id": G_LF, "label": "LF", "members": [{"radiator_id": i} for i in ids]}] if ids else []),
        ],
    }


def design(**changes):
    return {**copy.deepcopy(HORN), **changes}


@pytest.fixture
def store(tmp_path):
    cad = CadLinkStore.for_data_dir(tmp_path)
    cad.initialize()
    yield cad
    cad.close()


def compile_(store, tmp_path, dump, *, revision=1, fence=None, name="Front horn"):
    return asyncio.run(compile_design_assembly(
        store, tmp_path, dump, design_uid=UID, design_name=name, design_revision=revision,
        fence=fence or AssemblyBuildFence(),
    ))


# -- the one finalization, byte for byte for every producer ---------------------------


def _record(producer):
    record = {
        "ingest_id": "wgi_01JABCDEFGHJKMNPQRSTVWXYZ0", "created_at": "2026-10-29T00:00:00Z",
        "producer": producer, "manifest_sha256": "sha256:" + "a" * 64, "artifact_sha256": "sha256:" + "b" * 64,
        "mesh_store_path": "/tmp/x/solve.msh", "mesh_content_sha256": "sha256:" + "c" * 64,
        "acoustic_domain": "free-space", "sources": [{"id": "s", "role": "HF", "name": "Ünïcode"}],
        "source_tags": {"s": 2}, "symmetry": {"domain_planes": []}, "polar_grid_derivation": {"axes": {}},
        "normalisation": {"matrix": None}, "findings": [], "skipped_source_ids": [], "mesh": {"stats": {}},
        "domain_interpretation": {"type": "t"}, "tag_map": {}, "identity": {"instances": []},
        "values": np.asarray([1.5, 2.5]) if producer == "cad" else [1.5, 2.5],
    }
    return record


def test_file_records_keep_their_plain_json_bytes():
    record = _record("mesh-file")
    assert seal_prepared_record(copy.deepcopy(record), producer="mesh-file") == json.dumps(record, allow_nan=False)


@pytest.mark.parametrize(("producer", "encode"), [
    ("cad", canonical_normalized_bytes), ("native", canonical_ascii_strict_bytes),
])
def test_cad_and_native_records_keep_their_canonical_identities(producer, encode):
    record = _record(producer)
    expected = copy.deepcopy(record)
    expected["solve_model_sha256"] = solve_model_sha256(expected)
    expected["report_sha256"] = "sha256:" + hashlib.sha256(encode(expected)).hexdigest()
    sealed = seal_prepared_record(record, producer=producer)
    assert sealed == encode(expected).decode("utf-8")
    assert record["report_sha256"] == expected["report_sha256"]


@pytest.mark.parametrize(("change", "message"), [
    ({"ingest_id": "x"}, "wgi_"),
    ({"source_tags": {"other": 2}}, "recorded sources"),
    ({"source_tags": {"s": 0}}, "positive"),
    ({"sources": [{"id": "s"}, {"id": "s"}]}, "unique"),
    ({"skipped_source_ids": ["nope"]}, "skips unknown"),
    ({"mesh_content_sha256": "md5:1"}, "sha256"),
])
def test_every_producer_is_held_to_the_same_invariants(change, message):
    with pytest.raises(ValueError, match=message):
        check_prepared_record({**_record("mesh-file"), **change})
    record = _record("mesh-file")
    del record["polar_grid_derivation"]
    with pytest.raises(ValueError, match="incomplete"):
        check_prepared_record(record)


# -- compiling a design -------------------------------------------------------------


def test_compiled_horn_only_is_the_ordinary_design_mesh_after_tag_normalisation(store, tmp_path):
    ordinary = _build_sync(DesignConfig.model_validate(HORN).model_dump(mode="json"))
    outcome = compile_(store, tmp_path, design(assembly=assembly(woofer(enabled=False))))
    record = outcome["record"]
    assert outcome["status"] == "published" and not outcome["reused"]
    normalised, _ = prepare_file_mesh(parse_file_mesh(ordinary["msh_text"]), "m", "+z", "+y")
    compiled = parse_file_mesh(open(record["mesh_store_path"], encoding="utf-8").read())
    assert np.array_equal(compiled.points, normalised.points)
    assert np.array_equal(compiled.triangles, normalised.triangles)
    assert np.array_equal(compiled.tags == record["source_tags"]["horn-throat"], normalised.tags == 2)
    assert mesh_text(normalised) == open(record["mesh_store_path"], encoding="utf-8").read()
    assert record["sources"] == [{
        "id": "horn-throat", "role": "HF", "required": True, "name": "HF horn throat",
        "radiator_id": "horn-throat", "segment_id": None,
        "observed": {"total_area_mm2": pytest.approx(record["sources"][0]["observed"]["total_area_mm2"])},
    }]
    assert [r["state"] for r in record["radiators"]] == ["compiled", "disabled"]
    assert record["excitations"] == [{"id": "horn-throat", "source_ids": ["horn-throat"], "motion": "normal",
                                     "patch_weights": {"horn-throat": 1.0}}]


def test_a_lower_woofer_compiles_with_roles_tags_evidence_and_full_domain(store, tmp_path):
    lower = woofer(W2, shape="ath_cone")
    lower["placement"] = {"anchor": "free", "x_mm": 0, "y_mm": -400,
                          "spacing_to": {"radiator_id": W1, "distance_mm": 175}}
    tall = design(assembly=assembly(woofer(), lower))
    tall["enclosure"]["space_b"] = 450
    record = compile_(store, tmp_path, tall)["record"]
    assert record["producer"] == "design-assembly"
    assert {s["id"]: s["role"] for s in record["sources"]} == {
        "horn-throat": "HF", f"{W1}/piston": "LF", f"{W2}/cap": "LF", f"{W2}/cone": "LF"}
    tags = record["source_tags"]
    assert tags["horn-throat"] == 2 and len(set(tags.values())) == 4
    assert {int(k): v["role"] for k, v in record["tag_map"].items()} == {tags[k]: r for k, r in (
        ("horn-throat", "HF"), (f"{W1}/piston", "LF"), (f"{W2}/cap", "LF"), (f"{W2}/cone", "LF"))}
    mesh = parse_file_mesh(open(record["mesh_store_path"], encoding="utf-8").read())
    for source in record["sources"]:
        assert source["observed"]["total_area_mm2"] > 0
        assert np.any(mesh.tags == tags[source["id"]])
    # Default full domain; a lower woofer is no mirror image of itself in y.
    assert record["symmetry"]["solved_domain"] == "full" and record["symmetry"]["domain_planes"] == []
    assert record["symmetry"]["excitation_mirror_invariance"] == {
        W1: {"x0": True, "y0": False}, W2: {"x0": True, "y0": False}}
    assert {axis["minimum_deg"] for axis in record["polar_grid_derivation"]["axes"].values()} == {-180.0}
    assert record["evidence"] == {"fem_regions": [], "fem_air_volumes": []}
    assert record["normalisation"]["assembly_frame_is_solver_frame"] is True
    woofers = {r["radiator_id"]: r for r in record["radiators"]}
    assert woofers[W1]["center_mm"] == [0.0, -190.0] and woofers[W2]["center_mm"] == [0.0, -365.0]
    assert woofers[W1]["motion_axis"] == [0.0, 0.0, 1.0]
    assert woofers[W2]["projected_area_m2"] == pytest.approx(np.pi * 0.08**2)
    assert [p["segment_id"] for p in woofers[W2]["patches"]] == ["cap", "cone"]
    assert record["signal_groups"] == [
        {"id": G_HF, "label": "HF", "members": ["horn-throat"]},
        {"id": G_LF, "label": "LF", "members": [W1, W2]}]
    assert record["solve_model_sha256"].startswith("sha256:") and record["report_sha256"].startswith("sha256:")
    assert record["mesh"]["metadata"]["mesher_features"] == ["design-enclosure-woofers"]
    row = store.get_ingest(record["ingest_id"])
    assert row["model_id"] == design_model_id(UID)
    assert store.get_model(design_model_id(UID))["producer"] == "design-assembly"


def test_rigid_woofers_cut_the_baffle_but_are_not_driven(store, tmp_path):
    record = compile_(store, tmp_path, design(assembly=assembly(woofer(drive="rigid"))))["record"]
    assert [s["id"] for s in record["sources"]] == ["horn-throat"]
    assert {r["radiator_id"]: r["state"] for r in record["radiators"]} == {"horn-throat": "compiled", W1: "rigid"}


def test_identical_geometry_reuses_and_processing_changes_never_rebuild(store, tmp_path):
    base = design(assembly=assembly(woofer()))
    first = compile_(store, tmp_path, base)["record"]
    processed = copy.deepcopy(base)
    processed["assembly"]["signal_groups"][1]["members"][0].update(delay_ms=1.5, invert=True)
    processed["assembly"]["drivers"] = {W1: {"exterior_transducer": {"re_ohm": 6.0}}}
    processed["simulation"] = {"f1": 100, "f2": 2000, "num_frequencies": 5}
    again = compile_(store, tmp_path, processed, revision=2)
    assert again["reused"] and again["record"]["ingest_id"] == first["ingest_id"]
    moved = copy.deepcopy(base)
    moved["assembly"]["woofers"][0]["placement"]["gap_mm"] = 20
    other = compile_(store, tmp_path, moved, revision=3)["record"]
    assert other["ingest_id"] != first["ingest_id"]
    assert other["manifest_sha256"] != first["manifest_sha256"]
    assert other["design"]["revision"] == 3


def test_a_superseded_build_publishes_nothing(store, tmp_path, monkeypatch):
    fence = AssemblyBuildFence()
    real = design_assembly.build_assembly_mesh

    async def newer_arrives(design, cancel_cb=None):
        fence.begin(UID, 9)
        return await real(design, cancel_cb)

    monkeypatch.setattr(design_assembly, "build_assembly_mesh", newer_arrives)
    assert compile_(store, tmp_path, design(assembly=assembly(woofer())), revision=4, fence=fence) == {"status": "superseded"}
    assert store.list_model_ingests(design_model_id(UID)) == []
    with pytest.raises(AssemblySuperseded):
        compile_(store, tmp_path, design(assembly=assembly(woofer())), revision=5, fence=fence)


def test_the_fence_is_checked_inside_the_publishing_transaction(store, tmp_path, monkeypatch):
    fence = AssemblyBuildFence()
    published = design_assembly._publish

    def newer_during_publication(*args):
        published(*args)
        fence.begin(UID, 9)

    monkeypatch.setattr(design_assembly, "_publish", newer_during_publication)
    assert compile_(store, tmp_path, design(assembly=assembly(woofer())), revision=4, fence=fence) == {"status": "superseded"}
    assert store.list_model_ingests(design_model_id(UID)) == []
    assert list((tmp_path / "imports" / "files").iterdir()) == []


@pytest.mark.parametrize(("dump", "message"), [
    (HORN, "no Sources and baffle assembly"),
    ({**HORN, "enclosure": {"depth": 0}, "assembly": assembly(woofer())}, "Set an enclosure depth"),
    ({**HORN, "assembly": assembly(woofer(gap=-200))}, "greater than or equal"),
])
def test_unsupported_assemblies_are_refused_precisely(store, tmp_path, dump, message):
    with pytest.raises((AssemblyRefusal, ValueError), match=message):
        compile_(store, tmp_path, dump)


def test_an_overlapping_woofer_is_refused_by_the_design_mesher(store, tmp_path):
    with pytest.raises(Exception, match="clearance|overlap|mouth"):
        compile_(store, tmp_path, design(assembly=assembly(woofer(gap=0))))
    assert store.list_model_ingests(design_model_id(UID)) == []


def test_an_unsaved_design_id_is_refused_before_building(store, tmp_path):
    with pytest.raises(AssemblyRefusal, match="Save the design"):
        asyncio.run(compile_design_assembly(
            store, tmp_path, design(assembly=assembly()), design_uid="nope",
            design_name="x", design_revision=1, fence=AssemblyBuildFence()))


def test_the_latest_compiled_record_is_retained_and_older_ones_collected(store, tmp_path):
    jobs = JobStore.for_data_dir(tmp_path)
    jobs.initialize()
    old = compile_(store, tmp_path, design(assembly=assembly(woofer())))["record"]
    new = compile_(store, tmp_path, design(assembly=assembly(woofer(gap=30))), revision=2)["record"]
    past = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    with store._lock, store._transaction() as conn:
        conn.execute("UPDATE ingests SET created_at = ?, last_used_at = NULL", (past,))
    report = collect_prepared_meshes(store, jobs, tmp_path, terminal_states=TERMINAL_STATES)
    assert report.removed_ingests == [old["ingest_id"]]
    assert store.get_ingest(new["ingest_id"]) is not None
    assert open(new["mesh_store_path"], encoding="utf-8").read().startswith("$MeshFormat")
    assert not (tmp_path / "imports" / "files" / old["ingest_id"]).exists()


def test_a_store_made_before_assembly_models_admits_them_and_keeps_its_rows(tmp_path):
    import sqlite3

    first = CadLinkStore.for_data_dir(tmp_path)
    first.initialize()
    path = first.db_path
    first.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE imported_models")
        conn.execute(
            "CREATE TABLE imported_models (model_id TEXT PRIMARY KEY, producer TEXT NOT NULL "
            "CHECK (producer IN ('mesh-file', 'cad')), display_name TEXT NOT NULL, archive_stem TEXT, "
            "latest_ingest_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("INSERT INTO imported_models VALUES ('mesh-file:sha256:1', 'mesh-file', 'a.msh', 'a', "
                     "'wgi_1', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')")
    reopened = CadLinkStore.for_data_dir(tmp_path)
    try:
        assert reopened.get_model("mesh-file:sha256:1")["display_name"] == "a.msh"
        record = compile_(reopened, tmp_path, design(assembly=assembly(woofer(enabled=False))))["record"]
        assert reopened.get_model(design_model_id(UID))["latest_ingest_id"] == record["ingest_id"]
    finally:
        reopened.close()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'imported_models%'").fetchone()[0] == 1


def test_the_models_api_compiles_and_draws_an_assembly(store, tmp_path):
    from test_models_api import app_for, call

    async def scenario():
        app, runtime = app_for(tmp_path, store)
        try:
            dump = design(assembly=assembly(woofer()))
            context = await call(app, "POST", "/api/models/assembly/context", {"design": dump})
            assert context["mouth_bottom_mm"] == -100.0 and context["woofers"][0]["y_mm"] == -190.0
            assert context["clearances"][W1]["mouth_mm"] == pytest.approx(10.0)
            assert context["baffle"]["y0_mm"] == -350.0 and len(context["mouth_outline_mm"]) > 8
            refused = await call(app, "POST", "/api/models/assembly/context",
                                 {"design": {**dump, "enclosure": {"depth": 0}}})
            assert "enclosure depth" in refused["refusal"]
            body = {"design": dump, "design_uid": UID, "design_name": "Front horn", "design_revision": 2}
            compiled = await call(app, "POST", "/api/models/assembly/compile", body)
            assert compiled["status"] == "published" and compiled["model"]["producer"] == "design-assembly"
            prepared = compiled["prepared"]
            assert prepared["msh_text"].startswith("$MeshFormat") and prepared["design"]["revision"] == 2
            assert [r["radiator_id"] for r in prepared["radiators"]] == ["horn-throat", W1]
            older = await call(app, "POST", "/api/models/assembly/compile", {**body, "design_revision": 1})
            assert older == {"status": "superseded"}
            bad = await call(app, "POST", "/api/models/assembly/compile",
                             {**body, "design_revision": 3, "design": HORN}, expected=422)
            assert bad["error"]["code"] == "assembly_refused"
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())
