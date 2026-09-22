"""Generated loudspeaker geometry for the automatic solver frame (M1e).

Every fixture is built in OCC through gmsh in a canonical pose -- front along
+z, the front baffle at z = 0, the body behind it -- meshed, wound outward
into the air (each face is checked against the solid with ``isInside``), and
its source faces tagged by where they are and which way they face. Poses are
then applied to the arrays, so the same triangles are surveyed in every pose.

None of this is private CAD: the PartyMEH look-alike only borrows the rough
proportions recorded in the M1 field notes (a box with a horn on the front
baffle, an HF throat deep in the horn, two MF taps on its side walls facing
each other and slightly forward, an LF cone on the baffle).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
from typing import Any

import numpy as np

from server.cadlink.frame_infer import SurveyMesh, SurveySource

#: (role, source_id, predicate(face) -> bool)
SourceRule = tuple[str, str, Callable[["_Face"], bool]]


@dataclass(frozen=True)
class _Face:
    """A meshed OCC face: its oriented mean normal and triangle centroids."""

    normal: np.ndarray
    normals: np.ndarray
    center: np.ndarray
    centroids: np.ndarray
    edge: float


@dataclass(frozen=True)
class FixtureMesh:
    points_mm: np.ndarray
    triangles: np.ndarray
    tags: np.ndarray
    sources: tuple[SurveySource, ...]

    def survey(self, rotation: np.ndarray | None = None, **changes: Any) -> SurveyMesh:
        points = self.points_mm if rotation is None else self.points_mm @ np.asarray(rotation).T
        return SurveyMesh(
            points_mm=points,
            triangles=changes.get("triangles", self.triangles),
            tags=self.tags,
            sources=changes.get("sources", self.sources),
            identity_problem=changes.get("identity_problem"),
        )


# -- poses ---------------------------------------------------------------------


def _rotation(axis: tuple[float, float, float], degrees: float) -> np.ndarray:
    x, y, z = axis
    angle = math.radians(degrees)
    c, s = math.cos(angle), math.sin(angle)
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]], dtype=float)
    return np.eye(3) + s * k + (1 - c) * (k @ k)


#: Proper rotations taking the canonical front (+z) to each assembly axis.
POSES: dict[str, np.ndarray] = {
    "+z": np.eye(3),
    "-z": _rotation((1, 0, 0), 180),
    "+x": _rotation((0, 1, 0), 90),
    "-x": _rotation((0, 1, 0), -90),
    "+y": _rotation((1, 0, 0), -90),
    "-y": _rotation((1, 0, 0), 90),
}
for _axis, _matrix in POSES.items():
    _matrix[np.abs(_matrix) < 1e-12] = 0.0
#: The same poses as OCC turns (axis, degrees), for placing a STEP.
POSE_TURNS: dict[str, tuple[tuple[float, float, float], float] | None] = {
    "+z": None,
    "-z": ((1, 0, 0), 180),
    "+x": ((0, 1, 0), 90),
    "-x": ((0, 1, 0), -90),
    "+y": ((1, 0, 0), -90),
    "-y": ((1, 0, 0), 90),
}


# -- rules -----------------------------------------------------------------------


def face(center: Sequence[float], normal: Sequence[float], *, within: float, degrees: float = 3.0):
    """A planar face that faces ``normal`` and reaches ``center``.

    Judged on the face's triangles rather than its centroid, so a source face
    that a symmetry cut halved -- whose centroid moves off the plane -- is
    still found: ``center`` lies on its cut edge.
    """

    target = np.asarray(center, dtype=float)
    direction = np.asarray(normal, dtype=float)
    direction = direction / np.linalg.norm(direction)
    limit = math.cos(math.radians(degrees))

    def matches(candidate: _Face) -> bool:
        # Every triangle, not the mean: a whole cone's mean normal is its axis.
        if float((candidate.normals @ direction).min()) < limit:
            return False
        if float(np.linalg.norm(candidate.center - target)) <= within:
            return True
        offset = candidate.centroids - target
        in_plane = np.abs(offset @ candidate.normal) <= 0.5
        near = np.linalg.norm(offset, axis=1) <= within + candidate.edge
        return bool(np.any(in_plane & near))

    matches.center = target  # type: ignore[attr-defined]
    matches.within = within  # type: ignore[attr-defined]
    return matches


# -- building --------------------------------------------------------------------


def _occ() -> Any:
    import gmsh

    return gmsh.model.occ


def _box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> tuple[int, int]:
    return 3, _occ().addBox(x0, y0, z0, x1 - x0, y1 - y0, z1 - z0)


def _cylinder(base: Sequence[float], axis: Sequence[float], radius: float) -> tuple[int, int]:
    return 3, _occ().addCylinder(*base, *axis, radius)


def _cone(base: Sequence[float], axis: Sequence[float], r1: float, r2: float) -> tuple[int, int]:
    return 3, _occ().addCone(*base, *axis, r1, r2)


def _cut(body: list[tuple[int, int]], tools: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out, _ = _occ().cut(body, tools)
    return out


def _fuse(body: list[tuple[int, int]], tools: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out, _ = _occ().fuse(body, tools)
    return out


def _recess(center: Sequence[float], inward: Sequence[float], radius: float, depth: float) -> tuple[int, int]:
    """A cylinder that cuts a flat-bottomed recess ``depth`` into a wall.

    ``inward`` points from the air into the material; the recess bottom then
    faces ``-inward``, out into the air.
    """

    direction = np.asarray(inward, dtype=float)
    direction /= np.linalg.norm(direction)
    base = np.asarray(center, dtype=float) - 20.0 * direction
    return _cylinder(base, (20.0 + depth) * direction, radius)


def _wall_recess_point(axis_xy: Sequence[float], throat_z: float, throat_r: float, mouth_z: float, mouth_r: float,
                       z: float, side: float) -> tuple[np.ndarray, np.ndarray]:
    """A point on a conical horn wall at height ``z`` on the ``side`` (+1/-1) of x, and the wall's inward normal."""

    slope = (mouth_r - throat_r) / (mouth_z - throat_z)
    radius = throat_r + slope * (z - throat_z)
    point = np.array((axis_xy[0] + side * radius, axis_xy[1], z))
    # Normal of the cavity wall pointing into the air (towards the axis, forward).
    air = np.array((-side * 1.0, 0.0, slope))
    air /= np.linalg.norm(air)
    return point, -air


