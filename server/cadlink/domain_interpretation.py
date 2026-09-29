"""What an imported model's acoustic domain is: the M1c-auto interpretation.

PLAN.md, "M1c-auto. Automatic domain; the Model dropdown is removed", as
narrowed on 2026-09-22: every model is solved as the smallest domain the
checks allow, and nothing asks. Geometry alone shows only that a model *can*
be read as a half, never that it *is* one, so an already-cut model is mirrored
only with recorded evidence of the cut:

- a declaration in the return (``reduced-domain-v1``, unchanged);
- the user's own reading ("Change"), made on this snapshot or earlier in the
  same lineage;
- cut provenance the add-in recorded from the Fusion timeline during the
  explicit export (``assembly.cut_provenance`` under ``domain-automatic-v1``);
- an earlier provenance-backed reading of the same lineage.

Without evidence a model that looks cut is prepared as shown, unmirrored. An
open half with a spanning rigid-shell cut rim is refused at solve rather than
submitted as a full domain. A previous as-shown reading is never evidence.

One detector, observations apart from conclusions:

- :func:`observe` reads the solve mesh -- the deterministic import mesh, never
  the display tessellation -- and reports, per coordinate plane of the CAD
  (exported) frame, what is there: which side the geometry is on, free rim
  edges on the plane, faces lying in it, sources meeting it, and open edges
  anywhere else.
- :func:`conclude` turns one plane's observation into a conclusion:
  ``wg-cut`` (WG's own validated cut of a whole model), ``straddles`` (a whole
  model across the plane), ``candidate`` (a positive-side open rim on x0/y0
  with a source meeting the plane and nothing else open or capped),
  ``ambiguous`` (a rim, or a face with a source on it, that is not a clean
  candidate) or ``none``.
- :func:`apply_evidence` decides, per evidenced plane, whether the reading
  revalidates on this geometry. Evidence belonging to this snapshot (its own
  provenance, a Change made on it) that fails is a refusal with the remedy in
  words; evidence reused from the lineage that fails is simply not used.

The mirrored model is prepared exactly as a hand declaration of the same
planes (``declared_cut_planes``), so the solver input is the same; the mesher's
declared-domain verification then re-checks the open section, leaks and
winding. The interpretation is recorded on the ingestion record, enters the
mesh cache key when it changes the reading, and -- through the preparation's
plan identity (``preparation._resumable``) -- invalidates approvals.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from .wgreturn import (
    CUT_ORIGIN_PLANES,
    DOMAIN_PLANES,
    cut_provenance,
    declared_domain_planes,
    domain_kind,
)

if TYPE_CHECKING:  # pragma: no cover
    from .store import CadLinkStore


CONTRACT = "cad-domain-interpretation-v1"
PLANES = ("x0", "y0", "z0")
#: The planes a reduced domain may be mirrored on (the solver's own set).
SUPPORTED_PLANES = DOMAIN_PLANES
#: The tolerance ``verify_symmetry_cut`` reads a cut plane with
#: (``server.mesh.imported.SYMMETRY_SNAP_TOLERANCE_MM``); a test pins them equal.
TOLERANCE_MM = 1.0e-4
#: A rim is at least this many free edges on one plane, as the mesher's
#: ``detect_symmetry_planes`` counts it, so a stray leak vertex is not a cut.
MIN_RIM_EDGES = 3
#: A cut removes a whole side, so its rigid-shell rim is the cut shell's section
#: on the plane. That section need not be the shell's widest: side wings,
#: flanges, a tapered or chamfered cabinet or a horn flaring past the cut all
#: make the shell wider elsewhere than where it was cut. A port or vent that
#: happens to end on the plane is a small fraction of the cabinet in at least
#: one in-plane direction. Half the shell's extent, in both in-plane
#: directions, sits between the two with margin on each side.
SPANNING_RIM_FRACTION = 0.5

DECLARATION = "declaration"
USER = "user"
USER_LINEAGE = "user-lineage"
PROVENANCE = "cad-provenance"
LINEAGE = "lineage"
#: Evidence that belongs to this very snapshot: when it does not revalidate the
#: snapshot is refused with the reason, rather than solved some other way.
STRICT_SOURCES = frozenset({USER, PROVENANCE})

READING_FULL = "full"
READING_REDUCED = "reduced"
READING_AS_SHOWN = "as-shown"

_LETTER = {"x0": "x", "y0": "y", "z0": "z"}
_ORIGIN_PLANE = {plane: origin for origin, plane in CUT_ORIGIN_PLANES.items()}


class DomainReadingError(ValueError):
    """A reading this record cannot take."""


def plane_words(plane: str) -> str:
    return f"{_LETTER[plane]} = 0"


def _positive_side(plane: str) -> str:
    return f"{_LETTER[plane]} ≥ 0"


def side_words(plane: str, side: str | None) -> str:
    """``x ≥ 0`` / ``x ≤ 0``: the side of a plane a cut kept."""

    return f"{_LETTER[plane]} {'≤' if side == 'negative' else '≥'} 0"


# -- the plan: which evidence exists before anything is meshed ---------------------------


@dataclass(frozen=True)
class DomainPlan:
    """The evidence a preparation has before meshing, and what it reads."""

    manifest_domain: str
    source: str | None = None
    #: ``reduced`` or ``as-shown`` when evidence states a reading.
    reading: str | None = None
    #: CAD (exported) frame planes the evidence reads as mirror planes.
    planes: tuple[str, ...] = ()
    #: The snapshot a user's reading was made on.
    snapshot: str | None = None
    #: Recorded Fusion features, ``{"plane", "kind", "name"}``.
    features: tuple[Mapping[str, Any], ...] = ()
    #: Provenance entries that are not this snapshot's, and why.
    ignored: tuple[Mapping[str, Any], ...] = ()
    #: Why this snapshot's own evidence is refused before any meshing.
    refusal: str | None = None
    refusals: tuple[tuple[str, str], ...] = ()
    unlinked: bool = True
    #: The side of each plane recorded provenance says the cut kept. Checked
    #: against the geometry; not part of the identity (the planes are).
    kept_sides: tuple[tuple[str, str], ...] = ()
    #: Identity that permits a reading to carry to a later snapshot.
    body_object_ids: tuple[str, ...] = ()
    export_frame: str = "root-component"

    @property
    def strict(self) -> bool:
        return self.source in STRICT_SOURCES

    @property
    def evidenced_planes(self) -> tuple[str, ...]:
        """Planes the evidence asks WG to mirror (none for a declaration: it is today's path)."""

        if self.source in (None, DECLARATION) or self.reading != READING_REDUCED:
            return ()
        return self.planes

    def identity(self) -> dict[str, Any] | None:
        """What a preparation's reuse and the mesh cache key compare.

        None when there is no evidence beyond a declaration: such a snapshot is
        prepared exactly as before, under its old cache key.
        """

        if self.source in (None, DECLARATION):
            return None
        return {
            "contract": CONTRACT,
            "source": self.source,
            "reading": self.reading,
            "planes": list(self.planes),
            "snapshot": self.snapshot,
            "features": [dict(item) for item in self.features],
            "body_object_ids": list(self.body_object_ids),
            "export_frame": self.export_frame,
        }


def _export_frame(manifest: Mapping[str, Any]) -> str:
    coordinates = manifest.get("coordinate_system")
    frame = coordinates.get("export_frame") if isinstance(coordinates, Mapping) else None
    return str(frame or "root-component")


def _is_unlinked(manifest: Mapping[str, Any]) -> bool:
    from .solver_frame import is_unlinked_manifest

    return is_unlinked_manifest(manifest)


def _manifest_context(manifest: Mapping[str, Any]) -> tuple[tuple[str, ...], str]:
    scope = manifest.get("scope")
    included = scope.get("included") if isinstance(scope, Mapping) else None
    body_ids = tuple(
        sorted(
            str(item.get("object_id"))
            for item in included or ()
            if isinstance(item, Mapping) and item.get("object_id")
        )
    )
    return body_ids, _export_frame(manifest)


def _remembered_applies(
    remembered: Mapping[str, Any], manifest_sha256: str, context: tuple[tuple[str, ...], str]
) -> bool:
    """A reading carries only to the same exported bodies in the same frame."""

    if remembered.get("snapshot") == manifest_sha256:
        return True
    body_ids, export_frame = context
    return (
        tuple(sorted(str(item) for item in remembered.get("body_object_ids") or ()))
        == body_ids
        and remembered.get("export_frame") == export_frame
    )


def reading_key(store: CadLinkStore, manifest: Mapping[str, Any], manifest_sha256: str) -> str:
    """Where a lineage's reading is remembered: its project, or this exact snapshot."""

    from .project_setup import snapshot_project
    from .solver_frame import confirmation_key

    return confirmation_key(snapshot_project(store, manifest), manifest_sha256)


def record_key(record: Mapping[str, Any]) -> str:
    from .solver_frame import record_confirmation_key

    return record_confirmation_key(record)


def resolve_domain_plan(
    store: CadLinkStore | None, manifest: Mapping[str, Any], manifest_sha256: str
) -> DomainPlan:
    """The evidence for this snapshot's domain, in precedence order.

    1. A declaration in the return: today's declared path, unchanged.
    2. The user's reading (Change) for this lineage: theirs wins over any
       provenance, in either direction.
    3. This return's own cut provenance, when it belongs to this snapshot.
    4. An earlier provenance-backed reading of the lineage.
    """

    kind = domain_kind(manifest)
    declared = declared_domain_planes(manifest)
    if kind == "declared":
        return DomainPlan(
            manifest_domain=kind,
            source=DECLARATION if declared else None,
            reading=READING_REDUCED if declared else None,
            planes=declared,
        )
    unlinked = _is_unlinked(manifest)
    if not unlinked:
        # A linked return is solved in its WG design's frame, and a pre-cut
        # linked throat already fails role resolution; nothing to read here.
        return DomainPlan(manifest_domain=kind, unlinked=False)
    body_ids, export_frame = _manifest_context(manifest)
    remembered = None
    if store is not None:
        remembered = store.get_domain_reading(reading_key(store, manifest, manifest_sha256))
        if remembered is not None and not _remembered_applies(
            remembered, manifest_sha256, (body_ids, export_frame)
        ):
            remembered = None
    if remembered is not None and remembered.get("source") == USER:
        own = remembered.get("snapshot") == manifest_sha256
        return DomainPlan(
            manifest_domain=kind,
            source=USER if own else USER_LINEAGE,
            reading=str(remembered.get("reading")),
            planes=tuple(plane for plane in PLANES if plane in (remembered.get("planes") or [])),
            snapshot=str(remembered.get("snapshot") or "") or None,
            body_object_ids=body_ids,
            export_frame=export_frame,
        )
    entries, ignored = _usable_provenance(manifest)
    if entries:
        features = tuple(
            {"plane": entry["plane"], "kind": entry["feature"]["kind"], "name": entry["feature"]["name"]}
            for entry in entries
        )
        planes = tuple(plane for plane in PLANES if any(entry["plane"] == plane for entry in entries))
        refusals = _provenance_refusals(entries)
        return DomainPlan(
            manifest_domain=kind,
            source=PROVENANCE,
            reading=READING_REDUCED,
            planes=planes,
            features=features,
            ignored=tuple(ignored),
            refusal=refusals[0][1] if refusals else None,
            refusals=tuple(refusals),
            kept_sides=tuple(
                (str(entry["plane"]), str(entry.get("kept_side") or "positive"))
                for entry in entries
                if entry.get("kept_side") in ("positive", "negative")
            ),
            body_object_ids=body_ids,
            export_frame=export_frame,
        )
    if remembered is not None and remembered.get("source") == PROVENANCE:
        return DomainPlan(
            manifest_domain=kind,
            source=LINEAGE,
            reading=READING_REDUCED,
            planes=tuple(plane for plane in PLANES if plane in (remembered.get("planes") or [])),
            snapshot=str(remembered.get("snapshot") or "") or None,
            features=tuple(dict(item) for item in remembered.get("features") or []),
            ignored=tuple(ignored),
            body_object_ids=body_ids,
            export_frame=export_frame,
        )
    return DomainPlan(
        manifest_domain=kind,
        ignored=tuple(ignored),
        body_object_ids=body_ids,
        export_frame=export_frame,
    )


def _usable_provenance(
    manifest: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Provenance entries that belong to this snapshot's bodies and frame.

    The reader already checked the body is one of this return's included
    bodies. An entry recorded in another component's frame names a plane in
    coordinates this STEP is not written in, so it is not used.
    """

    frame = _export_frame(manifest)
    usable: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    for entry in cut_provenance(manifest):
        if entry.get("export_frame") != frame:
            ignored.append(
                {
                    "feature": (entry.get("feature") or {}).get("name"),
                    "reason": f"recorded in the {entry.get('export_frame')} frame; this model is in the {frame} frame",
                }
            )
            continue
        usable.append(entry)
    return usable, ignored


def _provenance_refusals(
    entries: Sequence[Mapping[str, Any]],
) -> list[tuple[str, str]]:
    refusals: list[tuple[str, str]] = []
    for entry in entries:
        plane = str(entry["plane"])
        name = (entry.get("feature") or {}).get("name") or "the cut"
        if plane not in SUPPORTED_PLANES:
            refusals.append(
                (
                    plane,
                    f"{name} cuts this model on {plane_words(plane)} "
                    f"(the {_ORIGIN_PLANE[plane]} plane), which WG cannot mirror yet. Cut it on "
                    "the YZ or XZ origin plane instead, or send the whole model.",
                )
            )
        elif entry.get("kept_side") not in ("positive", "negative"):
            refusals.append(
                (
                    plane,
                    f"{name} does not say which side of {plane_words(plane)} it kept; "
                    "send the model again, or send the whole model.",
                )
            )
    return refusals


# -- the detector: observations on the solve mesh ------------------------------------------


@dataclass
class PlaneObservation:
    plane: str
    negative: int
    positive: int
    on_plane: int
    rim_edges: int
    cap_triangles: int
    cap_area_mm2: float
    #: Free rigid-shell edges on this plane when they spread across the shell
    #: they bound and that shell lies to one side of the plane: a cut rim.
    rigid_cut_rim_edges: int = 0
    #: The solver-frame plane this CAD plane is (the observation is keyed by
    #: the CAD plane). Only the solver's z0 is never mirrored.
    solver_plane: str = ""
    sources_on_plane: list[str] = field(default_factory=list)
    #: Sources the plane passes through (a source face meets it without lying
    #: in it): the throat a cut bisects, as opposed to a driver on a baffle.
    sources_bisected: list[str] = field(default_factory=list)
    wg_cut: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "negative_vertices": self.negative,
            "positive_vertices": self.positive,
            "on_plane_vertices": self.on_plane,
            "rim_edges": self.rim_edges,
            "cap_triangles": self.cap_triangles,
            "cap_area_mm2": round(self.cap_area_mm2, 6),
            "rigid_cut_rim_edges": self.rigid_cut_rim_edges,
            "solver_plane": self.solver_plane,
            "sources_on_plane": list(self.sources_on_plane),
            "sources_bisected": list(self.sources_bisected),
            "wg_cut": self.wg_cut,
        }


@dataclass
class Observations:
    planes: dict[str, PlaneObservation]
    #: Free edges on no rim plane and no plane WG cut: an opening elsewhere.
    other_open_edges: int
    free_edges: int
    #: Cut rims on a plane parallel to x = 0 or y = 0 of the solver frame but
    #: off the origin (``{"plane_axis", "offset_mm", "solver_axis",
    #: "rim_edges"}``, the axis and offset in CAD terms): a cut WG cannot mirror.
    off_origin_rims: list[dict[str, Any]] = field(default_factory=list)
    #: Cut rims on an oblique plane: a component's free rigid edges all in one
    #: plane that is square to none of the solver's axes, spanning the shell
    #: that lies to one side of it (``{"normal", "offset_mm", "rim_edges"}``,
    #: solver frame). A cut WG cannot mirror.
    oblique_rims: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "planes": {plane: item.to_json() for plane, item in self.planes.items()},
            "other_open_edges": self.other_open_edges,
            "free_edges": self.free_edges,
            "off_origin_rims": [dict(item) for item in self.off_origin_rims],
            "oblique_rims": [dict(item) for item in self.oblique_rims],
            "tolerance_mm": TOLERANCE_MM,
        }


def _vertex_components(count: int, faces: np.ndarray) -> np.ndarray:
    """A connected-component label per vertex, triangles joining their corners."""

    if not count or not len(faces):
        return np.arange(count)
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    rows = np.concatenate([faces[:, 0], faces[:, 1]])
    cols = np.concatenate([faces[:, 1], faces[:, 2]])
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(count, count))
    return connected_components(graph, directed=False)[1]


def _cut_rim_edges(
    points: np.ndarray,
    components: np.ndarray,
    rigid_rim: np.ndarray,
    axis: int,
    others: list[int],
    tolerance_mm: float,
) -> int:
    """How many rigid free edges on this plane form a cut rim, or 0.

    Free edges of source faces are a sheet's own boundary, not a cut, so only
    rigid edges are passed in. They are judged against the connected rigid
    shell they bound. That shell must lie to one side
    of the plane (another body the plane passes through is not the cut one)
    and the rim must spread across it in both in-plane directions (a vent or a
    port ending on the plane does not). A face of another body lying in the
    plane (a solid block cut there too, say) caps nothing on this rim.
    """

    if len(rigid_rim) < MIN_RIM_EDGES:
        return 0
    count = 0
    for component in np.unique(components[rigid_rim[:, 0]]):
        component_rim = rigid_rim[components[rigid_rim[:, 0]] == component]
        if len(component_rim) < MIN_RIM_EDGES:
            continue
        shell_points = points[components == component]
        coordinate = shell_points[:, axis]
        if np.any(coordinate < -tolerance_mm) == np.any(coordinate > tolerance_mm):
            continue
        rim_points = points[component_rim.ravel()]
        if all(
            float(np.ptp(shell_points[:, other])) > tolerance_mm
            and float(np.ptp(rim_points[:, other]))
            >= SPANNING_RIM_FRACTION * float(np.ptp(shell_points[:, other]))
            for other in others
        ):
            count += len(component_rim)
    return count


def _off_origin_cut_rims(
    points: np.ndarray,
    components: np.ndarray,
    rigid_free: np.ndarray,
    rotation: np.ndarray,
    tolerance_mm: float,
) -> list[dict[str, Any]]:
    """Cut rims on planes parallel to the solver's x0/y0 but off the origin.

    The same reading as an origin-plane cut rim (:func:`_cut_rim_edges`): free
    rigid edges lying in one plane at the edge of the shell they bound and
    spreading across it. A cut leaves its shell on one side, so the plane is
    one of the shell's own extreme planes. Planes square to the solver's z
    (the radiation axis) are a horn mouth or an open back, not a cut, and are
    not read here.
    """

    found: list[dict[str, Any]] = []
    if len(rigid_free) < MIN_RIM_EDGES:
        return found
    owners = components[rigid_free[:, 0]]
    for solver_axis in (0, 1):
        row = rotation[solver_axis]
        cad_axis = int(np.argmax(np.abs(row)))
        sign = 1.0 if row[cad_axis] > 0 else -1.0
        others = [axis for axis in range(3) if axis != solver_axis]
        for component in np.unique(owners):
            edges = rigid_free[owners == component]
            if len(edges) < MIN_RIM_EDGES:
                continue
            shell = points[components == component]
            for level in (float(shell[:, solver_axis].min()), float(shell[:, solver_axis].max())):
                if abs(level) <= tolerance_mm:
                    continue
                tolerance = max(tolerance_mm, 1.0e-9 * abs(level))
                on_level = (np.abs(points[edges[:, 0], solver_axis] - level) <= tolerance) & (
                    np.abs(points[edges[:, 1], solver_axis] - level) <= tolerance
                )
                rim = edges[on_level]
                if len(rim) < MIN_RIM_EDGES:
                    continue
                rim_points = points[rim.ravel()]
                if all(
                    float(np.ptp(shell[:, other])) > tolerance
                    and float(np.ptp(rim_points[:, other]))
                    >= SPANNING_RIM_FRACTION * float(np.ptp(shell[:, other]))
                    for other in others
                ):
                    found.append(
                        {
                            "plane_axis": "xyz"[cad_axis],
                            "offset_mm": round(sign * level, 6),
                            "solver_axis": "xyz"[solver_axis],
                            "rim_edges": int(len(rim)),
                        }
                    )
    return sorted(found, key=lambda item: (item["plane_axis"], item["offset_mm"]))


#: A rim is planar when its vertices are within this fraction of the shell's
#: diagonal of their best-fit plane (and at least the snap tolerance).
OBLIQUE_PLANARITY_REL = 1.0e-4
#: A plane is square to an axis when its normal is within this of the axis.
OBLIQUE_AXIS_TOLERANCE_DEG = 0.5


def _oblique_cut_rims(
    points: np.ndarray,
    components: np.ndarray,
    rigid_free: np.ndarray,
    tolerance_mm: float,
) -> list[dict[str, Any]]:
    """Cut rims on planes square to no solver axis (solver frame).

    Per connected rigid shell: its free edges all lie in one plane, the shell
    lies to one side of that plane, and the rim spreads across the shell in
    two in-plane directions (the same reading as an origin-plane cut rim).
    Planes square to a solver axis are read elsewhere: x/y cut rims on and
    off the origin, and openings square to the radiation axis (a mouth).
    """

    found: list[dict[str, Any]] = []
    if len(rigid_free) < MIN_RIM_EDGES:
        return found
    owners = components[rigid_free[:, 0]]
    cosine = float(np.cos(np.radians(OBLIQUE_AXIS_TOLERANCE_DEG)))
    for component in np.unique(owners):
        edges = rigid_free[owners == component]
        if len(edges) < MIN_RIM_EDGES:
            continue
        rim_points = points[np.unique(edges.ravel())]
        shell = points[components == component]
        diagonal = float(np.linalg.norm(np.ptp(shell, axis=0)))
        if diagonal <= 0.0 or len(rim_points) < 3:
            continue
        centre = rim_points.mean(axis=0)
        _u, singular, vt = np.linalg.svd(rim_points - centre, full_matrices=False)
        normal = vt[-1]
        tolerance = max(tolerance_mm, OBLIQUE_PLANARITY_REL * diagonal)
        if float(np.max(np.abs((rim_points - centre) @ normal))) > tolerance:
            continue
        if float(np.max(np.abs(normal))) >= cosine:
            continue  # square to a solver axis: read by the other detectors
        distances = (shell - centre) @ normal
        if np.any(distances < -tolerance) and np.any(distances > tolerance):
            continue  # the shell is on both sides: not a cut there
        in_plane = vt[:2]
        rim_extent = np.ptp((rim_points - centre) @ in_plane.T, axis=0)
        shell_extent = np.ptp((shell - centre) @ in_plane.T, axis=0)
        if np.all(shell_extent > tolerance) and np.all(
            rim_extent >= SPANNING_RIM_FRACTION * shell_extent
        ):
            found.append(
                {
                    "normal": [round(float(value), 6) for value in normal],
                    "offset_mm": round(float(centre @ normal), 6),
                    "rim_edges": int(len(edges)),
                }
            )
    return found


def observe(
    points_mm: np.ndarray,
    triangles: np.ndarray,
    tags: np.ndarray,
    *,
    source_tags: Mapping[int, str],
    solver_from_assembly: np.ndarray,
    wg_cut_planes: Iterable[str] = (),
    tolerance_mm: float = TOLERANCE_MM,
) -> Observations:
    """What the solve mesh shows on each coordinate plane of the CAD frame.

    ``points_mm`` are solver-frame coordinates (the mesh as meshed);
    ``solver_from_assembly`` is the record's normalisation. Each solver plane
    is observed and reported under the CAD plane it is, with CAD sides.
    ``wg_cut_planes`` are the solver planes WG itself cut, whose rim is WG's.
    """

    points = np.asarray(points_mm, dtype=float).reshape(-1, 3)
    faces = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    tags = np.asarray(tags).reshape(-1)
    rotation = np.asarray(solver_from_assembly, dtype=float)[:3, :3]
    cut = {str(plane) for plane in wg_cut_planes}
    edges = np.sort(faces[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1)
    unique, inverse, counts = (
        np.unique(edges, axis=0, return_inverse=True, return_counts=True)
        if len(edges) else (edges, np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))
    )
    free_indices = np.flatnonzero(counts[inverse] == 1)
    free = edges[free_indices]
    on = np.abs(points) <= tolerance_mm
    source_mask = np.isin(tags, np.asarray(list(source_tags), dtype=np.int64)) if len(tags) else np.zeros(0, bool)
    # Each free edge has exactly one incident face. A standalone source sheet
    # can have a long edge on an origin plane without opening the rigid shell.
    rigid_free = ~source_mask[free_indices // 3]
    # Source sheets can touch or cross a cut shell without becoming part of it.
    components = _vertex_components(len(points), faces[~source_mask])
    observed: dict[str, PlaneObservation] = {}
    rim_axes: set[int] = set()
    for solver_axis, solver_plane in enumerate(PLANES):
        row = rotation[solver_axis]
        cad_axis = int(np.argmax(np.abs(row)))
        sign = 1.0 if row[cad_axis] > 0 else -1.0
        coordinate = points[:, solver_axis]
        negative = int(np.count_nonzero(coordinate < -tolerance_mm))
        positive = int(np.count_nonzero(coordinate > tolerance_mm))
        if sign < 0:
            negative, positive = positive, negative
        on_plane = on[:, solver_axis]
        # As the mesher counts a rim: free edges lying on this plane and on no
        # other coordinate plane, so a rim along another plane's edge (WG's own
        # cut crossing a baffle, say) is not read as a rim here.
        others = [axis for axis in range(3) if axis != solver_axis]
        rim_mask = (
            on_plane[free[:, 0]] & on_plane[free[:, 1]]
            & ~(on[free[:, 0]][:, others] & on[free[:, 1]][:, others]).any(axis=1)
        ) if len(free) else np.zeros(0, dtype=bool)
        rim = int(np.count_nonzero(rim_mask))
        rigid_rim = free[rim_mask & rigid_free]
        cut_rim = _cut_rim_edges(points, components, rigid_rim, solver_axis, others, tolerance_mm)
        cap = on_plane[faces].all(axis=1) if len(faces) else np.zeros(0, bool)
        corners = points[faces[cap]] if np.any(cap) else np.zeros((0, 3, 3))
        cap_area = float(
            0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum()
        ) if len(corners) else 0.0
        touching: list[str] = []
        bisected: list[str] = []
        if len(faces) and np.any(source_mask):
            for tag, source_id in sorted(source_tags.items()):
                rows = faces[tags == tag]
                if not len(rows):
                    continue
                meets = on_plane[rows]
                if np.any(meets):
                    touching.append(str(source_id))
                if np.any(meets.any(axis=1) & ~meets.all(axis=1)):
                    bisected.append(str(source_id))
        wg_cut = solver_plane in cut
        if rim >= MIN_RIM_EDGES or wg_cut:
            rim_axes.add(solver_axis)
        observed[PLANES[cad_axis]] = PlaneObservation(
            plane=PLANES[cad_axis],
            negative=negative,
            positive=positive,
            on_plane=int(np.count_nonzero(on_plane)),
            rim_edges=rim,
            cap_triangles=int(np.count_nonzero(cap)),
            cap_area_mm2=cap_area,
            rigid_cut_rim_edges=cut_rim,
            solver_plane=solver_plane,
            sources_on_plane=touching,
            sources_bisected=bisected,
            wg_cut=wg_cut,
        )
    elsewhere = 0
    if len(free):
        on_rim = np.zeros(len(free), dtype=bool)
        for axis in rim_axes:
            on_rim |= on[free[:, 0], axis] & on[free[:, 1], axis]
        elsewhere = int(np.count_nonzero(~on_rim))
    return Observations(
        planes={plane: observed[plane] for plane in PLANES},
        other_open_edges=elsewhere,
        free_edges=int(len(free)),
        off_origin_rims=_off_origin_cut_rims(
            points, components, free[rigid_free] if len(free) else free, rotation, tolerance_mm
        ),
        oblique_rims=_oblique_cut_rims(
            points, components, free[rigid_free] if len(free) else free, tolerance_mm
        ),
    )


def observe_record_mesh(built: Mapping[str, Any], msh_text: str | None = None) -> Observations | None:
    """:func:`observe` over a fresh or cached build; None when its mesh cannot be read."""

    from .frame_infer import parse_tagged_msh

    try:
        points_m, triangles, tags, _names = parse_tagged_msh(
            msh_text if msh_text is not None else str(built.get("msh_text") or "")
        )
    except Exception:  # noqa: BLE001 - an unreadable mesh has no observations
        return None
    if not len(triangles):
        return None
    normalisation = built.get("normalisation")
    matrix = normalisation.get("matrix") if isinstance(normalisation, Mapping) else None
    try:
        solver_from_assembly = np.asarray(matrix if matrix is not None else np.eye(4), dtype=float)
        if solver_from_assembly.shape != (4, 4):
            return None
    except (TypeError, ValueError):
        return None
    allocation = built.get("tag_allocation") or {}
    source_tags = {
        int(tag): str(source_id)
        for source_id, tag in (allocation.get("source_tags") or built.get("source_tags") or {}).items()
    }
    symmetry = built.get("symmetry") or {}
    return observe(
        np.asarray(points_m, dtype=float) * 1000.0,
        triangles,
        tags,
        source_tags=source_tags,
        solver_from_assembly=solver_from_assembly,
        wg_cut_planes=symmetry.get("cut_planes") or (),
    )


# -- conclusions -----------------------------------------------------------------------------


def conclude(observation: PlaneObservation, *, other_open_edges: int) -> tuple[str, list[str]]:
    """One plane's conclusion and, for ``ambiguous``, what makes it so."""

    if observation.wg_cut:
        return "wg-cut", []
    if observation.negative and observation.positive:
        return "straddles", []
    if observation.rim_edges >= MIN_RIM_EDGES and (observation.negative or observation.positive):
        reasons = []
        if observation.plane not in SUPPORTED_PLANES:
            reasons.append("unsupported-plane")
        if observation.cap_triangles:
            reasons.append("capped")
        if other_open_edges:
            reasons.append("other-openings")
        if not observation.sources_on_plane:
            reasons.append("no-source-on-plane")
        return ("ambiguous", reasons) if reasons else ("candidate", [])
    if observation.cap_triangles and observation.sources_bisected and not (
        observation.negative and observation.positive
    ):
        # A face on the plane where the plane passes through a driver: possibly
        # a capped cut, possibly a flat wall. Geometry cannot tell. (A driver
        # lying in a baffle on the plane is neither.)
        return "ambiguous", ["possible-cap"]
    return "none", []


def conclusions(observations: Observations | None) -> dict[str, dict[str, Any]]:
    if observations is None:
        return {}
    result = {}
    for plane, observation in observations.planes.items():
        status, reasons = conclude(observation, other_open_edges=observations.other_open_edges)
        result[plane] = {"status": status, "reasons": reasons}
    return result


def _mirrorable(observation: PlaneObservation, other_open_edges: int) -> str | None:
    """Why a reduced reading of this plane does not revalidate here, or None when it does.

    The revalidation list, per plane: the complete open cross-section on the
    plane, on one side of it, with no cap and no other opening. (Whether a
    driver meets the plane is not required: a mirrored pair of drivers leaves
    one of them clear of it.) A cut that kept the negative side is solved by
    reflecting its mesh onto the positive side the solver mirrors (stage 3).
    """

    plane = observation.plane
    words = plane_words(plane)
    if plane not in SUPPORTED_PLANES:
        return f"WG cannot mirror a cut on {words} yet; cut it on the YZ or XZ origin plane instead"
    if observation.cap_triangles:
        return (
            f"the cut on {words} is capped: a face closes it, which would solve as a wall. "
            "Delete the cap face so the cut is open"
        )
    if observation.rim_edges < MIN_RIM_EDGES:
        if other_open_edges:
            return (
                f"the model's open edge is not on {words}: cut it on the "
                f"{_ORIGIN_PLANE[plane]} origin plane (or a plane coincident with it) and leave the cut open"
            )
        return f"the model has no open cut on {words}"
    if other_open_edges:
        return (
            f"besides the cut on {words} the model has {other_open_edges} other open "
            "edge(s); a mirrored model would leak through them. Close them, or solve it as shown"
        )
    return None


def valid_choices(observations: Observations | None) -> list[tuple[str, ...]]:
    """The reduced readings the geometry would take, smallest set first."""

    if observations is None:
        return []
    ok = [
        plane
        for plane in SUPPORTED_PLANES
        if observations.planes[plane].rim_edges >= MIN_RIM_EDGES
        and not observations.planes[plane].wg_cut
        and _mirrorable(observations.planes[plane], observations.other_open_edges) is None
    ]
    choices: list[tuple[str, ...]] = [(plane,) for plane in ok]
    if len(ok) == 2:
        choices.append(tuple(ok))
    return choices


@dataclass
class EvidenceOutcome:
    applied: tuple[str, ...]
    not_applicable: dict[str, str]
    refusal: str | None
    #: The applied planes whose cut kept the negative side: their mesh is
    #: reflected onto the positive side (``reflect_triangle_mesh``).
    reflect: tuple[str, ...] = ()


def apply_evidence(plan: DomainPlan, observations: Observations | None, *, identity_problem: str | None = None) -> EvidenceOutcome:
    """Which evidenced planes revalidate on the observed geometry.

    A plane the model now spans is not a cut any more (a later edit made it
    whole): the evidence does not apply there, whatever its source. Any other
    failure refuses this snapshot's own evidence and sets aside the lineage's.
    """

    planes = plan.evidenced_planes
    if not planes:
        return EvidenceOutcome((), {}, None)
    if observations is None:
        reason = "the prepared mesh could not be read to check the cut"
        return EvidenceOutcome((), {plane: reason for plane in planes}, reason if plan.strict else None)
    applied: list[str] = []
    skipped: dict[str, str] = {}
    refusals: list[str] = []
    for plane in planes:
        observation = observations.planes[plane]
        if observation.wg_cut or (observation.negative and observation.positive):
            skipped[plane] = f"the model spans both sides of {plane_words(plane)}, so it is not cut there"
            continue
        problem = dict(plan.refusals).get(plane) or _mirrorable(
            observation, observations.other_open_edges
        )
        recorded_side = dict(plan.kept_sides).get(plane)
        observed_side = (
            "negative" if observation.negative and not observation.positive
            else "positive" if observation.positive and not observation.negative
            else None
        )
        if problem is None and recorded_side is not None and observed_side is not None and (
            recorded_side != observed_side
        ):
            problem = (
                f"the cut is recorded as keeping {side_words(plane, recorded_side)}, but the "
                f"model lies on {side_words(plane, observed_side)}"
            )
        if problem is None and identity_problem:
            problem = identity_problem
        if problem is None:
            applied.append(plane)
            continue
        skipped[plane] = problem
        refusals.append(problem)
    if refusals and plan.strict:
        return EvidenceOutcome((), skipped, refusals[0])
    if refusals:
        # Lineage evidence that no longer fits: solved as shown, never partly.
        return EvidenceOutcome((), skipped, None)
    reflect = tuple(
        plane
        for plane in applied
        if observations.planes[plane].negative and not observations.planes[plane].positive
    )
    return EvidenceOutcome(tuple(applied), skipped, None, reflect)


def evidence_refusal_message(plan: DomainPlan, problem: str) -> str:
    """The refusal a user reads when this snapshot's own evidence is denied."""

    if plan.source == PROVENANCE:
        names = ", ".join(sorted({str(item.get("name")) for item in plan.features})) or "a cut"
        lead = f"Fusion recorded {names} on this model, so WG would mirror it, but {problem}."
    else:
        lead = f"You chose to solve this model mirrored, but {problem}."
    return (
        f"symmetry: {lead} Or choose 'Solved as shown' on the model card to solve it unmirrored."
        if plan.source == USER
        else f"symmetry: {lead}"
    )


# -- the record ----------------------------------------------------------------------------


def interpretation_record(
    plan: DomainPlan,
    observations: Observations | None,
    built_symmetry: Mapping[str, Any],
    *,
    applied: Sequence[str] = (),
    outcome: EvidenceOutcome | None = None,
    cache_identity: Mapping[str, Any] | None = None,
    recovery: Mapping[str, Any] | None = None,
    reflected: Sequence[str] = (),
) -> dict[str, Any]:
    """What the ingestion record states about the domain it solved, and why.

    ``recovery`` is the verdict on the cuts the geometry shows
    (``cut_recovery.CutRecovery.to_json``) when WG judged them; ``applied``
    then names the planes it recovered, if it did.
    """

    judged = conclusions(observations)
    looks_cut = [plane for plane, item in judged.items() if item["status"] == "candidate"]
    ambiguous = [plane for plane, item in judged.items() if item["status"] == "ambiguous"]
    wg_cut = [str(plane) for plane in built_symmetry.get("cut_planes") or []]
    declared = [str(plane) for plane in built_symmetry.get("declared_cut_planes") or []]
    if plan.source == DECLARATION:
        reading, planes = READING_REDUCED, list(plan.planes)
    elif applied:
        reading, planes = READING_REDUCED, list(applied)
    elif looks_cut or ambiguous or (plan.source in (USER, USER_LINEAGE) and plan.reading == READING_AS_SHOWN):
        reading, planes = READING_AS_SHOWN, []
    else:
        reading, planes = READING_FULL, []
    recovered = (
        isinstance(recovery, Mapping)
        and bool(recovery.get("recoverable"))
        and bool(applied)
        and set(applied) == set(recovery.get("planes") or ())
    )
    if plan.source == DECLARATION or not plan.unlinked or recovered:
        # A cut recovered from its geometry offers nothing: solved as shown it
        # would be part of a speaker in free space, which is always refused.
        choices: list[dict[str, Any]] = []
    else:
        choices = []
        if reading == READING_REDUCED:
            choices.append({"reading": READING_AS_SHOWN})
        for option in valid_choices(observations):
            if reading == READING_REDUCED and list(option) == planes:
                continue
            choices.append({"reading": READING_REDUCED, "planes": list(option)})
    applied_evidence = bool(applied) or plan.source == DECLARATION
    evidence = {
        "source": plan.source,
        "reading": plan.reading,
        "planes": list(plan.planes),
        "features": [dict(item) for item in plan.features],
        "snapshot": plan.snapshot,
        "applied": applied_evidence
        or (plan.source in (USER, USER_LINEAGE) and plan.reading == READING_AS_SHOWN),
        "not_applicable": dict(outcome.not_applicable) if outcome else {},
        "ignored": [dict(item) for item in plan.ignored],
        "recovered": bool(recovered),
    }
    return {
        "contract": CONTRACT,
        "manifest_domain": plan.manifest_domain,
        "reading": reading,
        "planes": planes,
        "declared_planes": declared if plan.source == DECLARATION else [],
        "wg_cut_planes": wg_cut,
        "domain_planes": [str(plane) for plane in built_symmetry.get("domain_planes") or []],
        "looks_cut": looks_cut if reading != READING_REDUCED else [],
        "ambiguous": ambiguous if reading != READING_REDUCED else [],
        "conclusions": judged,
        "observations": observations.to_json() if observations is not None else None,
        "evidence": evidence,
        "choices": choices,
        "cut_recovery": dict(recovery) if isinstance(recovery, Mapping) else None,
        "reflected_planes": [plane for plane in PLANES if plane in set(reflected)],
        "plan": plan.identity(),
        "cache_identity": dict(cache_identity) if cache_identity is not None else None,
        "lineage_context": {
            "body_object_ids": list(plan.body_object_ids),
            "export_frame": plan.export_frame,
        },
        "excitation": {
            "requirement": "reflection-invariant" if built_symmetry.get("domain_planes") else None,
        },
    }


def record_plan_identity(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The plan identity a record was prepared under (None for none or before M1c-auto)."""

    interpretation = record.get("domain_interpretation")
    if not isinstance(interpretation, Mapping):
        return None
    plan = interpretation.get("plan")
    return dict(plan) if isinstance(plan, Mapping) else None


def has_rigid_rim_observations(interpretation: Any) -> bool:
    """Whether saved observations carry the rigid-shell rim this build judges cuts by.

    Records prepared by an earlier build lack it (or lack observations at all);
    their verified mesh is observed again before any solve.
    """

    observations = interpretation.get("observations") if isinstance(interpretation, Mapping) else None
    planes = observations.get("planes") if isinstance(observations, Mapping) else None
    return isinstance(planes, Mapping) and all(
        isinstance(planes.get(plane), Mapping)
        and isinstance(planes[plane].get("rigid_cut_rim_edges"), int)
        and planes[plane].get("solver_plane") in PLANES
        for plane in PLANES
    )


def cut_shaped_open_rim(interpretation: Mapping[str, Any]) -> tuple[str, int] | None:
    """A rigid-shell cut rim WG did not make, in saved mesh observations.

    Three free edges is the detector's minimum rim, shared with ``conclude``.
    ``rigid_cut_rim_edges`` counts only free rigid (non-source) edges that
    spread across the one-sided shell they bound (see ``_cut_rim_edges``), so
    a standalone source sheet's boundary and a small vent do not qualify,
    while a cut through the enclosure alone does, wherever the driver sits.
    On the solver's z0, which WG never mirrors, an open end (a horn mouth on
    the plane, say) is ordinary, so a cut there also needs a source the plane
    passes through.
    """

    observations = interpretation.get("observations")
    planes = observations.get("planes") if isinstance(observations, Mapping) else None
    if not isinstance(planes, Mapping):
        return None
    for plane in PLANES:
        item = planes.get(plane)
        if not isinstance(item, Mapping) or item.get("wg_cut"):
            continue
        cut_rim = item.get("rigid_cut_rim_edges")
        if not isinstance(cut_rim, int) or cut_rim < MIN_RIM_EDGES:
            continue
        if item.get("solver_plane") == "z0" and not item.get("sources_bisected"):
            continue
        rim = item.get("rim_edges")
        return plane, rim if isinstance(rim, int) else cut_rim
    return None


def open_half_refusal_message(plane: str, rim_edges: int, reason: str | None = None) -> str:
    """The refusal of an open cut; ``reason`` names the flip condition it failed."""

    if reason:
        return (
            f"The model is open along {plane_words(plane)} ({rim_edges} rim edges), "
            "so WG would solve half a speaker in free space, and it cannot mirror it as "
            f"the whole speaker's reduced domain: {reason}. Send the uncut "
            "model — WG finds the symmetry and reduces it automatically."
        )
    return (
        f"The model is open along {plane_words(plane)} ({rim_edges} rim edges), "
        "so WG would solve half a speaker in free space. Send the uncut "
        "model — WG finds the symmetry and reduces it automatically."
    )


def oblique_cut_refusal_message(rim_edges: int) -> str:
    return (
        f"The model is open along an oblique plane ({rim_edges} rim edges): it looks cut on "
        "an oblique plane, which WG cannot mirror in this version, and solved as shown it "
        "would be part of a speaker in free space. Send the uncut model — WG finds the "
        "symmetry and reduces it automatically."
    )


def off_centre_cut_refusal_message(axis: str, offset_mm: float, rim_edges: int) -> str:
    return (
        f"The model is open along {axis} = {offset_mm:.6g} mm ({rim_edges} rim edges): it "
        "looks cut off the origin planes, which WG cannot mirror in this version, and "
        "solved as shown it would be part of a speaker in free space. Cut it on the YZ "
        "or XZ origin plane, or send the uncut model — WG finds the symmetry and "
        "reduces it automatically."
    )


def interpretation_finding(
    interpretation: Mapping[str, Any], *, decision: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """The non-blocking finding that says which reading was solved, or None for a plain full model.

    With the snapshot's domain decision (``domain_decision.py``) its refusal is
    the one the finding states: the same words every submission refuses with.
    """

    reading = interpretation.get("reading")
    evidence = interpretation.get("evidence") or {}
    if reading == READING_REDUCED and evidence.get("recovered"):
        planes_list = list(interpretation.get("planes") or [])
        recovery = interpretation.get("cut_recovery") or {}
        kept = recovery.get("kept_sides") or {}
        reflected = [plane for plane in planes_list if kept.get(plane) == "negative"]
        planes = " and ".join(plane_words(plane) for plane in planes_list)
        detail = (
            f"recognised from its geometry as a model already cut on {planes} and solved "
            "as the reduced domain of the whole speaker"
        )
        if reflected:
            detail += (
                "; the kept "
                + " and ".join(side_words(plane, "negative") for plane in reflected)
                + " side was reflected onto the side WG mirrors"
            )
        return {
            "kind": "recovered-reduced-domain",
            "blocking": False,
            "planes": planes_list,
            "reflected_planes": reflected,
            "detail": detail + ".",
        }
    if reading == READING_REDUCED and evidence.get("source") not in (None, DECLARATION):
        planes = " and ".join(plane_words(plane) for plane in interpretation.get("planes") or [])
        return {
            "kind": "interpreted-reduced-domain",
            "blocking": False,
            "planes": list(interpretation.get("planes") or []),
            "source": evidence.get("source"),
            "detail": f"solved as a model already cut on {planes}, from recorded evidence ({evidence.get('source')}); Change on the model card solves it as shown.",
        }
    if reading == READING_AS_SHOWN:
        looks = list(interpretation.get("looks_cut") or [])
        refusal = decision.get("refusal") if isinstance(decision, Mapping) else None
        if isinstance(refusal, Mapping):
            refused: str | None = str(refusal.get("message"))
        elif decision is None:
            open_rim = cut_shaped_open_rim(interpretation)
            refused = open_half_refusal_message(*open_rim) if open_rim is not None else None
        else:
            refused = None
        detail = (
            refused
            if refused is not None
            else "solved as shown, unmirrored"
            + (f"; it looks cut at {' and '.join(plane_words(plane) for plane in looks)}" if looks else "")
            + ". Change on the model card solves it mirrored when the cut validates."
        )
        return {
            "kind": "domain-solved-as-shown",
            "blocking": False,
            "looks_cut": looks,
            "ambiguous": list(interpretation.get("ambiguous") or []),
            "detail": detail,
        }
    return None


# -- acoustic compatibility ----------------------------------------------------------------


#: How a drive channel may move its sources in a mirrored domain. ``normal``
#: is a piston on each face. ``axial`` moves each source along its own resolved
#: axis (``server.solver.imported.resolve_source_axes``): it is its own mirror
#: image only when that axis lies in the symmetry subspace, which
#: :func:`excitation_problem` checks per source and never projects.
_REFLECTION_INVARIANT_MOTIONS = frozenset({"normal", "axial"})


def excitation_problem(
    record: Mapping[str, Any],
    drive_channels: Iterable[Any],
    msh_text: str | None = None,
) -> str | None:
    """Why this excitation cannot drive the record's mirrored domain, or None.

    A mirrored solve drives each source's image exactly as the source. That is
    the full model's excitation only when the driven boundary field is
    invariant under the reflection:

    - every source is its own image: one identity, one channel, one complex
      drive (paired *distinct* sources are never mirrored onto each other --
      the mesher's mirror test is per source identity -- and a pre-cut model
      carries only the retained source of any pair);
    - each channel's motion is invariant under the reflection: a normal drive
      is; an axial drive is when every source's resolved axis lies in the
      symmetry subspace (a source the mirror cuts is completed by its image,
      so its axis has no component across the plane; a source the mirror does
      not touch may tilt across it, and then the reduction is refused and the
      model is solved whole).

    Anything else -- a motion WG does not know to be invariant, a mirror plane
    that does not contain +Z -- is a known incompatibility and refuses the
    solve; no reading of the domain can answer it away.
    """

    from server.solver.imported import imported_domain_planes

    planes = tuple(imported_domain_planes(record))
    if not planes:
        return None
    unsupported = [plane for plane in planes if plane not in SUPPORTED_PLANES]
    if unsupported:
        return (
            "This model is mirrored on " + ", ".join(unsupported)
            + ", which does not contain the radiation axis; WG cannot drive it mirrored."
        )
    driven: dict[str, str] = {}
    channels = list(drive_channels)
    if any(
        str((c.get("motion") if isinstance(c, Mapping) else getattr(c, "motion", None)) or "normal") == "axial"
        for c in channels
    ):
        from server.mesh.artifact import ImportedMeshArtifactError
        from server.solver.imported import (
            axial_domain_problem,
            read_verified_import_mesh,
            resolve_record_axial_axes,
        )

        try:
            text = msh_text if msh_text is not None else read_verified_import_mesh(record)
            axes = resolve_record_axial_axes(record, text, channels)
        except ImportedMeshArtifactError as exc:
            return f"Axial source motion needs the solve mesh to find each source's axis: {exc}"
        except ValueError as exc:
            return f"Axial source motion cannot be solved: {exc}"
        problem = axial_domain_problem(axes, planes)
        if problem is not None:
            return problem
    def field_of(channel: Any, name: str) -> Any:
        return channel.get(name) if isinstance(channel, Mapping) else getattr(channel, name, None)

    for channel in channels:
        motion = str(field_of(channel, "motion") or "normal")
        channel_id = str(field_of(channel, "id") or "?")
        sources = field_of(channel, "source_ids")
        if motion not in _REFLECTION_INVARIANT_MOTIONS:
            return (
                f"Drive channel {channel_id} moves its sources {motion!r}, which is not the same "
                "under the mirror this model is solved with. Solve it as shown (Change on the "
                "model card), or send the whole model."
            )
        for source_id in sources or ():
            if str(source_id) in driven:
                return (
                    f"Source {source_id} is driven by two channels ({driven[str(source_id)]} and "
                    f"{channel_id}); a mirrored model drives each source once."
                )
            driven[str(source_id)] = channel_id
    return None


# -- the Change: a user's reading ----------------------------------------------------------


def record_reading(store: CadLinkStore, record: Mapping[str, Any], reading: Mapping[str, Any]) -> dict[str, Any]:
    """Remember the user's reading of this model's domain for its lineage.

    Only a reading the record offers (``choices``) is taken: a reduced reading
    offered is one the geometry already revalidated, and it is checked again,
    like a declaration, when the model is prepared. Solve prepares again,
    under the new plan, so no approval given before carries to it.
    """

    interpretation = record.get("domain_interpretation")
    if not isinstance(interpretation, Mapping):
        raise DomainReadingError("this model was prepared before WG interpreted its domain; prepare it again")
    if interpretation.get("evidence", {}).get("source") == DECLARATION:
        raise DomainReadingError("this return declares its domain in Fusion; change it there")
    kind = reading.get("reading")
    planes = [str(plane) for plane in reading.get("planes") or []]
    if kind == READING_AS_SHOWN:
        wanted: dict[str, Any] = {"reading": READING_AS_SHOWN}
        planes = []
    elif kind == READING_REDUCED:
        planes = [plane for plane in PLANES if plane in set(planes)]
        wanted = {"reading": READING_REDUCED, "planes": planes}
    else:
        raise DomainReadingError("a reading is 'as-shown' or 'reduced' with its planes")
    offered = [dict(choice) for choice in interpretation.get("choices") or []]
    current = {"reading": interpretation.get("reading"), **({"planes": list(interpretation.get("planes") or [])} if interpretation.get("reading") == READING_REDUCED else {})}
    if wanted not in offered and wanted != current:
        raise DomainReadingError(
            "this model cannot be read that way: "
            + ("WG solves it as shown already" if kind == READING_AS_SHOWN else "the cut does not validate on the planes named")
        )
    return store.record_domain_reading(
        record_key(record),
        {
            "source": USER,
            "reading": wanted["reading"],
            "planes": planes,
            "snapshot": str(record.get("manifest_sha256") or ""),
            "ingest_id": record.get("ingest_id"),
            **dict(interpretation.get("lineage_context") or {}),
        },
    )


def remember_provenance_reading(store: CadLinkStore, record: Mapping[str, Any]) -> None:
    """After a provenance-backed reading solved, remember it for the lineage.

    Only a project lineage remembers; a snapshot with none has nothing to carry
    it to. A user's own reading is never replaced by it.
    """

    interpretation = record.get("domain_interpretation")
    if not isinstance(interpretation, Mapping):
        return
    evidence = interpretation.get("evidence") or {}
    if interpretation.get("reading") != READING_REDUCED or evidence.get("source") != PROVENANCE:
        return
    key = record_key(record)
    if not key.startswith("lineage:"):
        return
    held = store.get_domain_reading(key)
    if held is not None and held.get("source") == USER:
        return
    store.record_domain_reading(
        key,
        {
            "source": PROVENANCE,
            "reading": READING_REDUCED,
            "planes": list(interpretation.get("planes") or []),
            "features": list(evidence.get("features") or []),
            "snapshot": str(record.get("manifest_sha256") or ""),
            "ingest_id": record.get("ingest_id"),
            **dict(interpretation.get("lineage_context") or {}),
        },
    )


def lineage_reading(store: CadLinkStore, record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The reading remembered for this record's lineage (or snapshot), if any."""

    return store.get_domain_reading(record_key(record))


def interpretation_view(store: CadLinkStore, record: Mapping[str, Any]) -> dict[str, Any]:
    """The model card's domain line: what was solved, why, the Change choices, and a pending Change."""

    interpretation = record.get("domain_interpretation")
    if not isinstance(interpretation, Mapping):
        return {"ingestId": record.get("ingest_id"), "available": False}
    remembered = lineage_reading(store, record)
    pending = None
    if remembered is not None and remembered.get("source") == USER:
        wanted = {"reading": remembered.get("reading")}
        if wanted["reading"] == READING_REDUCED:
            wanted["planes"] = list(remembered.get("planes") or [])
        solved = {"reading": interpretation.get("reading")}
        if solved["reading"] == READING_REDUCED:
            solved["planes"] = list(interpretation.get("planes") or [])
        solved_as_user = (interpretation.get("evidence") or {}).get("source") in (USER, USER_LINEAGE)
        if wanted["reading"] == READING_AS_SHOWN:
            differs = solved["reading"] == READING_REDUCED
        else:
            differs = solved != wanted or not solved_as_user
        offered = [dict(choice) for choice in interpretation.get("choices") or []]
        # A carried reading can stop applying after the model changes (for
        # example, a former half becomes whole). Do not promise that Solve
        # will apply a reading this geometry no longer offers.
        if differs and wanted in offered:
            pending = wanted
    decision = record.get("domain_decision")
    return {
        "ingestId": record.get("ingest_id"),
        "available": True,
        "interpretation": dict(interpretation),
        # The snapshot's one domain decision (``domain_decision.py``), exactly
        # as the ingestion record seals it and every submission checks it.
        "decision": dict(decision) if isinstance(decision, Mapping) else None,
        "remembered": remembered,
        "pending": pending,
    }


__all__ = [
    "CONTRACT",
    "DECLARATION",
    "DomainPlan",
    "DomainReadingError",
    "LINEAGE",
    "Observations",
    "PROVENANCE",
    "READING_AS_SHOWN",
    "READING_FULL",
    "READING_REDUCED",
    "STRICT_SOURCES",
    "USER",
    "USER_LINEAGE",
    "apply_evidence",
    "conclude",
    "conclusions",
    "evidence_refusal_message",
    "excitation_problem",
    "interpretation_finding",
    "interpretation_record",
    "interpretation_view",
    "lineage_reading",
    "observe",
    "observe_record_mesh",
    "plane_words",
    "record_key",
    "record_plan_identity",
    "record_reading",
    "remember_provenance_reading",
    "resolve_domain_plan",
    "valid_choices",
]
