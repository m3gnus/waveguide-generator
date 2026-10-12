"""Declared regions through real intake, isolated meshing and verified storage."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from server.cadlink.intake import ingest
from server.cadlink.intake.isolation import ChildRefusal
from server.cadlink.store import CadLinkStore
from server.mesh.artifact import ImportedMeshArtifactError, read_verified_fem_region_mesh
from server.mesh.fem_regions import physical_name, read_fem_region_domain
from server.tests.test_cadlink_wgreturn import _manifest, write_bundle
from server.tests.test_fem_regions import CONE, _face, _region, _write_step


def _interior_region(**overrides):
    # F6 coverage: no interface with the exterior, so keep the independent child.
    region = deepcopy(_region(**overrides))
    region["surface_groups"][1]["kind"] = "plane-wave-termination"
    return region


def _bundle(root, *, regions=None):
    root.mkdir(parents=True)
    step = _write_step(root / "box.step").read_bytes()
    manifest = _manifest(step)
    manifest["instances"] = []
    manifest["sources"] = []
    manifest["coordinate_system"]["solver_anchor_instance_id"] = None
    manifest["assembly"]["bbox_mm"] = [[0, 0, 0], [40, 30, 20]]
    manifest["scope"]["included"] = [{
        "object_id": "box", "name": "Box", "body_kind": "solid", "visible": True,
        "external_reference": "local",
    }]
    manifest["required_features"].append("fem-regions-v1")
    manifest["scope"]["fem_regions"] = regions if regions is not None else [_interior_region()]
    for region in manifest["scope"]["fem_regions"]:
        manifest["files"][region["file"]] = {
            "sha256": "sha256:" + hashlib.sha256(step).hexdigest(), "size_bytes": len(step),
            "media_type": "model/step", "purpose": "fem-region",
        }
    bundle = write_bundle(root, manifest, step=step)
    for region in manifest["scope"]["fem_regions"]:
        path = bundle / region["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(step)
    return bundle


def _ingest(bundle, root):
    store = CadLinkStore(root / "cadlink.db")
    try:
        return ingest.ingest_bundle(bundle, {
            "rigid_size_mm": 12, "transition_mm": 12,
            "source_size_mm": {
                source["id"]: 8 for source in json.loads((bundle / "wgreturn.json").read_text())["sources"]
            },
        }, [], store, root / "data", defer_viewport=True)
    finally:
        store.close()


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")


def test_optional_physics_refusal_lists_all_groups_and_clears_after_remedy(tmp_path, monkeypatch):
    regions = [_interior_region(), _interior_region(
        region_id="front-chamber", name="Front chamber", object_id="air-2",
        file="fem-regions/front-chamber.step")]
    for region in regions:
        moving, wall = region["surface_groups"]
        moving["relative_level_db"] = -6.
        wall.update(kind="rigid", lining={
            "model": "miki", "thickness_mm": 30., "flow_resistivity_pa_s_per_m2": 5000.,
        })
        second_wall = deepcopy(wall)
        second_wall.update(group_id="wall", name="Wall")
        region["surface_groups"].append(second_wall)
    bundle = _bundle(tmp_path / "returns", regions=regions)

    def unexpected(*_args, **_kwargs):
        pytest.fail("unsupported FEM options must refuse before meshing")

    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", unexpected)
    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", unexpected)
    monkeypatch.setattr(ingest, "build_coupled_mesh_isolated", unexpected)
    with pytest.raises(ingest.IngestRefusal) as caught:
        _ingest(bundle, tmp_path)
    assert caught.value.reason_code == "fem_motion_weight_unsupported"
    assert caught.value.stage == "stage 2 scope gate"
    message = str(caught.value)
    for region in regions:
        for group in region["surface_groups"]:
            assert (f"FEM region {region['name']!r} ({region['region_id']!r}), "
                    f"group {group['name']!r} ({group['group_id']!r})") in message
    assert "fem_motion_weight_unsupported" not in message
    assert "fem_lining_unsupported" not in message
    assert ("remove the lining (or set the relative level to 0 dB) "
            "on these groups in WGLink and send again") in message
    assert not (tmp_path / "cadlink.db").exists()

    from server.solver.coupled_admission import fem_group_options_problem

    for region in regions:
        for group in region["surface_groups"]:
            group.pop("lining", None)
            if "relative_level_db" in group:
                group["relative_level_db"] = 0.
    assert fem_group_options_problem(regions) is None


@pytest.mark.parametrize("code", [None, "fem_lining_unsupported", "fem_motion_weight_unsupported"])
def test_rebuilt_child_refusal_preserves_reason_code(tmp_path, monkeypatch, code):
    bundle = _bundle(tmp_path / "returns")
    refusal = ChildRefusal("stage 7 meshing", "child refusal",
                           details={"reason_code": code} if code else {})

    def refuse(*_args, **_kwargs):
        raise refusal

    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", refuse)
    with pytest.raises(ingest.IngestRefusal) as caught:
        _ingest(bundle, tmp_path)
    assert caught.value.reason_code == code
    assert str(caught.value) == "stage 7 meshing: child refusal"
    assert caught.value.__cause__ is refusal
    with sqlite3.connect(tmp_path / "cadlink.db") as db:
        assert db.execute("SELECT count(*) FROM ingests").fetchone()[0] == 0
    assert not any(path.is_file() for path in (tmp_path / "data" / "imports").rglob("*"))


def test_real_ingest_meshes_every_region_and_retains_verified_artifacts(tmp_path, monkeypatch):
    second = _interior_region(region_id="front-chamber", name="Front chamber", object_id="air-2",
                     file="fem-regions/front-chamber.step", mesh_size_mm=10)
    bundle = _bundle(tmp_path / "returns", regions=[_interior_region(), second])
    calls = []
    exterior = ingest.build_imported_mesh_isolated
    region_mesh = ingest.build_fem_region_mesh_isolated

    def exterior_call(*args, **kwargs):
        calls.append("exterior")
        return exterior(*args, **kwargs)

    def region_call(path, region, **kwargs):
        calls.append(region["region_id"])
        member = json.loads((bundle / "wgreturn.json").read_text())["files"][region["file"]]
        assert kwargs.pop("deadline") > ingest.time.monotonic()
        assert kwargs.pop("domain_planes") == ()
        assert kwargs.pop("cut_planes") == ()
        assert len(kwargs.pop("normalisation_matrix")) == 4
        assert kwargs == {"expected_sha256": member["sha256"], "expected_size_bytes": member["size_bytes"]}
        return region_mesh(path, region, **kwargs)

    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", exterior_call)
    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", region_call)
    record = _ingest(bundle, tmp_path)
    assert calls == ["exterior", "rear-chamber", "front-chamber"]
    assert len(record["fem_regions"]) == 2
    for region in record["fem_regions"]:
        text = read_verified_fem_region_mesh(record, region["region_id"])
        domain = read_fem_region_domain(text, region["region_id"])
        assert region["stats"]["tetrahedra"] == len(domain.tetrahedra) > 0
        assert region["stats"]["nodes"] == len(domain.points_mm) > 0
        assert Path(region["mesh_store_path"]).as_posix().endswith(f"fem/region-{region['region_id']}.msh")
        assert len(region["mesh_lookup_key"]) == len(region["mesh_cache_key"]) == 64
        assert region["mesh_size_bytes"] == len(text.encode())
        groups = region["stats"]["surface_groups"]
        assert groups["cone"]["area_mm2"] == pytest.approx(1200)
        assert groups["port"]["area_mm2"] == pytest.approx(600)
        assert groups["_walls"]["area_mm2"] == pytest.approx(3400)
        assert groups["cone"]["face_count"] == groups["port"]["face_count"] == 1
        assert groups["_walls"]["face_count"] == 4
    store = CadLinkStore(tmp_path / "cadlink.db")
    try:
        retained = json.loads(store.get_ingest(record["ingest_id"])["record_json"])
    finally:
        store.close()
    assert retained["fem_regions"] == record["fem_regions"]
    corrupted = deepcopy(record)
    corrupted["fem_regions"][0]["mesh_size_bytes"] += 1
    with pytest.raises(ImportedMeshArtifactError, match="size mismatch"):
        read_verified_fem_region_mesh(corrupted, "rear-chamber")
    path = Path(record["fem_regions"][0]["mesh_store_path"])
    path.write_text(path.read_text().replace("4.1", "4.2", 1))
    with pytest.raises(ImportedMeshArtifactError, match="digest mismatch"):
        read_verified_fem_region_mesh(record, "rear-chamber")
    path.unlink()
    with pytest.raises(ImportedMeshArtifactError, match="unavailable"):
        read_verified_fem_region_mesh(record, "rear-chamber")
    with pytest.raises(ImportedMeshArtifactError, match="no unique FEM region"):
        read_verified_fem_region_mesh(record, "absent")
    changed_medium = deepcopy(record)
    changed_medium["fem_regions"][0]["medium"]["density_kg_per_m3"] = 1.3
    assert ingest.solve_model_sha256(changed_medium) != record["solve_model_sha256"]


@pytest.mark.parametrize("failure", ["lost", "shared", "two-solids"])
def test_region_geometry_refusals_block_ingest_and_name_region_and_group(tmp_path, failure):
    region = _interior_region()
    if failure == "lost":
        region["surface_groups"][0]["faces"] = [
            _face(1200, (20, 15, 5), [0, 0, 1], (0, 0, 5), (40, 30, 5)),
        ]
    elif failure == "shared":
        region["surface_groups"][1]["faces"] = [CONE]
    bundle = _bundle(tmp_path / "returns", regions=[region])
    if failure == "two-solids":
        path = bundle / region["file"]
        _write_step(path, boxes=((0, 0, 0), (100, 0, 0)))
        manifest_path = bundle / "wgreturn.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][region["file"]].update(
            sha256="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size,
        )
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ingest.IngestRefusal, match="Rear chamber") as caught:
        _ingest(bundle, tmp_path)
    assert caught.value.stage == "stage 7 meshing"
    if failure != "two-solids":
        assert "Cone" in str(caught.value)
    with sqlite3.connect(tmp_path / "cadlink.db") as db:
        assert db.execute("SELECT count(*) FROM ingests").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["size", "digest"])
def test_region_member_changed_after_admission_refuses_in_child(tmp_path, monkeypatch, mutation):
    bundle = _bundle(tmp_path / "returns")
    original = ingest.build_imported_mesh_isolated

    def mutate_after_exterior(*args, **kwargs):
        built = original(*args, **kwargs)
        path = bundle / "fem-regions/rear-chamber.step"
        raw = path.read_bytes()
        path.write_bytes(raw + b" " if mutation == "size" else raw.replace(b"ISO", b"BAD", 1))
        return built

    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", mutate_after_exterior)
    with pytest.raises(ingest.IngestRefusal, match="Rear chamber"):
        _ingest(bundle, tmp_path)


def test_no_regions_ingest_is_identical_with_flag_on_or_off(tmp_path, monkeypatch):
    from server.tests.test_cadlink_ingest import _built_for_source

    bundle = _bundle(tmp_path / "returns", regions=[])
    path = bundle / "wgreturn.json"
    manifest = json.loads(path.read_text())
    manifest["required_features"].remove("fem-regions-v1")
    del manifest["scope"]["fem_regions"]
    source = _manifest(b"STEP")["sources"][0]
    source["instance_id"] = None
    source["selectors"] = {"appearance_labels": ["HF"]}
    manifest["sources"] = [source]
    path.write_text(json.dumps(manifest))

    def unexpected(*args, **kwargs):
        pytest.fail("a return without regions must not run a region child")

    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", unexpected)
    monkeypatch.setattr(ingest, "build_imported_mesh_isolated",
                        lambda *args, **kwargs: _built_for_source(source["id"]))
    _ingest(bundle, tmp_path)  # Prime the exterior cache for both comparisons.
    enabled = _ingest(bundle, tmp_path)
    monkeypatch.setenv("WG2_FEM_REGIONS", "0")
    disabled = _ingest(bundle, tmp_path)
    assert "fem_regions" not in enabled
    assert "fem_regions" not in disabled
    for record in (enabled, disabled):
        for key in ("ingest_id", "created_at", "report_sha256"):
            record.pop(key)
    assert enabled == disabled


def test_ingest_forwards_user_region_and_group_sizes_without_limits(tmp_path, monkeypatch):
    region = _interior_region(mesh_size_mm=0.001)
    region["surface_groups"][0]["mesh_size_mm"] = 0.0001
    bundle = _bundle(tmp_path / "returns", regions=[region])
    seen = []

    def mesh(_path, declared, **kwargs):
        seen.append(declared)
        return {"msh_text": "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n",
                "stats": {"tetrahedra": 1, "nodes": 4, "surface_groups": {}}, "physical_names": {}}

    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", mesh)
    record = _ingest(bundle, tmp_path)
    assert seen == [region]
    assert record["fem_regions"][0]["mesh_size_mm"] == 0.001
    assert record["fem_regions"][0]["surface_groups"][0]["mesh_size_mm"] == 0.0001


@pytest.mark.parametrize("kind", ["rigid", "moving"])
def test_ingest_user_walls_preserves_declared_group_and_unclaimed_boundary(tmp_path, kind):
    region = _interior_region()
    region["surface_groups"][0].update(group_id="walls", name="User walls", kind=kind)
    if kind == "rigid":
        region["surface_groups"][0].pop("drive_channel_id")
        # Keep an actual diaphragm in this interior-only return.
        region["surface_groups"][1].update(kind="moving", drive_channel_id="drive-lf")
    record = _ingest(_bundle(tmp_path / "returns", regions=[region]), tmp_path)
    retained = record["fem_regions"][0]
    groups = retained["stats"]["surface_groups"]
    assert groups["walls"]["kind"] == kind
    assert groups["walls"]["triangle_count"] > 0
    assert groups["_walls"]["kind"] == "rigid"
    assert groups["_walls"]["triangle_count"] > 0
    assert groups["walls"]["area_mm2"] == pytest.approx(1200)
    assert groups["_walls"]["area_mm2"] == pytest.approx(3400)
    assert sum(group["area_mm2"] for group in groups.values()) == pytest.approx(5200)
    domain = read_fem_region_domain(read_verified_fem_region_mesh(record, region["region_id"]), region["region_id"])
    assert physical_name(region["region_id"], "walls", kind) in domain.surfaces
    assert physical_name(region["region_id"], "_walls", "rigid") in domain.surfaces


@pytest.mark.parametrize("region_id", ["con", "nul", "aux", "prn", "com1", "lpt1"])
def test_reserved_device_region_ids_use_safe_mesh_filenames(tmp_path, monkeypatch, region_id):
    from types import SimpleNamespace

    region = _region(region_id=region_id)
    bundle = SimpleNamespace(manifest={"scope": {"fem_regions": [region]},
                                      "files": {region["file"]: {"sha256": "sha256:step", "size_bytes": 1}}},
                             members={region["file"]: tmp_path / "air.step"})
    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", lambda *args, **kwargs:
                        {"msh_text": "mesh", "stats": {}, "physical_names": {}})
    (retained,) = ingest._mesh_fem_regions(bundle, tmp_path)
    assert retained["region_id"] == region_id
    assert Path(retained["mesh_store_path"]).name == f"region-{region_id}.msh"
    assert read_verified_fem_region_mesh({"fem_regions": [retained]}, region_id) == "mesh"


@pytest.mark.parametrize("failure", ["publish", "verify"])
def test_artifact_failures_refuse_by_region(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace

    region = _region()
    bundle = SimpleNamespace(manifest={"scope": {"fem_regions": [region]},
                                      "files": {region["file"]: {"sha256": "sha256:step", "size_bytes": 1}}},
                             members={region["file"]: tmp_path / "air.step"})
    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", lambda *args, **kwargs:
                        {"msh_text": "mesh", "stats": {}, "physical_names": {}})

    def fail(*args, **kwargs):
        raise OSError("artifact failure")

    monkeypatch.setattr(ingest, "_write_cache" if failure == "publish" else "read_verified_import_mesh", fail)
    with pytest.raises(ingest.IngestRefusal, match="Rear chamber.*publish and verify.*artifact failure") as caught:
        ingest._mesh_fem_regions(bundle, tmp_path)
    assert caught.value.stage == "stage 7 meshing"


@pytest.mark.parametrize("exhaustion", ["before-next", "during-child", "during-publish"])
def test_one_total_budget_covers_all_regions_and_publication(tmp_path, monkeypatch, exhaustion):
    from types import SimpleNamespace

    regions = [_region(), _region(region_id="front", name="Front chamber", file="fem-regions/front.step")]
    bundle = SimpleNamespace(manifest={"scope": {"fem_regions": regions}, "files": {
        region["file"]: {"sha256": "sha256:step", "size_bytes": 1} for region in regions}},
        members={region["file"]: tmp_path / "air.step" for region in regions})
    now = [0.0]
    monkeypatch.setattr(ingest.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ingest, "FEM_REGIONS_WALL_TIME_S", 10.0)
    called = []

    def mesh(*args, **kwargs):
        called.append(kwargs["deadline"])
        now[0] += 6.0 if exhaustion == "during-child" else 1.0
        return {"msh_text": "mesh", "stats": {}, "physical_names": {}}

    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", mesh)
    original = ingest._write_cache

    def publish(*args, **kwargs):
        original(*args, **kwargs)
        if exhaustion == "during-publish":
            now[0] = 11.0

    monkeypatch.setattr(ingest, "_write_cache", publish)
    # Cross the deadline on the check before the second child, while the first
    # child and its publication are still within budget.
    if exhaustion == "before-next":
        values = iter([0.0, 0.0, 1.0, 2.0, 11.0])
        monkeypatch.setattr(ingest.time, "monotonic", lambda: next(values))
    label = "Rear chamber" if exhaustion == "during-publish" else "Front chamber"
    with pytest.raises(ingest.IngestRefusal, match=f"{label}.*total FEM regions wall-clock budget"):
        ingest._mesh_fem_regions(bundle, tmp_path)
    assert called == ([10.0, 10.0] if exhaustion == "during-child" else [10.0])