# -- the fixtures ----------------------------------------------------------------


def horn_box(scale: float = 1.0, *, mf_taps: bool = False, rear_lf: bool = False) -> list[SourceRule]:
    """A box with a conical horn cavity open at the front; HF at the throat."""

    s = scale
    body = [_box(-60 * s, -45 * s, -80 * s, 60 * s, 45 * s, 0)]
    body = _cut(body, [_cone((0, 0, -70 * s), (0, 0, 75 * s), 7 * s, 36 * s)])
    rules: list[SourceRule] = [("HF", "hf", face((0, 0, -70 * s), (0, 0, 1), within=2 * s))]
    if mf_taps:
        tools = []
        # One MF source with two faces, as WGLink writes a pair of taps.
        for side, name in ((1.0, "mf"), (-1.0, "mf")):
            point, inward = _wall_recess_point((0, 0), -70 * s, 7 * s, 5 * s, 36 * s, -35 * s, side)
            tools.append(_recess(point, inward, 6 * s, 3 * s))
            bottom = point + 3 * s * inward
            rules.append(("MF", name, face(bottom, -inward, within=2 * s)))
        body = _cut(body, tools)
    if rear_lf:
        body = _cut(body, [_recess((0, 0, -80 * s), (0, 0, 1), 16 * s, 3 * s)])
        rules.append(("LF", "lf", face((0, 0, -77 * s), (0, 0, -1), within=2 * s)))
    return rules


