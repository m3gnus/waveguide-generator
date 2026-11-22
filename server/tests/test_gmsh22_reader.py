"""The shared Gmsh 2.2 reader answers as the seven readers it replaced.

Each fixture goes through every old reader (frozen in ``legacy_gmsh22_readers``)
and through the view that replaced it, comparing answers bit for bit and
refusals by exception class and message. The differences left on these
fixtures, all on malformed text no WG writer produces, are listed in
``DIFFERENCES``.

``WG_GMSH22_CORPUS_DIR`` names a directory of ``*.msh`` texts (for example the
meshes a test run captured) to hold to the same comparison.
"""

from __future__ import annotations

import dataclasses
import math
import os
from pathlib import Path

import numpy as np
import pytest

from server.mesh import gmsh22
from server.mesh.imported import gmsh22_triangle_arrays
from server.mesh.tagged_msh import parse_tagged_msh
from server.solver.beat_adapter.mesh import read_surface, scale_msh_text
from server.solver.field_traces_store import mesh_trace_dof_counts
from server.solver.ground_plane import GroundPlane, model_extent, place_above_ground
from server.solver.imported import _msh_triangles
from server.solver.result_mapping import _gmsh22_observation_frame_parts

from server.tests import legacy_gmsh22_readers as legacy

ROOT = Path(__file__).resolve().parents[2]
REPO_MESHES = [
    "docs/validation/abec-rerun/A_asro68_full_nosym/250917asro68.msh",
    "docs/validation/abec-rerun/B_asro2_quarter_sym/asro2.msh",
    "docs/validation/abec-rerun/C_fine_quarter_sym/FINE1.msh",
    "frontend/src/viewport/test-fixtures/tagged_sources-small.msh",
    "server/solver/beat_runtime/fixtures/probe.msh",
    "server/solver/warmup_mesh.msh",
    "server/tests/fixtures/mesh_import/tetra.msh",
]


def _mesh(points, elements, *, node_ids=None, names=None, newline="\n", header="2.2 0 8"):
    node_ids = node_ids or list(range(1, len(points) + 1))
    rows = ["$MeshFormat", header, "$EndMeshFormat"]
    if names is not None:
        rows += ["$PhysicalNames", str(len(names)), *names, "$EndPhysicalNames"]
    rows += ["$Nodes", str(len(points))]
    rows += [" ".join(map(str, [node, *point])) for node, point in zip(node_ids, points)]
    rows += ["$EndNodes", "$Elements", str(len(elements)), *elements, "$EndElements", ""]
    return newline.join(rows)


TETRA = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
FACES = ["51 2 2 2 2 40 9 71", "52 2 2 1 1 40 3 9"]
BEAT = _mesh(TETRA, FACES, node_ids=[40, 9, 71, 3])
FULL = _mesh(
    [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.0, 0.1, 0.0], [0.0, 0.0, 0.2],
     [0.123456789012345678, -0.0, 1e-300]],
    ["1 2 2 2 2 1 3 2", "2 2 2 1 1 1 2 4", "3 2 2 1 1 2 3 4", "4 2 2 1 1 3 1 4",
     "5 2 2 3 3 2 5 4"],
)


