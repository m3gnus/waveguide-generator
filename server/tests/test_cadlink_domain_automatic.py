"""M1c-auto: the automatic domain, on real generated geometry.

PLAN.md, "M1c-auto. Automatic domain; the Model dropdown is removed" (as
narrowed on 2026-09-22). A model that is already cut is mirrored with
recorded evidence of the cut -- a declaration, an earlier explicit or
provenance-backed interpretation of the same lineage, or cut provenance the
add-in recorded -- and, since stage 3 branch 2, recovered from its geometry
alone when every flip condition holds (``server/cadlink/cut_recovery.py``;
a negative-side cut by reflecting its mesh). Any other open cut is refused at
solve, naming the condition it failed. Nothing here asks.

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


def _standalone_sheet(path: Path) -> None:
    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.addBox(-50.0, -50.0, -80.0, 100.0, 100.0, 70.0)
        gmsh.model.occ.addDisk(30.0, 0.0, 0.0, 10.0, 10.0)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(build)


def _edge_aligned_source_sheet(path: Path) -> None:
    # The rigid box is complete. Only the separate source sheet reaches x = 0.
    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.addBox(20.0, -50.0, -80.0, 60.0, 100.0, 70.0)
        gmsh.model.occ.addRectangle(0.0, -50.0, 10.0, 60.0, 100.0)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(build)


def _split_open_backed_shell(path: Path) -> None:
    # A box split on the YZ plane, +x side kept open where the split was: an
    # open-backed shell. The driver sits clear of the plane.
    _box(path, x=(0.0, 60.0), discs=[(30.0, 0.0, 10.0)], open_x0_face=True)


def _slot_on_plane(path: Path) -> None:
    # A closed box whose x = 0 wall has a port through it, driver on top.
    _box(path, x=(0.0, 80.0), discs=[(40.0, 0.0, 10.0)], slot_in_x0_face=True)


def _port_exit_on_plane(path: Path) -> None:
    # A closed box clear of x = 0 whose small square port duct runs out to the
    # plane and ends open there: an opening on the plane, not a cut.
    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        occ = gmsh.model.occ
        box = occ.addBox(20.0, -40.0, -80.0, 60.0, 80.0, 80.0)
        duct = occ.addBox(0.0, -5.0, -45.0, 20.0, 10.0, 10.0)
        fused, _ = occ.fuse([(3, box)], [(3, duct)])
        disc = occ.addDisk(50.0, 0.0, 0.0, 10.0, 10.0)
        occ.fragment(fused, [(2, disc)])
        occ.synchronize()
        occ.remove(gmsh.model.getEntities(3), recursive=False)
        occ.synchronize()
        exit_face = [
            (2, tag) for _dim, tag in gmsh.model.getEntities(2)
            if abs(gmsh.model.getBoundingBox(2, tag)[0]) < 1e-6
            and abs(gmsh.model.getBoundingBox(2, tag)[3]) < 1e-6
        ]
        assert len(exit_face) == 1, exit_face
        occ.remove(exit_face, recursive=False)
        occ.synchronize()
        occ.healShapes(sewFaces=True, makeSolids=False)
        occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(build)


def _surfaces_only() -> None:
    gmsh.model.occ.synchronize()
    gmsh.model.occ.remove(gmsh.model.getEntities(3), recursive=False)
    gmsh.model.occ.synchronize()


def _drop_faces(predicate: Callable[[tuple[float, ...], int], bool]) -> None:
    doomed = [
        (2, tag) for _dim, tag in gmsh.model.getEntities(2)
        if predicate(gmsh.model.getBoundingBox(2, tag), tag)
    ]
    assert doomed
    gmsh.model.occ.remove(doomed, recursive=False)
    gmsh.model.occ.synchronize()


def _open_x0_box_shell() -> None:
    """The open-backed box shell (x 0..60, open at x = 0, driver on top), not yet written."""

    occ = gmsh.model.occ
    box = occ.addBox(0.0, -40.0, -80.0, 60.0, 80.0, 80.0)
    occ.fragment([(3, box)], [(2, occ.addDisk(30.0, 0.0, 0.0, 10.0, 10.0))])
    _surfaces_only()
    _drop_faces(lambda box, _tag: abs(box[0]) < 1e-6 and abs(box[3]) < 1e-6)
    occ.healShapes(sewFaces=True, makeSolids=False)
    occ.synchronize()


def _open_half_with_block(path: Path, *, block_x: float) -> None:
    # The open-backed shell with a separate solid block inside it: at x = 0 the
    # block is cut too (a capped face on the plane); at x = -10 it is whole and
    # straddles the plane.
    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        _open_x0_box_shell()
        gmsh.model.occ.addBox(block_x, -10.0, -40.0, 20.0, 20.0, 20.0)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(build)


def _winged_open_half(path: Path) -> None:
    # The open-backed shell with a rigid side wing: the rim on x = 0 spans
    # about 70% of the shell's y extent.
    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        occ = gmsh.model.occ
        box = occ.addBox(0.0, -40.0, -80.0, 60.0, 80.0, 80.0)
        wing = occ.addBox(30.0, 40.0, -80.0, 30.0, 35.0, 80.0)
        fused, _ = occ.fuse([(3, box)], [(3, wing)])
        occ.fragment(fused, [(2, occ.addDisk(30.0, 0.0, 0.0, 10.0, 10.0))])
        _surfaces_only()
        _drop_faces(lambda box, _tag: abs(box[0]) < 1e-6 and abs(box[3]) < 1e-6)
        occ.healShapes(sewFaces=True, makeSolids=False)
        occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(build)


def _curved_source_sheet(path: Path) -> None:
    # A complete closed box plus a separate half-cylinder source sheet whose
    # two straight edges lie on x = 0 and span the box in y and z.
    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        occ = gmsh.model.occ
        occ.addBox(20.0, -50.0, -80.0, 60.0, 100.0, 70.0)
        cylinder = occ.addCylinder(0.0, -50.0, -45.0, 0.0, 100.0, 0.0, 35.0)
        occ.intersect([(3, cylinder)], [(3, occ.addBox(0.0, -60.0, -100.0, 100.0, 120.0, 100.0))])
        occ.synchronize()
        occ.remove(
            [dim_tag for dim_tag in gmsh.model.getEntities(3) if gmsh.model.getBoundingBox(*dim_tag)[0] < 1e-6],
            recursive=False,
        )
        occ.synchronize()
        _drop_faces(lambda box, tag: box[0] < 19.0 and str(gmsh.model.getType(2, tag)).casefold() == "plane")
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(build)


def _curved_face(surfaces: list[int]) -> list[int]:
    chosen = [tag for tag in surfaces if str(gmsh.model.getType(2, tag)).casefold() != "plane"]
    assert len(chosen) == 1
    return chosen


def _z_up_top_half(path: Path) -> None:
    # A Z-up speaker facing -Y (front at y = -80), its top half kept and left
    # open along z = 0, the driver on the front clear of the cut. Under the -y
    # solver frame CAD z = 0 is the solver's y0, a plane WG mirrors.
    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        occ = gmsh.model.occ
        box = occ.addBox(-40.0, -80.0, 0.0, 80.0, 80.0, 60.0)
        disc = occ.addDisk(0.0, -80.0, 30.0, 10.0, 10.0)
        occ.rotate([(2, disc)], 0.0, -80.0, 30.0, 1.0, 0.0, 0.0, math.pi / 2)
        occ.fragment([(3, box)], [(2, disc)])
        _surfaces_only()
        _drop_faces(lambda box, _tag: abs(box[2]) < 1e-6 and abs(box[5]) < 1e-6)
        occ.healShapes(sewFaces=True, makeSolids=False)
        occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(build)


def _open_mouth_horn_facing_minus_y(path: Path) -> None:
    # A single-sheet surface horn (inner wall and throat only, nothing cut)
    # radiating -Y, its open mouth on CAD y = 0: the solver's z0 under -y.
    _horn(path)

    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
        _surfaces_only()

        def keep(box: tuple[float, ...]) -> bool:
            inner = abs(box[2]) < 1e-3 and abs(box[5] - 60.0) < 1e-3
            throat = abs(box[2]) < 1e-6 and abs(box[5]) < 1e-6
            return inner or throat

        _drop_faces(lambda box, _tag: not keep(box))
        shapes = gmsh.model.getEntities(2)
        gmsh.model.occ.translate(shapes, 0.0, 0.0, -60.0)
        gmsh.model.occ.rotate(shapes, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, math.pi / 2)
        gmsh.model.occ.healShapes(sewFaces=True, makeSolids=False)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(build)


def _throat_farthest_along_y(surfaces: list[int]) -> list[int]:
    flat = [
        tag for tag in _plane_faces(surfaces)
        if abs(gmsh.model.getBoundingBox(2, tag)[4] - gmsh.model.getBoundingBox(2, tag)[1]) < 1e-6
    ]
    assert flat
    return [max(flat, key=lambda tag: abs(float(gmsh.model.occ.getCenterOfMass(2, tag)[1])))]


def _mirrored_pair(path: Path) -> None:
    # A whole solid box, two drivers mirrored about x = 0.
    _box(path, x=(-60.0, 60.0), discs=[(-25.0, 0.0, 10.0), (25.0, 0.0, 10.0)], solid=True)


HORN_THROAT = _horn_throat
BOX_DRIVER = _faces_near((30.0, 0.0, 0.0))
SLOT_DRIVER = _faces_near((40.0, 0.0, 0.0))
PORT_DRIVER = _faces_near((50.0, 0.0, 0.0))
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


def _legacy(record: dict[str, Any]) -> dict[str, Any]:
    """The record as an earlier build saved it: no domain observations, no decision."""

    return {
        key: value for key, value in record.items()
        if key not in {"domain_interpretation", "domain_decision"}
    }


_SOLVE_ENGINES = ("auto", "metal", "beat", "beat-cpu", "bempp")


def _solve_verdicts(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """The Solve card's plan and each engine's submission outcome for ``record``.

    An outcome is the resolved engine's name, or the refusal's reason code.
    """

    import asyncio
    from types import SimpleNamespace

    from server.jobs.runtime import plan_imported_submission, resolve_imported_submission
    from test_imported_jobs import _beat_cpu, _bempp, _metal, _request

    engines = (_metal(), _beat_cpu(), _bempp(sources=("parametric", "imported")))

    class Registry:
        async def capabilities(self) -> tuple[Any, ...]:
            return engines

        async def get_engine(self, name: str) -> Any:
            return SimpleNamespace(name=name)

        async def unavailable_reason(self, _name: str) -> str | None:
            return None

    registry = Registry()
    mesh = Path(record["mesh_store_path"]).read_text(encoding="utf-8")
    symmetry = {"resolved_quadrants": 1234}

    async def run() -> tuple[dict[str, Any], dict[str, str]]:
        request = _request(record["ingest_id"])
        request.options.engine = "auto"
        plan = await plan_imported_submission(
            request, registry, imported_record=record, imported_msh_text=mesh,
            symmetry_metadata=symmetry,
        )
        outcomes: dict[str, str] = {}
        for engine in _SOLVE_ENGINES:
            request.options.engine = engine
            try:
                resolved = await resolve_imported_submission(
                    request, registry, imported_record=record, imported_msh_text=mesh,
                    symmetry_metadata=symmetry,
                )
                outcomes[engine] = str(resolved.engine_name)
            except Exception as exc:  # noqa: BLE001 - the refusal's code is the verdict
                outcomes[engine] = str(getattr(exc, "reason_code", type(exc).__name__))
        return plan, outcomes

    return asyncio.run(run())


def _assert_refused_everywhere(record: dict[str, Any], message: str | None = None) -> None:
    plan, outcomes = _solve_verdicts(record)
    assert plan["engine"] is None and plan["code"] == "imported_open_half_shell", plan
    assert outcomes == {engine: "imported_open_half_shell" for engine in _SOLVE_ENGINES}
    if message is not None:
        assert plan["reason"] == f"imported_open_half_shell: {message}"


def _same_open_cut_refusal(decided: tuple[str, str] | None, judged: tuple[str, str] | None) -> None:
    """The decision's refusal and the saved-observation check's are the same refusal.

    Only the decision names the flip condition the cut failed; the check that
    reads observations alone (an earlier build's record) states the rest.
    """

    assert decided is not None and judged is not None
    assert decided[0] == judged[0] == "imported_open_half_shell"
    assert decided[1].startswith(judged[1].split(". Send")[0])


def _assert_solves_on_metal_and_beat(record: dict[str, Any]) -> None:
    plan, outcomes = _solve_verdicts(record)
    assert plan["code"] is None and plan["engine"] == "metal", plan
    assert outcomes["auto"] == outcomes["metal"] == "metal", outcomes
    assert outcomes["beat"] == outcomes["beat-cpu"] == "beat-cpu", outcomes


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


def test_the_same_open_half_without_provenance_is_recovered_from_its_geometry(tmp_path: Path) -> None:
    """Every flip condition holds: the cut half is solved as the whole speaker's reduced domain."""

    from server.jobs.runtime import _imported_open_half_refusal

    bundle = _bundle(tmp_path, "half-bare", _open_half, HORN_THROAT)
    record = _ingest(bundle, tmp_path / "data")

    interpretation = _interpretation(record)
    assert interpretation["reading"] == "reduced"
    assert interpretation["planes"] == ["x0"]
    assert interpretation["evidence"]["source"] is None
    assert interpretation["evidence"]["recovered"] is True
    assert interpretation["cut_recovery"]["recoverable"] is True
    assert interpretation["reflected_planes"] == []
    assert record["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert record["symmetry"]["planes"]["x0"]["source"] == "recovered-from-geometry"
    assert record["symmetry_verification"]["verified"] is True
    assert record["reflection"] is None
    assert _multiplier(record) == 4.0
    # Solved as shown it would be half a speaker in free space: no Change.
    assert interpretation["choices"] == []
    # A reduced domain is solved as it was modelled (+z) until M1d.
    assert record["normalisation"]["solver_frame"]["allowed_axes"] == ["+z"]
    _no_blocking_domain_finding(record)
    assert _imported_open_half_refusal(record) is None
    _assert_solves_on_metal_and_beat(record)


def test_an_open_half_saved_without_observations_is_refused_from_its_mesh(tmp_path: Path) -> None:
    """A record from an earlier build has no observations; its verified mesh is observed.

    The half's source is identified as the left one, so it is not recovered.
    """

    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(
        _bundle(tmp_path, "half-legacy", _open_half, HORN_THROAT, sources=["woofer-left"]),
        tmp_path / "data",
    )
    rim = _interpretation(record)["observations"]["planes"]["x0"]["rim_edges"]
    decided = _imported_open_half_refusal(record)
    assert decided is not None and "identified as the left one" in decided[1]
    legacy = _legacy(record)
    refusal = _imported_open_half_refusal(legacy)
    assert refusal is not None and refusal[0] == "imported_open_half_shell"
    assert f"x = 0 ({rim} rim edges)" in refusal[1]
    _same_open_cut_refusal(decided, refusal)
    _assert_refused_everywhere(legacy, refusal[1])
    # Observations saved before the rigid-shell rim existed are observed again too.
    # (Those builds wrote no domain decision either.)
    earlier = json.loads(json.dumps(record))
    earlier.pop("domain_decision")
    for plane in earlier["domain_interpretation"]["observations"]["planes"].values():
        plane.pop("rigid_cut_rim_edges")
    assert _imported_open_half_refusal(earlier) == refusal
    unnamed = json.loads(json.dumps(record))
    unnamed.pop("domain_decision")
    for plane in unnamed["domain_interpretation"]["observations"]["planes"].values():
        plane.pop("solver_plane")
    assert _imported_open_half_refusal(unnamed) == refusal


def test_undeclared_negative_side_half_is_recovered_by_reflection(tmp_path: Path) -> None:
    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(_bundle(tmp_path, "negative-bare", _negative_half, HORN_THROAT), tmp_path / "data")
    observed = _interpretation(record)["observations"]["planes"]["x0"]
    assert observed["negative_vertices"] > 0
    assert observed["positive_vertices"] == 0
    assert record["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert record["symmetry"]["reflected_planes"] == ["x0"]
    assert record["reflection"]["axes"] == ["x"]
    assert _imported_open_half_refusal(record) is None


def test_change_to_half_is_checked_mirrored_and_remembered_for_the_lineage(tmp_path: Path) -> None:
    """A cut WG does not recover by itself (no driver meets it) is mirrored on the user's Change."""

    from server.cadlink.domain_interpretation import interpretation_view, record_reading

    data_dir = tmp_path / "data"
    first = _ingest(_bundle(tmp_path, "half-change", _split_open_backed_shell, BOX_DRIVER), data_dir)
    assert _interpretation(first)["reading"] == "as-shown"

    record_reading(_store(data_dir), first, {"reading": "reduced", "planes": ["x0"]})
    changed = _ingest(Path(first["bundle_store_path"]), data_dir)
    assert _interpretation(changed)["reading"] == "reduced"
    assert _interpretation(changed)["evidence"]["source"] == "user"
    assert changed["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert changed["mesh_cache_key"] != first["mesh_cache_key"]

    # A new version of the same document: the lineage's reading is reused
    # because it still revalidates on the new geometry.
    later = _ingest(_bundle(tmp_path, "half-change-v2", _split_open_backed_shell, BOX_DRIVER), data_dir)
    assert _interpretation(later)["reading"] == "reduced"
    assert _interpretation(later)["evidence"]["source"] == "user-lineage"
    assert later["symmetry"]["domain_planes"] == ["x0", "y0"]

    # ... and a later full model of that lineage is simply a full model.
    full = _ingest(_bundle(tmp_path, "half-change-v3", _horn, HORN_THROAT), data_dir)
    assert _interpretation(full)["reading"] == "full"
    assert _interpretation(full)["evidence"]["applied"] is False
    assert full["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert _interpretation(full)["choices"] == []
    assert interpretation_view(_store(data_dir), full)["pending"] is None


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


def test_open_quarter_without_provenance_is_recovered_as_a_quarter(tmp_path: Path) -> None:
    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(_bundle(tmp_path, "quarter-bare", _open_quarter, HORN_THROAT), tmp_path / "data")

    assert _interpretation(record)["reading"] == "reduced"
    assert _interpretation(record)["planes"] == ["x0", "y0"]
    assert record["symmetry"]["cut_planes"] == []
    assert _multiplier(record) == 4.0
    _no_blocking_domain_finding(record)
    assert _imported_open_half_refusal(record) is None


def test_an_off_centre_complete_model_is_a_full_model(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "off-centre", _off_centre, HORN_THROAT), tmp_path / "data")

    interpretation = _interpretation(record)
    assert interpretation["reading"] == "full"
    assert interpretation["looks_cut"] == [] and interpretation["ambiguous"] == []
    assert "x0" not in record["symmetry"]["domain_planes"]


def test_standalone_source_sheet_without_cut_shaped_rim_remains_eligible(tmp_path: Path) -> None:
    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(_bundle(tmp_path, "source-sheet", _standalone_sheet, HORN_THROAT), tmp_path / "data")
    assert record["symmetry"]["domain_planes"] == []
    assert _imported_open_half_refusal(record) is None
    assert _imported_open_half_refusal(_legacy(record)) is None
    _assert_solves_on_metal_and_beat(record)


def test_edge_aligned_standalone_source_sheet_is_not_a_shell_cut(tmp_path: Path) -> None:
    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(
        _bundle(tmp_path, "edge-sheet", _edge_aligned_source_sheet, HORN_THROAT),
        tmp_path / "data",
    )
    observed = _interpretation(record)["observations"]["planes"]["x0"]
    assert observed["rim_edges"] == 12
    assert observed["sources_bisected"]
    assert observed["rigid_cut_rim_edges"] == 0
    assert _imported_open_half_refusal(record) is None
    _assert_solves_on_metal_and_beat(record)
    # The same return saved by an earlier build is judged from its mesh alike.
    assert _imported_open_half_refusal(_legacy(record)) is None
    _assert_solves_on_metal_and_beat(_legacy(record))


def test_a_port_through_the_plane_is_solved_as_shown(tmp_path: Path) -> None:
    """A real opening on x = 0 with the driver clear of it: not a cut, not asked."""

    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(_bundle(tmp_path, "slot", _slot_on_plane, SLOT_DRIVER), tmp_path / "data")

    interpretation = _interpretation(record)
    assert interpretation["reading"] == "as-shown"
    assert interpretation["looks_cut"] == []
    assert interpretation["ambiguous"] == ["x0"]
    assert _multiplier(record) == 1.0
    _no_blocking_domain_finding(record)
    assert _imported_open_half_refusal(record) is None
    assert _imported_open_half_refusal(_legacy(record)) is None
    _assert_solves_on_metal_and_beat(record)


def test_a_port_ending_open_on_the_plane_is_not_a_cut(tmp_path: Path) -> None:
    """An uncapped opening on x = 0 that does not span the model stays solvable."""

    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(_bundle(tmp_path, "port-exit", _port_exit_on_plane, PORT_DRIVER), tmp_path / "data")
    observed = _interpretation(record)["observations"]["planes"]["x0"]
    assert observed["rim_edges"] >= 3
    assert observed["cap_triangles"] == 0
    assert observed["negative_vertices"] == 0 and observed["positive_vertices"] > 0
    assert observed["rigid_cut_rim_edges"] == 0
    assert _imported_open_half_refusal(record) is None
    assert _imported_open_half_refusal(_legacy(record)) is None
    _assert_solves_on_metal_and_beat(record)


def test_a_cut_on_the_solver_y0_plane_is_refused_whatever_its_cad_name(tmp_path: Path) -> None:
    """Z-up, facing -Y: the top half kept, open along CAD z = 0, driver clear of it."""

    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(
        _bundle(tmp_path, "z-up-top-half", _z_up_top_half, _faces_near((0.0, -80.0, 30.0))),
        tmp_path / "data", solver_frame="-y",
    )
    observed = _interpretation(record)["observations"]["planes"]["z0"]
    assert observed["solver_plane"] == "y0"
    assert observed["rigid_cut_rim_edges"] >= 3 and observed["sources_bisected"] == []
    refusal = _imported_open_half_refusal(record)
    assert refusal is not None and "along z = 0" in refusal[1]
    # The failed flip condition, named: WG mirrors a cut only as modelled.
    assert "only in the frame it was modelled in" in refusal[1]
    _assert_refused_everywhere(record, refusal[1])
    _same_open_cut_refusal(refusal, _imported_open_half_refusal(_legacy(record)))


def test_a_horn_mouth_on_the_solver_z0_plane_stays_solvable_under_any_frame(tmp_path: Path) -> None:
    """A single-sheet horn facing -Y: its open mouth lies on CAD y = 0, the solver's z0."""

    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(
        _bundle(tmp_path, "horn-minus-y", _open_mouth_horn_facing_minus_y, _throat_farthest_along_y),
        tmp_path / "data", solver_frame="-y",
    )
    observed = _interpretation(record)["observations"]["planes"]["y0"]
    assert observed["solver_plane"] == "z0"
    assert observed["rigid_cut_rim_edges"] >= 3 and observed["sources_bisected"] == []
    assert _imported_open_half_refusal(record) is None
    assert _imported_open_half_refusal(_legacy(record)) is None
    plan, outcomes = _solve_verdicts(record)
    assert plan["code"] != "imported_open_half_shell", plan
    assert "imported_open_half_shell" not in outcomes.values(), outcomes


@pytest.mark.parametrize("block_x", [0.0, -10.0], ids=["cut-capped-block", "whole-straddling-block"])
def test_an_open_half_with_another_body_on_the_plane_is_still_refused(
    tmp_path: Path, block_x: float
) -> None:
    """A capped or straddling separate body does not make the cut shell whole."""

    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(
        _bundle(tmp_path, "half-with-block", lambda path: _open_half_with_block(path, block_x=block_x), BOX_DRIVER),
        tmp_path / "data",
    )
    observed = _interpretation(record)["observations"]["planes"]["x0"]
    if block_x == 0.0:
        assert observed["cap_triangles"] > 0
    else:
        assert observed["negative_vertices"] > 0 and observed["positive_vertices"] > 0
    assert observed["rigid_cut_rim_edges"] == observed["rim_edges"] >= 3
    refusal = _imported_open_half_refusal(record)
    assert refusal is not None and refusal[0] == "imported_open_half_shell"
    # Each failed flip condition is named.
    assert (
        "would solve as a wall across the cut" if block_x == 0.0 else "geometry crosses x = 0"
    ) in refusal[1]
    _assert_refused_everywhere(record, refusal[1])
    _same_open_cut_refusal(refusal, _imported_open_half_refusal(_legacy(record)))


def test_a_cut_through_a_winged_shell_is_refused(tmp_path: Path) -> None:
    """A side wing leaves the cut section at about 70% of the shell's width."""

    from server.cadlink.domain_interpretation import SPANNING_RIM_FRACTION
    from server.jobs.runtime import _imported_open_half_refusal

    # The x=0 rim is 80 mm wide; the attached shell plus wing is 115 mm.
    # This would be missed by the former three-quarter threshold.
    assert 0.65 < 80.0 / 115.0 < 0.75
    assert SPANNING_RIM_FRACTION <= 80.0 / 115.0
    record = _ingest(_bundle(tmp_path, "winged-half", _winged_open_half, BOX_DRIVER), tmp_path / "data")
    observed = _interpretation(record)["observations"]["planes"]["x0"]
    assert observed["rigid_cut_rim_edges"] == observed["rim_edges"] >= 3
    refusal = _imported_open_half_refusal(record)
    assert refusal is not None and refusal[0] == "imported_open_half_shell"
    assert "no source meets x = 0" in refusal[1]
    _assert_refused_everywhere(record, refusal[1])


def test_a_curved_source_sheet_edged_on_the_plane_is_not_a_shell_cut(tmp_path: Path) -> None:
    """Its straight edges span the box on x = 0; only rigid edges can be a cut."""

    from server.cadlink.domain_interpretation import observe_record_mesh
    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(_bundle(tmp_path, "curved-sheet", _curved_source_sheet, _curved_face), tmp_path / "data")
    observed = _interpretation(record)["observations"]["planes"]["x0"]
    assert observed["rim_edges"] >= 3
    assert observed["rigid_cut_rim_edges"] == 0
    assert _imported_open_half_refusal(record) is None
    _assert_solves_on_metal_and_beat(record)
    # Were the sheet's edges counted as rigid, the same mesh would read as a cut.
    mesh = Path(record["mesh_store_path"]).read_text(encoding="utf-8")
    untagged = observe_record_mesh({**record, "source_tags": {}}, mesh)
    assert untagged is not None
    assert untagged.planes["x0"].rigid_cut_rim_edges == observed["rim_edges"]


def test_an_open_backed_shell_without_provenance_is_refused_at_solve(tmp_path: Path) -> None:
    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(
        _bundle(tmp_path, "open-backed", _split_open_backed_shell, BOX_DRIVER), tmp_path / "data"
    )

    assert _interpretation(record)["reading"] == "as-shown"
    assert _multiplier(record) == 1.0
    _no_blocking_domain_finding(record)
    observed = _interpretation(record)["observations"]["planes"]["x0"]
    assert observed["rim_edges"] == 15
    assert observed["sources_bisected"] == []
    assert observed["rigid_cut_rim_edges"] == 15
    message = (
        "The model is open along x = 0 (15 rim edges), so WG would solve half a "
        "speaker in free space, and it cannot mirror it as the whole speaker's "
        "reduced domain: no source meets x = 0, so nothing shows it is the "
        "speaker's symmetry plane rather than an open side. Send the uncut "
        "model — WG finds the symmetry and reduces it automatically."
    )
    assert _imported_open_half_refusal(record) == ("imported_open_half_shell", message)
    # The driver is clear of the cut, yet AUTO, Metal, BEAT and BEMPP all refuse.
    _assert_refused_everywhere(record, message)
    legacy = _imported_open_half_refusal(_legacy(record))
    _same_open_cut_refusal((("imported_open_half_shell", message)), legacy)
    assert legacy is not None
    _assert_refused_everywhere(_legacy(record), legacy[1])


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
    # Shown unmirrored, the shell is still open across x = 0: a free-space
    # solve of it is refused like any other undeclared cut.
    from server.jobs.runtime import _imported_open_half_refusal

    refusal = _imported_open_half_refusal(shown)
    assert refusal is not None and refusal[0] == "imported_open_half_shell"


def test_provenance_whose_rim_is_off_the_plane_is_refused(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "rim-off", _rim_off_plane, HORN_THROAT, cut=[provenance("body-0")])

    with pytest.raises(IngestRefusal, match="not on x = 0"):
        _ingest(bundle, tmp_path / "data")


def test_provenance_naming_the_negative_side_is_mirrored_by_reflection(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path, "negative", _negative_half, HORN_THROAT,
        cut=[provenance("body-0", kept_side="negative")],
    )

    record = _ingest(bundle, tmp_path / "data")
    interpretation = _interpretation(record)
    assert interpretation["reading"] == "reduced"
    assert interpretation["evidence"]["source"] == "cad-provenance"
    assert interpretation["evidence"]["recovered"] is False
    assert interpretation["reflected_planes"] == ["x0"]
    assert record["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert record["symmetry"]["planes"]["x0"]["source"] == "interpreted-from-evidence"
    assert record["reflection"]["axes"] == ["x"]
    assert record["symmetry_verification"]["verified"] is True


def test_provenance_whose_kept_side_the_geometry_contradicts_is_refused(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path, "negative-claims-positive", _negative_half, HORN_THROAT,
        cut=[provenance("body-0", kept_side="positive")],
    )

    with pytest.raises(IngestRefusal, match=r"recorded as keeping x ≥ 0, but the model lies on x ≤ 0"):
        _ingest(bundle, tmp_path / "data")


def test_negative_side_history_is_set_aside_when_the_model_is_whole_again(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path,
        "negative-history-whole-model",
        _horn,
        HORN_THROAT,
        cut=[provenance("body-0", kept_side="negative")],
    )

    record = _ingest(bundle, tmp_path / "data")

    assert _interpretation(record)["reading"] == "full"
    assert _interpretation(record)["evidence"]["applied"] is False
    assert "x0" in _interpretation(record)["evidence"]["not_applicable"]


def test_a_capped_half_with_provenance_is_refused(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "capped", _capped_half, HORN_THROAT, cut=[provenance("body-0")])

    with pytest.raises(IngestRefusal, match="capped"):
        _ingest(bundle, tmp_path / "data")


def test_repeated_imports_without_evidence_stay_unmirrored(tmp_path: Path) -> None:
    """A previous 'solved as shown' never authorises mirroring."""

    from server.cadlink.domain_interpretation import lineage_reading

    data_dir = tmp_path / "data"
    # A cut WG cannot recover from its geometry (no driver meets it).
    first = _ingest(_bundle(tmp_path, "repeat-1", _split_open_backed_shell, BOX_DRIVER), data_dir)
    again = _ingest(Path(first["bundle_store_path"]), data_dir)
    later = _ingest(_bundle(tmp_path, "repeat-2", _split_open_backed_shell, BOX_DRIVER), data_dir)

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
    """Absent domain still prepares both shapes; an open pre-cut is recovered or refused alike."""

    from server.jobs.runtime import _imported_open_half_refusal

    data_dir = tmp_path / "data"
    full = _ingest(_bundle(tmp_path, "old-full", _horn, HORN_THROAT, domain=None), data_dir)
    assert full["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert _interpretation(full)["manifest_domain"] == "absent"
    half = _ingest(_bundle(tmp_path, "old-half", _open_half, HORN_THROAT, domain=None), data_dir)
    assert _interpretation(half)["reading"] == "reduced"
    assert _interpretation(half)["evidence"]["recovered"] is True
    _no_blocking_domain_finding(half)
    assert _imported_open_half_refusal(half) is None
    box = _ingest(_bundle(tmp_path, "old-box", _split_open_backed_shell, BOX_DRIVER, domain=None), data_dir)
    assert _interpretation(box)["reading"] == "as-shown"
    assert _imported_open_half_refusal(box) is not None


def test_a_new_add_in_manifest_of_a_full_model_is_cut_as_before(tmp_path: Path) -> None:
    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(_bundle(tmp_path, "new-full", _horn, HORN_THROAT), tmp_path / "data")

    assert _interpretation(record)["manifest_domain"] == "automatic"
    assert _interpretation(record)["reading"] == "full"
    assert record["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert _imported_open_half_refusal(record) is None
    # 'automatic' is not a declared domain: every axis stays available.
    assert len(record["normalisation"]["solver_frame"]["allowed_axes"]) == 6


def test_acceptance_evidence_backed_precut_equals_the_same_cut_declared_by_hand(tmp_path: Path) -> None:
    from server.jobs.runtime import _imported_open_half_refusal

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
    assert _imported_open_half_refusal(declared) is None


def test_an_explicit_reading_enters_the_mesh_cache_key_even_when_the_mesh_is_the_same(
    tmp_path: Path,
) -> None:
    """The interpretation is part of what was prepared, not only of what was meshed."""

    from server.cadlink.domain_interpretation import record_reading

    data_dir = tmp_path / "data"
    default = _ingest(_bundle(tmp_path, "cache-key", _split_open_backed_shell, BOX_DRIVER), data_dir)
    record_reading(_store(data_dir), default, {"reading": "as-shown"})
    chosen = _ingest(Path(default["bundle_store_path"]), data_dir)

    assert _interpretation(chosen)["evidence"]["source"] == "user"
    assert _interpretation(chosen)["reading"] == "as-shown"
    assert chosen["mesh_cache_key"] != default["mesh_cache_key"]
    # The same model meshed the same way: only the reading differs.
    assert chosen["mesh_content_sha256"] == default["mesh_content_sha256"]