def party_meh_like(scale: float = 1.0) -> list[SourceRule]:
    """A PartyMEH look-alike: box, horn on the baffle, MF taps facing each other, LF cone."""

    s = scale
    body = [_box(-250 * s, -632 * s, -400 * s, 250 * s, 168 * s, 0)]
    horn = (0.0, -30.0 * s)
    throat_z, throat_r, mouth_z, mouth_r = -221 * s, 15 * s, 5 * s, 185 * s
    tools = [_cone((horn[0], horn[1], throat_z), (0, 0, mouth_z - throat_z), throat_r, mouth_r)]
    rules: list[SourceRule] = [("HF", "hf", face((horn[0], horn[1], throat_z), (0, 0, 1), within=3 * s))]
    body = _cut(body, tools)
    taps = []
    # One MF source with two faces, as WGLink writes a pair of taps.
    for side, name in ((1.0, "mf"), (-1.0, "mf")):
        point, inward = _wall_recess_point(horn, throat_z, throat_r, mouth_z, mouth_r, -120 * s, side)
        taps.append(_recess(point, inward, 30 * s, 8 * s))
        rules.append(("MF", name, face(point + 8 * s * inward, -inward, within=4 * s)))
    body = _cut(body, taps)
    body = _cut(body, [_cone((0, -450 * s, -60 * s), (0, 0, 65 * s), 25 * s, 95 * s)])
    rules.append(("LF", "lf", face((0, -450 * s, -60 * s), (0, 0, 1), within=3 * s)))
    return rules


def side_firing_taps() -> list[SourceRule]:
    """HF horn on the front, MF drivers on both side panels."""

    body = [_box(-100, -75, -150, 100, 75, 0)]
    body = _cut(body, [_cone((0, 0, -60), (0, 0, 65), 8, 45)])
    body = _cut(body, [_recess((100, 0, -75), (-1, 0, 0), 40, 4), _recess((-100, 0, -75), (1, 0, 0), 40, 4)])
    return [
        ("HF", "hf", face((0, 0, -60), (0, 0, 1), within=2)),
        ("MF", "mf-right", face((96, 0, -75), (1, 0, 0), within=3)),
        ("MF", "mf-left", face((-96, 0, -75), (-1, 0, 0), within=3)),
    ]


def two_way_box() -> list[SourceRule]:
    body = [_box(-70, -45, -80, 70, 45, 0)]
    body = _cut(body, [_cone((24, 0, -70), (0, 0, 75), 6, 27), _cylinder((-30, 0, -12), (0, 0, 17), 18)])
    return [
        ("HF", "hf", face((24, 0, -70), (0, 0, 1), within=2)),
        ("LF", "lf", face((-30, 0, -12), (0, 0, 1), within=2)),
    ]


def ported_box() -> list[SourceRule]:
    """LF and HF on the front baffle; a reflex port through the back panel."""

    body = [_box(-100, -150, -250, 100, 150, 0)]
    body = _cut(body, [
        _recess((0, -50, 0), (0, 0, -1), 70, 10),
        _recess((0, 100, 0), (0, 0, -1), 15, 3),
        _cylinder((0, 80, -260), (0, 0, 160), 25),
    ])
    return [
        ("LF", "lf", face((0, -50, -10), (0, 0, 1), within=3)),
        ("HF", "hf", face((0, 100, -3), (0, 0, 1), within=2)),
    ]


def coaxial() -> list[SourceRule]:
    """An LF cone recess with the HF on a boss at its centre."""

    body = [_box(-100, -100, -150, 100, 100, 0)]
    body = _cut(body, [_recess((0, 0, 0), (0, 0, -1), 80, 20)])
    body = _fuse(body, [_cylinder((0, 0, -21), (0, 0, 11), 15)])
    return [
        ("LF", "lf", face((0, 0, -20), (0, 0, 1), within=3)),
        ("HF", "hf", face((0, 0, -10), (0, 0, 1), within=2)),
    ]


def folded_horn() -> list[SourceRule]:
    """An HF source at the closed end of a folded channel: no line of sight out."""

    body = [_box(-100, -100, -200, 100, 100, 0)]
    body = _cut(body, [
        _box(20, -40, -170, 80, 40, 5),       # mouth leg, open at the front
        _box(-80, -40, -170, 80, 40, -150),   # the fold
        _box(-80, -40, -170, -20, 40, -40),   # the closed leg the source sits in
    ])
    return [("HF", "hf", face((-50, 0, -40), (0, 0, -1), within=3))]