def _crafted() -> dict[str, str]:
    cases = {
        "beat": BEAT,
        "full": FULL,
        "crlf": FULL.replace("\n", "\r\n"),
        "trailing-space": FULL.replace("$Nodes\n", "$Nodes  \n").replace("$Elements\n", " $Elements\n"),
        "untagged": _mesh(TETRA, ["1 2 0 1 3 2", "2 2 2 2 2 1 2 4", "3 2 2 1 1 2 3 4"]),
        "three-tags": _mesh(TETRA, ["1 2 3 2 2 9 1 3 2", "2 2 3 1 1 9 1 2 4"]),
        "tag-zero": _mesh(TETRA, ["1 2 2 0 0 1 3 2", "2 2 2 2 2 1 2 4"]),
        "points-and-lines": _mesh(TETRA, ["1 15 2 1 1 1", "2 1 2 1 1 1 2", *FACES[:0],
                                          "3 2 2 2 2 1 3 2", "4 2 2 1 1 1 2 4"]),
        "quad": _mesh(TETRA, ["1 2 2 2 2 1 3 2", "2 3 2 1 1 1 2 3 4"]),
        "quad-first": _mesh(TETRA, ["1 3 2 1 1 1 2 3 4", "2 2 2 0 0 1 3 2"]),
        "names": _mesh(TETRA, ["1 2 2 2 2 1 3 2", "2 2 2 7 7 1 2 4"],
                       names=['2 2 "source"', '2 7 "wall with spaces"', '1 3 "edge"']),
        "noncontiguous": _mesh(TETRA, ["10 2 2 2 2 101 303 202", "20 2 2 1 1 101 202 404"],
                               node_ids=[101, 202, 303, 404]),
        "degenerate": _mesh([[0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 1, 0]],
                            ["1 2 2 2 2 1 2 3", "2 2 2 1 1 1 2 4"]),
        "nan": _mesh([[0, 0, 0], [1, 0, 0], [0, float("nan"), 0], [0, 0, 1]], FACES[:0]
                     + ["1 2 2 2 2 1 3 2", "2 2 2 1 1 1 2 4"]),
        "no-triangles": _mesh(TETRA, ["1 1 2 1 1 1 2"]),
        "no-elements": FULL.split("$Elements")[0],
        "no-nodes": FULL.split("$Nodes")[0] + "$Elements" + FULL.split("$Elements")[1],
        "no-header": FULL.split("$EndMeshFormat\n")[1],
        "header-only": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
        "version-4": FULL.replace("2.2 0 8", "4.1 0 8"),
        "version-2.1": FULL.replace("2.2 0 8", "2.1 0 8"),
        "binary-flag": FULL.replace("2.2 0 8", "2.2 1 8"),
        "extra-sections": FULL + "$NodeData\n1\n\"p\"\n1\n0.0\n3\n0\n1\n5\n1 1\n2 1\n3 1\n4 1\n5 1\n$EndNodeData\n",
        "comment-first": "$Comments\nmade by hand\n$EndComments\n" + FULL,
        "scaled": scale_msh_text(FULL, 1000.0),
        "placed": place_above_ground(FULL, GroundPlane(axis="y", height_m=0.25)),
        # Malformed structure: some old readers skipped it, the shared one refuses.
        "undeclared-node": _mesh(TETRA[:2], ["1 2 2 1 1 1 2 3"]),
        "duplicate-node": _mesh(TETRA, FACES, node_ids=[40, 9, 40, 3]),
        "five-field-node": FULL.replace("\n1 0.0 0.0 0.0\n", "\n1 0.0 0.0 0.0 7\n"),
        "short-count": FULL.replace("$Nodes\n5\n", "$Nodes\n4\n"),
        "bad-element-token": FULL.replace("\n1 2 2 2 2 1 3 2\n", "\n1 2 2 2 2 1 3 x\n"),
        "short-triangle": FULL.replace("\n2 2 2 1 1 1 2 4\n", "\n2 2 2 1 1 1 2\n"),
        "untagged-dangling": _mesh(TETRA, ["1 2 2 2 2 1 3 2", "2 2 0 1 2 99", "3 2 2 1 1 1 2 4"]),
        "names-bad-count": FULL.replace("$Nodes\n", '$PhysicalNames\nx\n2 2 "s"\n$EndPhysicalNames\n$Nodes\n'),
        "names-no-end": FULL.replace("$Nodes\n", '$PhysicalNames\n1\n2 2 "s"\n$Nodes\n'),
        "names-bad-tag": FULL.replace("$Nodes\n", '$PhysicalNames\n1\n2 x "s"\n$EndPhysicalNames\n$Nodes\n'),
        "extra-triangle-field": FULL.replace("\n2 2 2 1 1 1 2 4\n", "\n2 2 2 1 1 1 2 4 7\n"),
        "format-last-line": FULL.split("$EndMeshFormat\n")[1] + "$MeshFormat",
        "bom": "\ufeff" + FULL,
        "bom-crlf": "\ufeff" + FULL.replace("\n", "\r\n"),
        "non-integer-node-id": FULL.replace("\n5 0.123", "\nfive 0.123"),
        "three-field-node": FULL.replace("\n1 0.0 0.0 0.0\n", "\n1 0.0 0.0\n"),
        "bad-coordinate": FULL.replace("\n1 0.0 0.0 0.0\n", "\n1 0.0 zero 0.0\n"),
        "bad-line-token": _mesh(TETRA, ["1 1 2 1 1 1 x", "2 2 2 2 2 1 3 2", "3 2 2 1 1 1 2 4"]),
        "bad-type-token": _mesh(TETRA, ["1 two 2 1 1 1 2", "2 2 2 2 2 1 3 2", "3 2 2 1 1 1 2 4"]),
        "truncated-elements": FULL.replace("$Elements\n5\n", "$Elements\n9\n").split("$EndElements")[0],
        "zero-elements": _mesh(TETRA, []),
        "negative-count": FULL.replace("$Nodes\n5\n", "$Nodes\n-1\n"),
    }
    return cases


