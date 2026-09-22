"""M1c-auto: the automatic domain, on real generated geometry.

PLAN.md, "M1c-auto. Automatic domain; the Model dropdown is removed" (as
narrowed on 2026-09-22). A model that is already cut is mirrored only with
recorded evidence of the cut -- a declaration, an earlier explicit or
provenance-backed interpretation of the same lineage, or cut provenance the
add-in recorded -- and otherwise solved exactly as shown. Nothing here asks.

Every fixture is built with gmsh, written as STEP, and put through the
production ``ingest_bundle`` (bundle reader, gates, the isolated mesher child,
record publication). The expectations were written before the rule existed.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest

from server.cadlink.ingest import IngestRefusal, ingest_bundle
from server.cadlink.store import CadLinkStore
from server.mesh.gmsh_worker import _run_in_gmsh_session
from test_cadlink_ingest_symmetry import _count_step_shell_bodies, _write_horn_step


gmsh = pytest.importorskip("gmsh")

AUTOMATIC = "domain-automatic-v1"
REDUCED = "reduced-domain-v1"
MESH_SIZES = {"rigid_size_mm": 20, "transition_mm": 30}
NATIVE_ID = "urn:adsk.wipprod:dm.lineage:auto-domain-fixture"

_RETURN_SEQUENCE = iter(range(10_000))
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _return_id() -> str:
    number = next(_RETURN_SEQUENCE)
    tail = ""
    for _ in range(6):
        tail = _CROCKFORD[number % 32] + tail
        number //= 32
    return "wgr_01J5A8QK3M9T2XVBH0RD" + tail


# ------------------------------------------------------------------ geometry


def _plane_faces(surfaces: list[int]) -> list[int]:
    return [tag for tag in surfaces if str(gmsh.model.getType(2, tag)).casefold() == "plane"]


def _flat_in_z(tag: int) -> bool:
    box = gmsh.model.getBoundingBox(2, tag)
    return abs(box[5] - box[2]) < 1.0e-6


def _source_faces(step_path: Path, pick: Callable[[list[int]], list[int]]) -> tuple[list[int], list[float]]:
    """The STEP ADVANCED_FACE indices of the faces ``pick`` chooses, and their areas."""

    from hornlab_mesher.step_import import advanced_face_order, gmsh_surface_tags

    def measure() -> tuple[list[int], list[float]]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(step_path), highestDimOnly=False)
        gmsh.model.occ.synchronize()
        surfaces = gmsh_surface_tags()
        chosen = pick(surfaces)
        order = advanced_face_order(step_path)
        indices = [order[surfaces.index(tag)] for tag in chosen]
        areas = [float(gmsh.model.occ.getMass(2, tag)) for tag in chosen]
        gmsh.clear()
        return indices, areas

    return _run_in_gmsh_session(measure)


def _horn_throat(surfaces: list[int]) -> list[int]:
    """The throat disc: the flat-in-z planar face nearest the bore."""

    flat = [tag for tag in _plane_faces(surfaces) if _flat_in_z(tag)]
    assert flat, "the horn fixture always has a planar throat face"
    return [max(flat, key=lambda tag: float(gmsh.model.occ.getCenterOfMass(2, tag)[2]))]


def _faces_near(*centres: tuple[float, float, float]) -> Callable[[list[int]], list[int]]:
    """The smallest planar face whose centre is at each of ``centres``."""

    def pick(surfaces: list[int]) -> list[int]:
        chosen = []
        for centre in centres:
            near = [
                tag
                for tag in _plane_faces(surfaces)
                if np.linalg.norm(np.asarray(gmsh.model.occ.getCenterOfMass(2, tag)) - centre) < 1.0
            ]
            assert near, centre
            chosen.append(min(near, key=lambda tag: float(gmsh.model.occ.getMass(2, tag))))
        return chosen

    return pick


def _horn(path: Path) -> None:
    """The round horn: radiates along +z, symmetric about x0 and y0."""

    _write_horn_step(path, vertical_offset_mm=0.0)


def _transform(path: Path, *, dx: float = 0.0) -> None:
    def move() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        shapes = gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
        gmsh.model.occ.translate(shapes, dx, 0.0, 0.0)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(move)


def _cut_open(path: Path, keep: dict[str, tuple[float, float]]) -> None:
    """Trim the model's faces to a box, leaving every cut OPEN (no cap face).

    ``keep`` maps an axis letter to the (low, high) retained; the other axes are
    kept whole. This is the shape an open Fusion cut leaves: the plane is a
    free rim, not a face.
    """

    def cut() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        box = [float(value) for value in gmsh.model.getBoundingBox(-1, -1)]
        volumes = gmsh.model.getEntities(3)
        if volumes:
            gmsh.model.occ.remove(volumes, recursive=False)
            gmsh.model.occ.synchronize()
        surfaces = [tag for _dim, tag in sorted(gmsh.model.getEntities(2))]
        low = [box[axis] - 10.0 for axis in range(3)]
        high = [box[axis + 3] + 10.0 for axis in range(3)]
        for letter, (lo, hi) in keep.items():
            axis = "xyz".index(letter)
            low[axis], high[axis] = max(low[axis], lo), min(high[axis], hi)
        tool = gmsh.model.occ.addBox(
            low[0], low[1], low[2], high[0] - low[0], high[1] - low[1], high[2] - low[2]
        )
        gmsh.model.occ.synchronize()
        gmsh.model.occ.intersect(
            [(2, surface) for surface in surfaces], [(3, tool)], removeObject=True, removeTool=True
        )
        gmsh.model.occ.synchronize()
        gmsh.model.occ.healShapes(sewFaces=True, makeSolids=False)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(cut)


def _cut_capped(path: Path) -> None:
    """Split the solid on x = 0 and keep x >= 0 WITH its cap face (a Fusion Split Body)."""

    def cut() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        box = [float(value) for value in gmsh.model.getBoundingBox(-1, -1)]
        tool = gmsh.model.occ.addBox(
            0.0, box[1] - 10.0, box[2] - 10.0,
            box[3] + 10.0, box[4] - box[1] + 20.0, box[5] - box[2] + 20.0,
        )
        gmsh.model.occ.intersect(gmsh.model.getEntities(3), [(3, tool)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(cut)


def _box(
    path: Path,
    *,
    x: tuple[float, float],
    discs: list[tuple[float, float, float]],
    solid: bool = False,
    open_x0_face: bool = False,
    slot_in_x0_face: bool = False,
) -> None:
    """A rectangular enclosure, y in [-40, 40], z in [-80, 0], with discs on its top.

    ``open_x0_face`` leaves out the wall on x = x[0] (an open-backed shell, or
    equally a closed box split on x = 0 with the +x side kept open); a
    ``slot_in_x0_face`` cuts a port through that wall instead.
    """

    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        occ = gmsh.model.occ
        volume = occ.addBox(x[0], -40.0, -80.0, x[1] - x[0], 80.0, 80.0)
        tools = [(2, occ.addDisk(cx, cy, 0.0, radius, radius)) for cx, cy, radius in discs]
        if slot_in_x0_face:
            slot = occ.addRectangle(-10.0, -30.0, 0.0, 20.0, 12.0)
            # The rectangle is drawn in z = 0; stand it up in the x = x[0] wall.
            occ.rotate([(2, slot)], 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, -math.pi / 2)
            occ.translate([(2, slot)], x[0], 0.0, -40.0)
            tools.append((2, slot))
        occ.fragment([(3, volume)], tools)
        occ.synchronize()
        if not solid:
            occ.remove(gmsh.model.getEntities(3), recursive=False)
            occ.synchronize()
        doomed = []
        for _dim, tag in gmsh.model.getEntities(2):
            box = gmsh.model.getBoundingBox(2, tag)
            on_x0 = abs(box[0] - x[0]) < 1e-6 and abs(box[3] - x[0]) < 1e-6
            if not on_x0:
                continue
            area = float(occ.getMass(2, tag))
            if open_x0_face and area > 1000.0:
                doomed.append((2, tag))
            if slot_in_x0_face and abs(area - 240.0) < 1.0:
                doomed.append((2, tag))
        if doomed:
            occ.remove(doomed, recursive=False)
            occ.synchronize()
        if not solid:
            occ.healShapes(sewFaces=True, makeSolids=False)
            occ.synchronize()
        # Anything floating inside a face (a leftover disc copy) is not the model.
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(build)


# ------------------------------------------------------------------ bundles


def provenance(
    body: str,
    plane: str = "x0",
    *,
    kept_side: str = "positive",
    name: str = "Split Body 3",
    kind: str = "split-body",
    export_frame: str = "root-component",
) -> dict[str, Any]:
    """One recorded cut, as the add-in writes it (``assembly.cut_provenance[]``)."""

    return {
        "body_object_id": body,
        "feature": {"kind": kind, "name": name},
        "tool": {"kind": "origin-plane", "origin_plane": {"x0": "YZ", "y0": "XZ", "z0": "XY"}[plane]},
        "plane": plane,
        "kept_side": kept_side,
        "export_frame": export_frame,
    }


def _bundle(
    root: Path,
    name: str,
    geometry: Callable[[Path], None],
    pick: Callable[[list[int]], list[int]],
    *,
    domain: str | tuple[str, ...] | None = "automatic",
    cut: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
    native_id: str | None = NATIVE_ID,
    edit: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    """A CAD-authored (unlinked) return of ``geometry``.

    ``domain``: None writes none (an add-in before M1c-auto), "automatic" what
    a new add-in writes, a tuple of planes a hand declaration. ``cut`` is the
    recorded cut provenance (object ids ``body-0``..). ``sources`` names one
    source per picked face; one source owns them all when absent.
    """

    bundle = root / "workspace" / "wgreturn" / f"{name}.wgreturn"
    bundle.mkdir(parents=True)
    step_path = bundle / "assembly.step"
    geometry(step_path)
    faces, areas = _source_faces(step_path, pick)
    step = step_path.read_bytes()
    bodies = _count_step_shell_bodies(step_path)
    solid = "MANIFOLD_SOLID_BREP" in step.decode("utf-8", errors="replace").upper()
    groups = (
        [(source_id, [face], [area]) for source_id, face, area in zip(sources, faces, areas, strict=True)]
        if sources
        else [("throat", faces, areas)]
    )
    manifest: dict[str, Any] = {
        "wgreturn_version": "1.0",
        "required_features": ["checksummed-files-v1", "assembly-frame-v1", "instance-records-v1"],
        "return": {"id": _return_id(), "created_at": "2026-09-22T09:14:03Z"},
        "generator": {"adapter": "hornlab-fusion-addin/WGLink", "adapter_version": "1.0.0", "cad_app": "fusion360", "cad_version": "2704.1.53"},
        "document": {"name": name, "native_id": native_id},
        "coordinate_system": {"length_unit": "mm", "handedness": "right", "matrix_convention": "row-major-local-to-parent"},
        "assembly": {"file": "assembly.step", "n_bodies_expected": bodies, "bbox_mm": [[-500, -500, -500], [500, 500, 500]]},
        "files": {"assembly.step": {"sha256": "sha256:" + hashlib.sha256(step).hexdigest(), "size_bytes": len(step), "media_type": "model/step", "purpose": "exterior-assembly"}},
        "scope": {
            "selection": "root",
            "included": [
                {"object_id": f"body-{index}", "name": f"Body{index + 1}", "body_kind": "solid" if solid else "surface", "visible": True, "external_reference": "local", "wglink_instance_id": None}
                for index in range(bodies)
            ],
            "skipped": [],
            "fem_air_volumes": [],
            "status": "clean",
        },
        "instances": [],
        "sources": [
            {
                "id": source_id,
                "role": "HF",
                "instance_id": None,
                "required": True,
                "default_drive_channel_id": f"drive-{source_id}",
                "patch_policy": "explicit-disconnected",
                "expected_connected_components": len(face_group),
                "selectors": {"advanced_face_indices": face_group},
                "observed": {"face_count": len(face_group), "total_area_mm2": sum(area_group), "per_face_area_mm2": area_group, "bodies": ["Body1"]},
                "suggested_resolution_mm": 8,
            }
            for source_id, face_group, area_group in groups
        ],
        "acoustics": None,
    }
    if domain == "automatic":
        manifest["required_features"].append(AUTOMATIC)
        manifest["assembly"]["domain"] = {"kind": "automatic"}
        if cut is not None:
            manifest["assembly"]["cut_provenance"] = cut
    elif isinstance(domain, tuple):
        manifest["required_features"].append(REDUCED)
        manifest["assembly"]["domain"] = {
            "kind": "half" if len(domain) == 1 else "quarter",
            "cut_planes": list(domain),
            "declared_by": "cad-author",
            "evidence": {plane: {"min_mm": 0.0, "max_mm": 34.0, "tolerance_mm": 0.05} for plane in domain},
        }
    if edit is not None:
        edit(manifest)
    (bundle / "wgreturn.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return bundle


def _open_half(path: Path) -> None:
    _horn(path)
    _cut_open(path, {"x": (0.0, math.inf)})


def _open_quarter(path: Path) -> None:
    _horn(path)
    _cut_open(path, {"x": (0.0, math.inf), "y": (0.0, math.inf)})


def _negative_half(path: Path) -> None:
    _horn(path)
    _cut_open(path, {"x": (-math.inf, 0.0)})


def _rim_off_plane(path: Path) -> None:
    _horn(path)
    _cut_open(path, {"x": (5.0, math.inf)})


def _capped_half(path: Path) -> None:
    _horn(path)
    _cut_capped(path)


def _off_centre(path: Path) -> None:
    _horn(path)
    _transform(path, dx=120.0)


def _split_open_backed_shell(path: Path) -> None:
    # A box split on the YZ plane, +x side kept open where the split was: an
    # open-backed shell. The driver sits clear of the plane.
    _box(path, x=(0.0, 60.0), discs=[(30.0, 0.0, 10.0)], open_x0_face=True)


def _slot_on_plane(path: Path) -> None:
    # A closed box whose x = 0 wall has a port through it, driver on top.
    _box(path, x=(0.0, 80.0), discs=[(40.0, 0.0, 10.0)], slot_in_x0_face=True)


def _mirrored_pair(path: Path) -> None:
    # A whole solid box, two drivers mirrored about x = 0.
    _box(path, x=(-60.0, 60.0), discs=[(-25.0, 0.0, 10.0), (25.0, 0.0, 10.0)], solid=True)


HORN_THROAT = _horn_throat
BOX_DRIVER = _faces_near((30.0, 0.0, 0.0))
SLOT_DRIVER = _faces_near((40.0, 0.0, 0.0))
PAIR_DRIVERS = _faces_near((-25.0, 0.0, 0.0), (25.0, 0.0, 0.0))


def _sizes(bundle: Path) -> dict[str, Any]:
    manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    return {**MESH_SIZES, "source_size_mm": {source["id"]: 8 for source in manifest["sources"]}}


def _ingest(bundle: Path, data_dir: Path, **prep: Any) -> dict[str, Any]:
    return _run_in_gmsh_session(
        ingest_bundle,
        bundle,
        _sizes(bundle),
        [],
        CadLinkStore(data_dir / "cadlink.db"),
        data_dir,
        prep_options={"symmetry_mode": "auto", **prep},
    )


def _store(data_dir: Path) -> CadLinkStore:
    return CadLinkStore(data_dir / "cadlink.db")


def _interpretation(record: dict[str, Any]) -> dict[str, Any]:
    interpretation = record.get("domain_interpretation")
    assert isinstance(interpretation, dict), sorted(record)
    return interpretation


def _multiplier(record: dict[str, Any]) -> float:
    return float(record["mesh"]["stats"]["domain_multiplier"])


def _no_blocking_domain_finding(record: dict[str, Any]) -> None:
    kinds = {finding["kind"] for finding in record["findings"] if finding.get("blocking")}
    assert "undeclared-reduced-domain" not in kinds, record["findings"]


# ------------------------------------------------------------------ fixtures


def test_open_half_with_provenance_is_mirrored_and_its_second_plane_cut(tmp_path: Path) -> None:
    """Recorded Fusion cut provenance that revalidates: mirrored, never asked.

    Smallest model: the horn is also symmetric about y = 0, which WG's mirror
    test validates, so the half becomes a quarter.
    """

    bundle = _bundle(tmp_path, "half-provenance", _open_half, HORN_THROAT, cut=[provenance("body-0")])
    record = _ingest(bundle, tmp_path / "data")

    interpretation = _interpretation(record)
    assert interpretation["reading"] == "reduced"
    assert interpretation["planes"] == ["x0"]
    assert interpretation["evidence"]["source"] == "cad-provenance"
    assert interpretation["evidence"]["applied"] is True
    assert interpretation["evidence"]["features"] == [{"plane": "x0", "kind": "split-body", "name": "Split Body 3"}]
    assert record["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert record["symmetry"]["cut_planes"] == ["y0"]
    assert record["symmetry_verification"]["verified"] is True
    assert _multiplier(record) == 4.0
    assert record["normalisation"]["solver_frame"]["allowed_axes"] == ["+z"]
    _no_blocking_domain_finding(record)


def test_the_same_open_half_without_provenance_is_solved_as_shown(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "half-bare", _open_half, HORN_THROAT)
    record = _ingest(bundle, tmp_path / "data")

    interpretation = _interpretation(record)
    assert interpretation["reading"] == "as-shown"
    assert interpretation["planes"] == []
    assert interpretation["looks_cut"] == ["x0"]
    assert interpretation["evidence"]["source"] is None
    assert record["symmetry"]["domain_planes"] == []
    assert _multiplier(record) == 1.0
    # The Change control offers the reading that can validate.
    assert {"reading": "reduced", "planes": ["x0"]} in interpretation["choices"]
    # Frame: a model solved as shown is not restricted by a cut it may not have.
    assert len(record["normalisation"]["solver_frame"]["allowed_axes"]) == 6
    _no_blocking_domain_finding(record)


def test_change_to_half_is_checked_mirrored_and_remembered_for_the_lineage(tmp_path: Path) -> None:
    from server.cadlink.domain_interpretation import record_reading

    data_dir = tmp_path / "data"
    first = _ingest(_bundle(tmp_path, "half-change", _open_half, HORN_THROAT), data_dir)
    assert _interpretation(first)["reading"] == "as-shown"

    record_reading(_store(data_dir), first, {"reading": "reduced", "planes": ["x0"]})
    changed = _ingest(Path(first["bundle_store_path"]), data_dir)
    assert _interpretation(changed)["reading"] == "reduced"
    assert _interpretation(changed)["evidence"]["source"] == "user"
    assert changed["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert changed["mesh_cache_key"] != first["mesh_cache_key"]

    # A new version of the same document: the lineage's reading is reused
    # because it still revalidates on the new geometry.
    later = _ingest(_bundle(tmp_path, "half-change-v2", _open_half, HORN_THROAT), data_dir)
    assert _interpretation(later)["reading"] == "reduced"
    assert _interpretation(later)["evidence"]["source"] == "user-lineage"
    assert later["symmetry"]["domain_planes"] == ["x0", "y0"]

    # ... and a later full model of that lineage is simply a full model.
    full = _ingest(_bundle(tmp_path, "half-change-v3", _horn, HORN_THROAT), data_dir)
    assert _interpretation(full)["reading"] == "full"
    assert _interpretation(full)["evidence"]["applied"] is False
    assert full["symmetry"]["cut_planes"] == ["x0", "y0"]


def test_open_quarter_with_provenance_on_both_planes_is_a_quarter(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path, "quarter-provenance", _open_quarter, HORN_THROAT,
        cut=[provenance("body-0", "x0"), provenance("body-0", "y0", name="Split Body 4")],
    )
    record = _ingest(bundle, tmp_path / "data")

    assert _interpretation(record)["reading"] == "reduced"
    assert _interpretation(record)["planes"] == ["x0", "y0"]
    assert record["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert record["symmetry"]["cut_planes"] == []
    assert _multiplier(record) == 4.0


def test_open_quarter_without_provenance_is_solved_as_shown(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "quarter-bare", _open_quarter, HORN_THROAT), tmp_path / "data")

    assert _interpretation(record)["reading"] == "as-shown"
    assert _interpretation(record)["looks_cut"] == ["x0", "y0"]
    assert _multiplier(record) == 1.0
    _no_blocking_domain_finding(record)


def test_an_off_centre_complete_model_is_a_full_model(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "off-centre", _off_centre, HORN_THROAT), tmp_path / "data")

    interpretation = _interpretation(record)
    assert interpretation["reading"] == "full"
    assert interpretation["looks_cut"] == [] and interpretation["ambiguous"] == []
    assert "x0" not in record["symmetry"]["domain_planes"]


def test_a_port_through_the_plane_is_solved_as_shown(tmp_path: Path) -> None:
    """A real opening on x = 0 with the driver clear of it: not a cut, not asked."""

    record = _ingest(_bundle(tmp_path, "slot", _slot_on_plane, SLOT_DRIVER), tmp_path / "data")

    interpretation = _interpretation(record)
    assert interpretation["reading"] == "as-shown"
    assert interpretation["looks_cut"] == []
    assert interpretation["ambiguous"] == ["x0"]
    assert _multiplier(record) == 1.0
    _no_blocking_domain_finding(record)


def test_an_open_backed_shell_without_provenance_is_solved_as_shown(tmp_path: Path) -> None:
    record = _ingest(
        _bundle(tmp_path, "open-backed", _split_open_backed_shell, BOX_DRIVER), tmp_path / "data"
    )

    assert _interpretation(record)["reading"] == "as-shown"
    assert _multiplier(record) == 1.0
    _no_blocking_domain_finding(record)


def test_a_split_open_backed_shell_with_provenance_is_mirrored_and_change_unmirrors_it(
    tmp_path: Path,
) -> None:
    """The residual risk Magnus accepted: stated on the card, and Change is the remedy."""

    from server.cadlink.domain_interpretation import record_reading

    data_dir = tmp_path / "data"
    bundle = _bundle(
        tmp_path, "open-backed-split", _split_open_backed_shell, BOX_DRIVER,
        cut=[provenance("body-0", name="Split Body 7")],
    )
    mirrored = _ingest(bundle, data_dir)
    interpretation = _interpretation(mirrored)
    assert interpretation["reading"] == "reduced"
    assert interpretation["evidence"]["source"] == "cad-provenance"
    assert interpretation["evidence"]["features"][0]["name"] == "Split Body 7"
    assert "x0" in mirrored["symmetry"]["domain_planes"]
    assert {"reading": "as-shown"} in interpretation["choices"]

    record_reading(_store(data_dir), mirrored, {"reading": "as-shown"})
    shown = _ingest(Path(mirrored["bundle_store_path"]), data_dir)
    assert _interpretation(shown)["reading"] == "as-shown"
    assert _interpretation(shown)["evidence"]["source"] == "user"
    assert shown["symmetry"]["domain_planes"] == []
    assert _multiplier(shown) == 1.0
    assert shown["mesh_cache_key"] != mirrored["mesh_cache_key"]


def test_provenance_whose_rim_is_off_the_plane_is_refused(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "rim-off", _rim_off_plane, HORN_THROAT, cut=[provenance("body-0")])

    with pytest.raises(IngestRefusal, match="not on x = 0"):
        _ingest(bundle, tmp_path / "data")


def test_provenance_naming_the_negative_side_is_refused_with_its_remedy(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path, "negative", _negative_half, HORN_THROAT,
        cut=[provenance("body-0", kept_side="negative")],
    )

    with pytest.raises(IngestRefusal, match=r"keep the x ≥ 0 side"):
        _ingest(bundle, tmp_path / "data")


def test_a_capped_half_with_provenance_is_refused(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "capped", _capped_half, HORN_THROAT, cut=[provenance("body-0")])

    with pytest.raises(IngestRefusal, match="capped"):
        _ingest(bundle, tmp_path / "data")


def test_repeated_imports_without_evidence_stay_unmirrored(tmp_path: Path) -> None:
    """A previous 'solved as shown' never authorises mirroring."""

    from server.cadlink.domain_interpretation import lineage_reading

    data_dir = tmp_path / "data"
    first = _ingest(_bundle(tmp_path, "repeat-1", _open_half, HORN_THROAT), data_dir)
    again = _ingest(Path(first["bundle_store_path"]), data_dir)
    later = _ingest(_bundle(tmp_path, "repeat-2", _open_half, HORN_THROAT), data_dir)

    for record in (first, again, later):
        assert _interpretation(record)["reading"] == "as-shown"
        assert _multiplier(record) == 1.0
    assert lineage_reading(_store(data_dir), later) is None


def test_mirrored_distinct_sources_are_not_mirrored_onto_each_other(tmp_path: Path) -> None:
    """Acoustic compatibility: two drivers are two excitations, never one mirror."""

    data_dir = tmp_path / "data"
    pair = _ingest(
        _bundle(tmp_path, "pair", _mirrored_pair, PAIR_DRIVERS, sources=["left", "right"]),
        data_dir,
    )
    assert "x0" not in pair["symmetry"]["cut_planes"]
    assert _interpretation(pair)["reading"] == "full"
    # Positive control: one source owning both drivers mirrors about x = 0.
    shared = _ingest(_bundle(tmp_path, "pair-shared", _mirrored_pair, PAIR_DRIVERS), data_dir)
    assert "x0" in shared["symmetry"]["cut_planes"]


def test_an_old_add_in_manifest_keeps_its_meaning(tmp_path: Path) -> None:
    """Absent domain: a full model with WG's auto-cut, as before; a pre-cut is shown as is."""

    data_dir = tmp_path / "data"
    full = _ingest(_bundle(tmp_path, "old-full", _horn, HORN_THROAT, domain=None), data_dir)
    assert full["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert _interpretation(full)["manifest_domain"] == "absent"
    half = _ingest(_bundle(tmp_path, "old-half", _open_half, HORN_THROAT, domain=None), data_dir)
    assert _interpretation(half)["reading"] == "as-shown"
    assert _interpretation(half)["looks_cut"] == ["x0"]
    _no_blocking_domain_finding(half)


def test_a_new_add_in_manifest_of_a_full_model_is_cut_as_before(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "new-full", _horn, HORN_THROAT), tmp_path / "data")

    assert _interpretation(record)["manifest_domain"] == "automatic"
    assert _interpretation(record)["reading"] == "full"
    assert record["symmetry"]["cut_planes"] == ["x0", "y0"]
    # 'automatic' is not a declared domain: every axis stays available.
    assert len(record["normalisation"]["solver_frame"]["allowed_axes"]) == 6


def test_acceptance_evidence_backed_precut_equals_the_same_cut_declared_by_hand(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    automatic = _ingest(
        _bundle(tmp_path, "accept-auto", _open_half, HORN_THROAT, cut=[provenance("body-0")]), data_dir
    )
    declared = _ingest(_bundle(tmp_path, "accept-hand", _open_half, HORN_THROAT, domain=("x0",)), data_dir)

    assert automatic["mesh_content_sha256"] == declared["mesh_content_sha256"]
    assert automatic["symmetry"]["domain_planes"] == declared["symmetry"]["domain_planes"]
    assert automatic["mesh"]["stats"] == declared["mesh"]["stats"]
    assert automatic["polar_grid_derivation"] == declared["polar_grid_derivation"]
    assert automatic["source_tags"] == declared["source_tags"]
    assert automatic["post_cut_source_areas"] == declared["post_cut_source_areas"]
    assert _interpretation(declared)["evidence"]["source"] == "declaration"