def dipole_baffle() -> list[SourceRule]:
    """An open baffle radiating equally from both faces."""

    body = [_box(-150, -200, -20, 150, 200, 0)]
    body = _cut(body, [_recess((0, 0, 0), (0, 0, -1), 60, 3), _recess((0, 0, -20), (0, 0, 1), 60, 3)])
    return [
        ("MF", "mf-front", face((0, 0, -3), (0, 0, 1), within=2)),
        ("MF", "mf-back", face((0, 0, -17), (0, 0, -1), within=2)),
    ]


def cardioid() -> list[SourceRule]:
    """Front HF and MF; passive cardioid vents on the sides and the back."""

    body = [_box(-100, -100, -200, 100, 100, 0)]
    body = _cut(body, [
        _cone((0, 40, -50), (0, 0, 55), 8, 40),
        _recess((0, -50, 0), (0, 0, -1), 40, 5),
        _recess((100, 0, -120), (-1, 0, 0), 50, 5),
        _recess((-100, 0, -120), (1, 0, 0), 50, 5),
        _recess((0, 0, -200), (0, 0, 1), 60, 5),
    ])
    return [
        ("HF", "hf", face((0, 40, -50), (0, 0, 1), within=2)),
        ("MF", "mf", face((0, -50, -5), (0, 0, 1), within=2)),
        ("PASSIVE_CARDIOID", "vent-right", face((95, 0, -120), (1, 0, 0), within=3)),
        ("PASSIVE_CARDIOID", "vent-left", face((-95, 0, -120), (-1, 0, 0), within=3)),
        ("PASSIVE_CARDIOID", "vent-back", face((0, 0, -195), (0, 0, -1), within=3)),
    ]


def equal_area_front_back() -> list[SourceRule]:
    """Two sources of exactly equal area, one on the front, one on the back."""

    body = [_box(-80, -80, -120, 80, 80, 0)]
    body = _cut(body, [_recess((0, 0, 0), (0, 0, -1), 30, 4), _recess((0, 0, -120), (0, 0, 1), 30, 4)])
    return [
        ("HF", "front", face((0, 0, -4), (0, 0, 1), within=2)),
        ("LF", "back", face((0, 0, -116), (0, 0, -1), within=2)),
    ]


def mf_only_opposing_taps() -> list[SourceRule]:
    """A bore open at the front with two MF taps facing each other exactly."""

    body = [_box(-100, -100, -200, 100, 100, 0)]
    body = _cut(body, [_cylinder((0, 0, -150), (0, 0, 155), 60)])
    body = _cut(body, [_recess((60, 0, -75), (1, 0, 0), 25, 4), _recess((-60, 0, -75), (-1, 0, 0), 25, 4)])
    return [
        ("MF", "mf-right", face((64, 0, -75), (-1, 0, 0), within=3)),
        ("MF", "mf-left", face((-64, 0, -75), (1, 0, 0), within=3)),
    ]


def lf_only_box() -> list[SourceRule]:
    """Two LF drivers and nothing else: no role that makes a front meaningful."""

    body = [_box(-100, -150, -200, 100, 150, 0)]
    body = _cut(body, [_recess((0, -60, 0), (0, 0, -1), 60, 8), _recess((0, 70, 0), (0, 0, -1), 60, 8)])
    return [
        ("LF", "lf-1", face((0, -60, -8), (0, 0, 1), within=3)),
        ("LF", "lf-2", face((0, 70, -8), (0, 0, 1), within=3)),
    ]


def occluded_hf() -> list[SourceRule]:
    """An HF source facing into a sealed internal void, with an LF on the front."""

    body = [_box(-100, -100, -200, 100, 100, 0)]
    body = _cut(body, [_box(-40, -40, -150, 40, 40, -60), _recess((0, 0, 0), (0, 0, -1), 50, 6)])
    return [
        ("HF", "hf", face((0, 0, -60), (0, 0, -1), within=3)),
        ("LF", "lf", face((0, 0, -6), (0, 0, 1), within=3)),
    ]