def _outcome(function, *args, **kwargs):
    try:
        return ("ok", _canonical(function(*args, **kwargs)))
    except Exception as exc:  # the old readers' every failure is compared
        return ("refused", type(exc).__name__, str(exc))


def _canonical(value):
    if isinstance(value, np.ndarray):
        array = np.ascontiguousarray(value)
        return ("array", array.dtype.str, array.shape, array.tobytes())
    if isinstance(value, float):
        return ("float", value.hex() if math.isfinite(value) else repr(value))
    if isinstance(value, (np.generic,)):
        return ("scalar", type(value).__name__, _canonical(value.item()))
    if dataclasses.is_dataclass(value):
        return (type(value).__name__, *[
            (field.name, _canonical(getattr(value, field.name)))
            for field in dataclasses.fields(value)
        ])
    if isinstance(value, dict):
        return ("dict", tuple((key, _canonical(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, tuple(_canonical(item) for item in value))
    return value


PLANES = [GroundPlane(axis=axis, height_m=height) for axis in "xyz" for height in (0.0, 0.05, 10.0)]
STATS = {"vertex_count": 17, "triangle_count": 29}


def _calls():
    """(name, legacy call, shared-reader call) for every reader and argument set."""

    calls = []
    for symmetry in (None, "yz", "xz", "yz+xz"):
        for aperture in (None, 1, 3, 9):
            kwargs = {"symmetry_plane": symmetry, "aperture_tag": aperture}
            calls.append((f"frame {symmetry} {aperture}", legacy._gmsh22_observation_frame_parts,
                          _gmsh22_observation_frame_parts, (), kwargs))
    for stats in (None, STATS):
        calls.append((f"dofs {bool(stats)}", legacy.mesh_trace_dof_counts, mesh_trace_dof_counts,
                      (stats,), {}))
    for scale in (1.0, 0.001):
        calls.append((f"surface {scale}", legacy.read_surface, read_surface, (),
                      {"scale_to_m": scale}))
        calls.append((f"scale {scale}", legacy.scale_msh_text, scale_msh_text, (scale,), {}))
    calls.append(("msh_triangles", legacy._msh_triangles, _msh_triangles, (), {}))
    calls.append(("tagged", legacy.parse_tagged_msh, parse_tagged_msh, (), {}))
    calls.append(("triangle_arrays", legacy.gmsh22_triangle_arrays, gmsh22_triangle_arrays, (), {}))
    for plane in PLANES:
        calls.append((f"extent {plane.axis} {plane.height_m}", legacy.model_extent, model_extent, (plane,), {}))
        calls.append((f"place {plane.axis} {plane.height_m}", legacy.place_above_ground, place_above_ground, (plane,), {}))
    return calls


def _category(before, after) -> str:
    if before[0] == "ok" and after[0] == "ok":
        return "answer"
    if before[0] == "ok":
        return "refused"
    if after[0] == "ok":
        return "accepted"
    return "error" if before[1] != after[1] else "message"


def _differences(text: str) -> set[str]:
    """``"<reader> <category>"`` for every reader whose answer changed.

    Categories: ``answer`` (both answered, differently), ``refused`` (it used to
    answer), ``accepted`` (it used to refuse), ``error`` (both refuse, with a
    different exception class) and ``message`` (same class, other words).
    """

    found = set()
    for name, old, new, args, kwargs in _calls():
        gmsh22.clear_cache()
        before = _outcome(old, text, *args, **kwargs)
        after = _outcome(new, text, *args, **kwargs)
        # A second read comes from the shared artifact and must answer the same.
        again = _outcome(new, text, *args, **kwargs)
        if after != again:
            found.add(f"{name.split()[0]} cached")
        if before != after:
            found.add(f"{name.split()[0]} {_category(before, after)}")
    return found


#: Every difference from the old readers on these fixtures, all malformed text
#: no writer emits. The list is exact for the fixtures here, not for every
#: possible malformed text: other malformed rows may still answer differently.
#: Gmsh and WG write every solve text (user imports are refused or rewritten
#: by ``file_import``), so none of these reaches a reader. Where keeping an old
#: reader's answer was cheap it is kept; what is left on these fixtures is
#: listed, so a new difference on them fails.
DIFFERENCES = {
    # Both refuse; the old message named the token or node the lookup missed,
    # or numpy's ragged-array complaint, or a later symptom of the same fault.
    "bad-element-token": {"triangle_arrays message"},
    "duplicate-node": {"frame message"},
    "non-integer-node-id": {"triangle_arrays message"},
    "short-triangle": {"tagged message"},
    "three-field-node": {"frame message", "triangle_arrays message"},
}


@pytest.mark.parametrize("name", sorted(_crafted()))
def test_crafted_meshes_read_as_before(name):
    assert _differences(_crafted()[name]) == DIFFERENCES.get(name, set())


@pytest.mark.parametrize("path", REPO_MESHES)
def test_repository_meshes_read_as_before(path):
    assert _differences((ROOT / path).read_text(encoding="utf-8")) == set()


@pytest.mark.skipif(not os.environ.get("WG_GMSH22_CORPUS_DIR"), reason="no corpus named")
def test_captured_corpus_reads_as_before():
    paths = sorted(Path(os.environ["WG_GMSH22_CORPUS_DIR"]).glob("*.msh"))
    assert paths
    failures = {}
    for path in paths:
        differences = _differences(path.read_text(encoding="utf-8", errors="surrogatepass"))
        if differences:
            failures[path.name] = differences
    assert failures == {}


def test_one_text_is_parsed_once():
    gmsh22.clear_cache()
    first = gmsh22.read_msh(FULL)
    assert gmsh22.read_msh(str(FULL)) is first
    assert gmsh22.read_msh("".join(list(FULL))) is first


def test_shared_arrays_cannot_be_written_or_made_writeable():
    artifact = gmsh22.read_msh(FULL)
    arrays = [artifact.points, artifact.corner_indices]
    arrays += [value for value in vars(artifact.elements).values() if isinstance(value, np.ndarray)]
    for array in arrays:
        with pytest.raises(ValueError):
            array.flags.writeable = True
        if array.size:
            with pytest.raises(ValueError):
                array.flat[0] = 1


def test_the_cache_is_bounded_by_entries_bytes_and_age(monkeypatch):
    gmsh22.clear_cache()
    texts = [scale_msh_text(FULL, float(scale)) for scale in range(1, 8)]
    artifacts = [gmsh22.read_msh(text) for text in texts]
    assert len(gmsh22._cache) == gmsh22._CACHE_ENTRIES
    assert gmsh22.read_msh(texts[-1]) is artifacts[-1]
    assert gmsh22.read_msh(texts[0]) is not artifacts[0]

    gmsh22.clear_cache()
    monkeypatch.setattr(gmsh22, "_CACHE_MAX_BYTES", 10)
    gmsh22.read_msh(FULL)
    assert not gmsh22._cache  # a single entry over the cap is not kept

    monkeypatch.setattr(gmsh22, "_CACHE_MAX_BYTES", 1 << 30)
    clock = [1000.0]
    monkeypatch.setattr(gmsh22.time, "monotonic", lambda: clock[0])
    gmsh22.read_msh(FULL)
    clock[0] += gmsh22._CACHE_IDLE_SECONDS + 1
    gmsh22.read_msh(texts[1])
    assert list(gmsh22._cache) == [texts[1]]


def test_placement_neither_parses_elements_nor_keeps_the_unplaced_mesh():
    gmsh22.clear_cache()
    placed = place_above_ground(FULL, GroundPlane(axis="y", height_m=0.25))
    assert not gmsh22._cache
    assert gmsh22.read_msh_nodes(FULL).elements is None
    assert placed != FULL


def test_the_frame_survey_turns_any_unreadable_mesh_into_unavailable():
    """``infer_record_frame`` never raises for bad geometry, whatever the reader raises."""

    from server.cadlink.interpretation.frame_infer import SurveyUnavailable
    from server.cadlink.interpretation.frame_infer import parse_tagged_msh as survey_read

    # A dangling node (TaggedMeshError) and a short triangle row (plain ValueError).
    for name in ("undeclared-node", "short-triangle"):
        with pytest.raises(SurveyUnavailable):
            survey_read(_crafted()[name])


def test_idle_entries_expire_on_a_cache_hit_too(monkeypatch):
    gmsh22.clear_cache()
    clock = [1000.0]
    monkeypatch.setattr(gmsh22.time, "monotonic", lambda: clock[0])
    first = gmsh22.read_msh(FULL)
    other = scale_msh_text(FULL, 2.0)
    gmsh22.read_msh(other)
    clock[0] += gmsh22._CACHE_IDLE_SECONDS / 2
    assert gmsh22.read_msh(other) is not None  # a hit keeps ``other`` fresh
    clock[0] += gmsh22._CACHE_IDLE_SECONDS / 2 + 1
    assert gmsh22.read_msh(other) is not None  # this hit drops the idle FULL entry
    assert list(gmsh22._cache) == [other]
    assert gmsh22.read_msh(FULL) is not first