# -- meshing ---------------------------------------------------------------------


def half_space_tool(normal: Sequence[float], extent: float) -> tuple[int, int]:
    """A box covering the half-space ``normal . p >= 0`` within ``extent``."""

    n = np.asarray(normal, dtype=float)
    low = np.full(3, -extent)
    high = np.full(3, extent)
    axis = int(np.argmax(np.abs(n)))
    if n[axis] > 0:
        low[axis] = 0.0
    else:
        high[axis] = 0.0
    return _box(*low, *high)


def mesh_fixture(
    builder: Callable[[], list[SourceRule]],
    *,
    size_mm: float,
    keep: Sequence[Sequence[float]] = (),
    drop_planes: Sequence[int] = (),
) -> FixtureMesh:
    """Build, mesh and orient a fixture in the current gmsh session.

    ``keep`` intersects the model with the half-spaces ``n . p >= 0`` and
    ``drop_planes`` then removes the faces on those coordinate planes (0 = x,
    1 = y), leaving the open reduced model WG's own cutter leaves.
    """

    import gmsh

    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.clear()
    gmsh.model.add("frame-fixture")
    rules = builder()
    occ = gmsh.model.occ
    if keep:
        volumes = occ.getEntities(3)
        tools = [half_space_tool(normal, 5000.0) for normal in keep]
        tool = tools[0]
        if len(tools) > 1:
            out, _ = occ.intersect([tools[0]], tools[1:])
            tool = out[0]
        occ.intersect(volumes, [tool])
    occ.synchronize()
    gmsh.option.setNumber("Mesh.MeshSizeMax", size_mm)
    gmsh.option.setNumber("Mesh.MeshSizeMin", size_mm / 6.0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
    gmsh.model.mesh.generate(2)
    node_tags, coords, _ = gmsh.model.mesh.getNodes()
    points = np.asarray(coords, dtype=float).reshape(-1, 3)
    index = {int(tag): position for position, tag in enumerate(node_tags)}
    volumes = [tag for _, tag in gmsh.model.getEntities(3)]
    extent = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    triangles_all: list[np.ndarray] = []
    tags_all: list[np.ndarray] = []
    sources: dict[str, SurveySource] = {}
    next_tag = 101
    tag_for_id: dict[str, int] = {}
    for _, surface in gmsh.model.getEntities(2):
        types, _, element_nodes = gmsh.model.mesh.getElements(2, surface)
        rows = [
            np.asarray(flat, dtype=np.int64).reshape(-1, 3)
            for typ, flat in zip(types, element_nodes, strict=True)
            if int(typ) == 2
        ]
        if not rows:
            continue
        tris = np.vectorize(index.__getitem__)(np.concatenate(rows))
        corners = points[tris]
        cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        areas = 0.5 * np.linalg.norm(cross, axis=1)
        center = (areas[:, None] * corners.mean(axis=1)).sum(axis=0) / areas.sum()
        if drop_planes and any(np.ptp(corners[:, :, plane]) < 1e-6 and abs(center[plane]) < 1e-6 for plane in drop_planes):
            continue
        largest = int(np.argmax(areas))
        probe = corners[largest].mean(axis=0) + 1e-3 * extent * cross[largest] / np.linalg.norm(cross[largest])
        if any(gmsh.model.isInside(3, volume, probe.tolist()) for volume in volumes):
            tris = tris[:, [0, 2, 1]]
            cross = -cross
        normal = cross.sum(axis=0)
        normal /= np.linalg.norm(normal)
        candidate = _Face(
            normal=normal,
            normals=cross / np.linalg.norm(cross, axis=1)[:, None],
            center=center,
            centroids=corners.mean(axis=1),
            edge=float(np.linalg.norm(corners[:, 1] - corners[:, 0], axis=1).max()),
        )
        tag = 1
        for role, source_id, rule in rules:
            if rule(candidate):
                if source_id not in tag_for_id:
                    tag_for_id[source_id] = next_tag
                    sources[source_id] = SurveySource(tag=next_tag, source_id=source_id, role=role)
                    next_tag += 1
                tag = tag_for_id[source_id]
                break
        triangles_all.append(tris)
        tags_all.append(np.full(len(tris), tag, dtype=np.int64))
    gmsh.clear()
    wanted = {source_id for _, source_id, _ in rules}
    kept_side = bool(keep)
    missing = wanted - set(sources)
    if missing and not kept_side:
        raise AssertionError(f"fixture sources not found: {sorted(missing)}")
    return FixtureMesh(
        points_mm=points,
        triangles=np.concatenate(triangles_all),
        tags=np.concatenate(tags_all),
        sources=tuple(sources[key] for key in sorted(sources, key=lambda k: sources[k].tag)),
    )


def msh22_text(points_mm: np.ndarray, triangles: np.ndarray, tags: np.ndarray, names: dict[int, str]) -> str:
    """An ASCII MSH 2.2 text in metres, as ingestion writes its solver mesh."""

    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$PhysicalNames", str(len(names))]
    lines += [f'2 {tag} "{name}"' for tag, name in sorted(names.items())]
    lines += ["$EndPhysicalNames", "$Nodes", str(len(points_mm))]
    lines += [f"{i + 1} {x * 1e-3:.17g} {y * 1e-3:.17g} {z * 1e-3:.17g}" for i, (x, y, z) in enumerate(points_mm)]
    lines += ["$EndNodes", "$Elements", str(len(triangles))]
    lines += [
        f"{i + 1} 2 2 {int(tag)} {int(tag)} {a + 1} {b + 1} {c + 1}"
        for i, ((a, b, c), tag) in enumerate(zip(triangles, tags, strict=True))
    ]
    lines.append("$EndElements")
    return "\n".join(lines) + "\n"


def write_step_fixture(builder: Callable[[], list[SourceRule]], pose: str, path: Any) -> dict[str, Any]:
    """Write a fixture posed facing ``pose`` as STEP; name its source faces.

    Returns ``{"bbox": [low, high], "sources": {id: {"role", "faces", "areas"}}}``
    with each source's ADVANCED_FACE indices, which is how WGLink selects faces.
    Run inside a gmsh session.
    """

    import gmsh
    from hornlab_mesher.step_import import advanced_face_order, gmsh_surface_tags

    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.clear()
    gmsh.model.add("frame-fixture-step")
    rules = builder()
    turn = POSE_TURNS[pose]
    if turn is not None:
        (ax, ay, az), degrees = turn
        gmsh.model.occ.rotate(gmsh.model.occ.getEntities(3), 0, 0, 0, ax, ay, az, math.radians(degrees))
    gmsh.model.occ.synchronize()
    gmsh.write(str(path))
    gmsh.clear()
    gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
    gmsh.model.occ.synchronize()
    box = gmsh.model.getBoundingBox(-1, -1)
    surfaces = gmsh_surface_tags()
    order = advanced_face_order(path)
    rotation = POSES[pose]
    found: dict[str, dict[str, Any]] = {}
    for surface in surfaces:
        if str(gmsh.model.getType(2, surface)).casefold() != "plane":
            continue
        center = rotation.T @ np.asarray(gmsh.model.occ.getCenterOfMass(2, surface), dtype=float)
        for role, source_id, rule in rules:
            if float(np.linalg.norm(center - rule.center)) <= rule.within:  # type: ignore[attr-defined]
                entry = found.setdefault(source_id, {"role": role, "faces": [], "areas": []})
                entry["faces"].append(order[surfaces.index(surface)])
                entry["areas"].append(float(gmsh.model.occ.getMass(2, surface)))
                break
    gmsh.clear()
    missing = {source_id for _, source_id, _ in rules} - set(found)
    if missing:
        raise AssertionError(f"fixture sources not found in the STEP: {sorted(missing)}")
    return {"bbox": [list(box[:3]), list(box[3:])], "sources": found}
