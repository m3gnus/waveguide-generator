"""One domain decision per prepared CAD snapshot (``cad-domain-decision-v1``).

Stage 3, branch 1: ``server/cadlink/domain_decision.py`` states, once, what
an imported model is (full, cut, open sheet, unresolved), where each CAD cut
is and which side it kept, what WG cut, the planes and fraction the solver
mirrors, the frame, each source's identity, the evidence and conflicts, the
offered Changes and the refusal. The model card, the Solve card's plan, the
preparation, every submission and the job all read that one record.

Fixtures are real gmsh geometry through the production ingest, shared with
``test_cadlink_domain_automatic.py``. Nothing here reflects a mesh: a
negative-side cut is described (and stays refused) until mesh reflection.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from server.cadlink import domain_decision as dd
from server.cadlink.domain_interpretation import (
    cut_shaped_open_rim,
    interpretation_view,
    observe,
    record_plan_identity,
)
from server.mesh.gmsh_worker import _run_in_gmsh_session
from test_cadlink_domain_automatic import (
    BOX_DRIVER,
    HORN_THROAT,
    PAIR_DRIVERS,
    _bundle,
    _capped_half,
    _horn,
    _ingest,
    _mirrored_pair,
    _negative_half,
    _open_half,
    _open_mouth_horn_facing_minus_y,
    _open_quarter,
    _rim_off_plane,
    _slot_on_plane,
    _solve_verdicts,
    _split_open_backed_shell,
    _standalone_sheet,
    _store,
    _throat_farthest_along_y,
    SLOT_DRIVER,
    provenance,
)


gmsh = pytest.importorskip("gmsh")

_SOLVE_ENGINES = ("auto", "metal", "beat", "beat-cpu", "bempp")


def _decision(record: dict[str, Any]) -> dict[str, Any]:
    decision = record.get("domain_decision")
    assert isinstance(decision, dict), sorted(record)
    assert decision["contract"] == dd.CONTRACT
    # Every sealed decision describes its own record, intact.
    assert dd.decision_problem(record) is None
    assert decision["identity"]["decision_sha256"] == dd.decision_sha256(decision)
    assert decision["identity"]["snapshot_sha256"] == record["manifest_sha256"]
    assert decision["identity"]["mesh_content_sha256"] == record["mesh_content_sha256"]
    # Nothing is reflected in this branch, and every frame is proper.
    assert decision["reflected_axes"] == []
    assert decision["frame"]["proper"] is True
    assert decision["frame"]["determinant"] == pytest.approx(1.0)
    assert decision["frame"]["solver_from_cad"] == record["normalisation"]["matrix"]
    return decision


def _cut(decision: dict[str, Any], plane: str) -> dict[str, Any]:
    matches = [cut for cut in decision["cad_cuts"] if cut["plane"] == plane]
    assert len(matches) == 1, decision["cad_cuts"]
    return matches[0]


def _centre_in_z(path: Path) -> None:
    """Move the model so it straddles z = 0 (as PartyMEH does)."""

    def move() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        shapes = gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        box = gmsh.model.getBoundingBox(-1, -1)
        gmsh.model.occ.translate(shapes, 0.0, 0.0, -0.5 * (box[2] + box[5]) + 3.0)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(move)


def _negative_half_centred(path: Path) -> None:
    _negative_half(path)
    _centre_in_z(path)


def _open_half_centred(path: Path) -> None:
    _open_half(path)
    _centre_in_z(path)


# ------------------------------------------------------------------ readings


def test_a_full_model_is_full_and_its_smallest_reduction_is_wg_cut(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "full", _horn, HORN_THROAT), tmp_path / "data")
    decision = _decision(record)

    assert decision["input_reading"] == dd.INPUT_FULL
    assert decision["cad_cuts"] == []
    # The round horn is symmetric about both planes: a quarter wins over a half.
    assert decision["wg_cut_planes"] == ["x0", "y0"]
    assert decision["solver_domain"] == {"planes": ["x0", "y0"], "fraction": "quarter", "multiplier": 4}
    assert decision["confidence"] == dd.CONFIDENCE_ESTABLISHED
    assert decision["refusal"] is None
    assert decision["offered_changes"] == []
    throat = decision["sources"]["by_id"]["throat"]
    assert throat["tag"] == record["source_tags"]["throat"]
    assert throat["retained_fraction"] == pytest.approx(0.25, abs=1e-6)


def test_an_evidenced_half_is_a_cut_mirrored_and_quartered(tmp_path: Path) -> None:
    record = _ingest(
        _bundle(tmp_path, "half-prov", _open_half, HORN_THROAT, cut=[provenance("body-0")]),
        tmp_path / "data",
    )
    decision = _decision(record)

    assert decision["input_reading"] == dd.INPUT_CUT
    cut = _cut(decision, "x0")
    assert cut["kept_side"] == "positive"
    assert cut["found_by"] == "cad-provenance"
    assert cut["status"] == "mirrored"
    assert cut["features"] == [{"plane": "x0", "kind": "split-body", "name": "Split Body 3"}]
    assert decision["wg_cut_planes"] == ["y0"]
    assert decision["solver_domain"]["planes"] == ["x0", "y0"]
    assert decision["solver_domain"]["fraction"] == "quarter"
    assert decision["confidence"] == dd.CONFIDENCE_EVIDENCED
    assert decision["evidence"]["supporting"][0]["source"] == "cad-provenance"
    assert decision["evidence"]["conflicts"] == []
    assert decision["refusal"] is None
    # Change can still solve it as shown.
    assert {"reading": "as-shown"} in decision["offered_changes"]


def test_an_evidenced_quarter_is_two_mirrored_cuts(tmp_path: Path) -> None:
    record = _ingest(
        _bundle(
            tmp_path, "quarter-prov", _open_quarter, HORN_THROAT,
            cut=[provenance("body-0", "x0"), provenance("body-0", "y0", name="Split Body 4")],
        ),
        tmp_path / "data",
    )
    decision = _decision(record)

    assert decision["input_reading"] == dd.INPUT_CUT
    assert [cut["plane"] for cut in decision["cad_cuts"]] == ["x0", "y0"]
    assert {cut["status"] for cut in decision["cad_cuts"]} == {"mirrored"}
    assert decision["wg_cut_planes"] == []
    assert decision["solver_domain"]["fraction"] == "quarter"


def test_a_bare_positive_half_is_a_refused_cut_with_the_change_that_mirrors_it(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "half-bare", _open_half, HORN_THROAT), tmp_path / "data")
    decision = _decision(record)

    assert decision["input_reading"] == dd.INPUT_CUT
    cut = _cut(decision, "x0")
    assert (cut["kept_side"], cut["found_by"], cut["status"]) == ("positive", "geometry", "refused")
    assert cut["recovery"] == {"by_change": True}
    assert {"reading": "reduced", "planes": ["x0"]} in decision["offered_changes"]
    assert decision["solver_domain"]["fraction"] == "full"
    assert decision["confidence"] == dd.CONFIDENCE_REFUSED
    assert decision["refusal"]["code"] == "imported_open_half_shell"
    # The finding, the plan and every engine state this very refusal.
    finding = next(item for item in record["findings"] if item["kind"] == "domain-solved-as-shown")
    assert finding["detail"] == decision["refusal"]["message"]
    plan, outcomes = _solve_verdicts(record)
    assert plan["code"] == "imported_open_half_shell"
    assert plan["reason"] == "imported_open_half_shell: " + decision["refusal"]["message"]
    assert plan["domain_decision"] == dd.decision_summary(record)
    assert outcomes == {engine: "imported_open_half_shell" for engine in _SOLVE_ENGINES}


def test_a_negative_side_cut_is_described_and_stays_refused_until_reflection(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "negative", _negative_half, HORN_THROAT), tmp_path / "data")
    decision = _decision(record)

    assert decision["input_reading"] == dd.INPUT_CUT
    cut = _cut(decision, "x0")
    assert cut["kept_side"] == "negative"
    assert cut["status"] == "refused"
    # Every flip condition geometry can judge already holds; only the
    # reflection itself is missing.
    assert cut["recovery"] == {"by_reflection": dd.REFLECTION_PENDING, "blockers": []}
    assert decision["reflection"] == {"implemented": False, "pending": dd.REFLECTION_PENDING}
    assert decision["refusal"]["code"] == "imported_open_half_shell"
    assert decision["offered_changes"] == []
    _, outcomes = _solve_verdicts(record)
    assert set(outcomes.values()) == {"imported_open_half_shell"}


def test_an_open_backed_box_split_on_the_plane_is_a_refused_cut(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "open-backed", _split_open_backed_shell, BOX_DRIVER), tmp_path / "data")
    decision = _decision(record)

    assert decision["input_reading"] == dd.INPUT_CUT
    assert _cut(decision, "x0")["kept_side"] == "positive"
    assert decision["refusal"]["code"] == "imported_open_half_shell"


def test_a_capped_half_is_not_a_cut_the_solver_mirrors(tmp_path: Path) -> None:
    """A face closes the cut: a closed solid, never a symmetry plane."""

    record = _ingest(_bundle(tmp_path, "capped-bare", _capped_half, HORN_THROAT), tmp_path / "data")
    decision = _decision(record)

    assert decision["solver_domain"]["planes"] == [
        plane for plane in record["symmetry"]["domain_planes"]
    ]
    assert "x0" not in decision["solver_domain"]["planes"]
    assert all(cut["status"] != "mirrored" for cut in decision["cad_cuts"])
    observed = record["domain_interpretation"]["observations"]["planes"]["x0"]
    assert observed["cap_triangles"] > 0


def test_an_opening_off_the_origin_plane_is_not_read_as_a_cut_there(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "off-plane", _rim_off_plane, HORN_THROAT), tmp_path / "data")
    decision = _decision(record)

    assert all(cut["plane"] != "x0" for cut in decision["cad_cuts"])
    assert decision["input_reading"] in (dd.INPUT_OPEN_SHEET, dd.INPUT_UNRESOLVED)
    assert record["domain_interpretation"]["observations"]["other_open_edges"] > 0
    assert decision["refusal"] == (
        None if cut_shaped_open_rim(record["domain_interpretation"]) is None else decision["refusal"]
    )


def test_open_sheets_are_open_sheets_and_stay_solvable(tmp_path: Path) -> None:
    sheet = _ingest(_bundle(tmp_path, "sheet", _standalone_sheet, HORN_THROAT), tmp_path / "data")
    assert _decision(sheet)["input_reading"] == dd.INPUT_OPEN_SHEET
    assert _decision(sheet)["refusal"] is None

    mouth = _ingest(
        _bundle(tmp_path, "mouth", _open_mouth_horn_facing_minus_y, _throat_farthest_along_y),
        tmp_path / "data-mouth", solver_frame="-y",
    )
    decision = _decision(mouth)
    assert decision["input_reading"] == dd.INPUT_OPEN_SHEET
    assert decision["refusal"] is None
    assert decision["frame"]["axis"] == "-y"
    # A proper rotation, not the identity: the solver's z is CAD -y.
    assert decision["frame"]["solver_from_cad"] != np.eye(4).tolist()


def test_a_port_through_the_plane_is_an_opening_solved_as_shown(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "slot", _slot_on_plane, SLOT_DRIVER), tmp_path / "data")
    decision = _decision(record)

    assert decision["input_reading"] == dd.INPUT_OPEN_SHEET
    assert decision["resolved_reading"] == "as-shown"
    assert decision["cad_cuts"] == []
    assert decision["refusal"] is None


# ------------------------------------------------------------------ evidence


def test_an_x_cut_recorded_as_z0_is_shown_as_a_conflict_never_obeyed(tmp_path: Path) -> None:
    """PartyMEH's shape: an x cut whose recorded provenance names z0."""

    record = _ingest(
        _bundle(
            tmp_path, "mislabelled", _open_half_centred, HORN_THROAT,
            cut=[provenance("body-0", "z0", name="Extrude3", kind="extrude-cut")],
        ),
        tmp_path / "data",
    )
    decision = _decision(record)

    assert decision["input_reading"] == dd.INPUT_CUT
    cut = _cut(decision, "x0")
    assert (cut["found_by"], cut["kept_side"], cut["status"]) == ("geometry", "positive", "refused")
    conflicts = decision["evidence"]["conflicts"]
    assert {"source": "cad-provenance", "kind": "plane-mismatch", "recorded_planes": ["z0"],
            "observed_cut_planes": ["x0"]} in conflicts
    assert any(item.get("plane") == "z0" and "spans both sides" in item["observed"] for item in conflicts)
    assert decision["evidence"]["supporting"] == []
    assert decision["solver_domain"]["planes"] == []
    assert decision["refusal"]["code"] == "imported_open_half_shell"


def test_a_negative_x_cut_recorded_as_z0_is_described_like_partymeh(tmp_path: Path) -> None:
    record = _ingest(
        _bundle(
            tmp_path, "mislabelled-negative", _negative_half_centred, HORN_THROAT,
            cut=[provenance("body-0", "z0", name="Extrude9", kind="extrude-cut")],
        ),
        tmp_path / "data",
    )
    decision = _decision(record)

    cut = _cut(decision, "x0")
    assert cut["kept_side"] == "negative"
    assert cut["recovery"]["by_reflection"] == dd.REFLECTION_PENDING
    assert any(item.get("kind") == "plane-mismatch" for item in decision["evidence"]["conflicts"])
    assert decision["refusal"]["code"] == "imported_open_half_shell"


def test_stale_provenance_from_another_frame_is_ignored_and_the_half_refused(tmp_path: Path) -> None:
    record = _ingest(
        _bundle(
            tmp_path, "stale", _open_half, HORN_THROAT,
            cut=[provenance("body-0", export_frame="selected-occurrence-component")],
        ),
        tmp_path / "data",
    )
    decision = _decision(record)

    assert decision["evidence"]["ignored"]
    assert "frame" in decision["evidence"]["ignored"][0]["reason"]
    assert decision["evidence"]["supporting"] == []
    assert _cut(decision, "x0")["status"] == "refused"
    assert decision["refusal"]["code"] == "imported_open_half_shell"


def test_separate_left_and_right_sources_keep_their_identities_and_the_full_model(tmp_path: Path) -> None:
    pair = _ingest(
        _bundle(tmp_path, "pair", _mirrored_pair, PAIR_DRIVERS, sources=["left", "right"]),
        tmp_path / "data",
    )
    decision = _decision(pair)

    assert "x0" not in decision["solver_domain"]["planes"]
    sources = decision["sources"]["by_id"]
    assert sorted(sources) == ["left", "right"]
    assert sources["left"]["tag"] != sources["right"]["tag"]
    assert sources["left"]["tag"] == pair["source_tags"]["left"]
    assert sources["right"]["tag"] == pair["source_tags"]["right"]
    # Neither physical source is halved or replaced by the other's image.
    for item in sources.values():
        if item["retained_fraction"] is not None:
            assert item["retained_fraction"] * 2 ** len(decision["solver_domain"]["planes"]) == pytest.approx(1.0, abs=1e-6)
    summary = dd.decision_summary(pair)
    assert summary["sources"] == {
        "left": {"tag": sources["left"]["tag"], "retained_fraction": sources["left"]["retained_fraction"]},
        "right": {"tag": sources["right"]["tag"], "retained_fraction": sources["right"]["retained_fraction"]},
    }


# ------------------------------------------------------------------ one decision, every reader


def _job_request(record: dict[str, Any]) -> Any:
    from server.jobs.models import SolveRequest

    return SolveRequest.model_validate(
        {
            "geometry": {
                "type": "imported",
                "ingest_id": record["ingest_id"],
                "manifest_sha256": record["manifest_sha256"],
                "artifact_sha256": record["artifact_sha256"],
                "drive_channels": [
                    {"id": f"drive-{source['id']}", "source_ids": [source["id"]]}
                    for source in record["sources"]
                ],
                "mesh": record["mesh_sizes"],
            },
            "options": {
                "engine": "metal",
                "frequencies_hz": [100.0, 500.0],
                "polar_config": {"angle_range": [-180.0, 180.0, 37]},
            },
        }
    )


def test_preview_plan_preparation_and_job_consume_the_same_decision(tmp_path: Path) -> None:
    from server.cadlink import preparation
    from server.cadlink.solver_frame import confirm_frame
    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore
    from test_cad_preparation_solver_frame import _PreparedStore
    from test_imported_jobs import _PausedRegistry

    data_dir = tmp_path / "data"
    record = _ingest(_bundle(tmp_path, "every-reader", _horn, HORN_THROAT), data_dir)
    decision = _decision(record)
    summary = dd.decision_summary(record)
    assert summary is not None and summary["decision_sha256"] == decision["identity"]["decision_sha256"]
    assert summary["solver_domain"]["planes"] == record["symmetry"]["domain_planes"]

    # The model card.
    view = interpretation_view(_store(data_dir), record)
    assert view["decision"] == decision

    # The Solve card's plan.
    plan, outcomes = _solve_verdicts(record)
    assert plan["code"] is None and plan["domain_decision"] == summary
    assert outcomes["metal"] == "metal"

    # The preparation resumes this record, and only while its decision holds.
    row = {"preparation_id": "wgp_1"}
    identity = record_plan_identity(record)
    stored = json.loads(json.dumps(record))
    assert preparation._resumable(
        _PreparedStore(stored), row, "wgs_1", "sha256:s", "semantics", None, identity
    ) == stored
    drifted = copy.deepcopy(stored)
    drifted["domain_decision"]["solver_domain"]["planes"] = ["x0"]
    assert preparation._resumable(
        _PreparedStore(drifted), row, "wgs_1", "sha256:s", "semantics", None, identity
    ) is None

    # The job records the very summary the plan showed.
    confirm_frame(_store(data_dir), record, "+z")

    async def submit() -> dict[str, Any]:
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.db"),
            engine_registry=_PausedRegistry(),  # type: ignore[arg-type]
            cadlink_store=_store(data_dir),
        )
        try:
            job_id = await runtime.submit(_job_request(record))
            return runtime.store.get_job_row(job_id)
        finally:
            await runtime.shutdown()

    job = asyncio.run(submit())
    recorded = job["task_metadata"]["imported_geometry"]["domain_decision"]
    assert recorded == summary
    assert job["task_metadata"]["symmetry"]["cut_planes"] == summary["solver_domain"]["planes"]


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(lambda r: r["domain_decision"]["solver_domain"].update(planes=["x0"]), id="planes"),
        pytest.param(lambda r: r["domain_decision"].update(input_reading="cut"), id="hash"),
        pytest.param(lambda r: r["symmetry"].update(domain_planes=["x0"]), id="record-planes"),
        pytest.param(lambda r: r.update(mesh_content_sha256="sha256:" + "0" * 64), id="mesh"),
        pytest.param(
            lambda r: r["domain_interpretation"]["observations"].update(other_open_edges=7), id="observations"
        ),
        pytest.param(lambda r: r["normalisation"]["matrix"][0].__setitem__(3, 5.0), id="frame"),
        pytest.param(lambda r: r.update(domain_decision={"contract": "other"}), id="contract"),
    ],
)
def test_a_decision_that_does_not_describe_its_record_is_refused_everywhere(
    tmp_path: Path, tamper: Any
) -> None:
    from server.jobs.runtime import _imported_request_refusal

    record = _ingest(_bundle(tmp_path, "tamper", _horn, HORN_THROAT), tmp_path / "data")
    _decision(record)
    changed = json.loads(json.dumps(record))
    tamper(changed)
    problem = dd.decision_problem(changed)
    assert problem is not None
    request = _job_request(record)
    refusal = _imported_request_refusal(request, changed)
    assert refusal is not None and refusal[0] == dd.MISMATCH_CODE
    plan = _solve_verdicts_quiet(changed)
    assert plan == dd.MISMATCH_CODE


def _solve_verdicts_quiet(record: dict[str, Any]) -> str | None:
    try:
        plan, outcomes = _solve_verdicts(record)
    except Exception as exc:  # noqa: BLE001 - a planning refusal is the verdict
        return str(getattr(exc, "reason_code", type(exc).__name__))
    assert set(outcomes.values()) == {plan["code"]}, outcomes
    return plan["code"]


def test_a_record_from_an_earlier_build_has_no_decision_and_is_judged_as_before(tmp_path: Path) -> None:
    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(_bundle(tmp_path, "earlier", _open_half, HORN_THROAT), tmp_path / "data")
    decided = _imported_open_half_refusal(record)
    earlier = {key: value for key, value in record.items() if key != "domain_decision"}
    assert dd.decision_problem(earlier) is None
    assert dd.decision_summary(earlier) is None
    assert _imported_open_half_refusal(earlier) == decided


def test_the_decision_refusal_can_never_weaken_the_open_shell_check(tmp_path: Path) -> None:
    """A decision whose refusal were lost still meets the saved-observation check."""

    from server.jobs.runtime import _imported_open_half_refusal

    record = _ingest(_bundle(tmp_path, "belt", _open_half, HORN_THROAT), tmp_path / "data")
    silent = json.loads(json.dumps(record))
    silent["domain_decision"]["refusal"] = None
    assert _imported_open_half_refusal(silent) is not None
    # (And the tampered decision is itself refused as a mismatch first.)
    assert dd.decision_problem(silent) is not None


# ------------------------------------------------------------------ the source-edge filter


def _source_bordered_open_box() -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[int, str]]:
    """An open-backed box (x 0..60, open at x = 0) whose rim row is a source.

    The walls are a structured grid. In the first column of quads next to
    x = 0, the triangle holding the rim edge is tagged as a source; the other
    triangle of the quad, rigid, still reaches the rim vertex. So every rim
    vertex belongs to the rigid shell's component, and the rim's free edges
    are all source edges: after component separation only the source-edge
    filter keeps them out of the cut rim.
    """

    # Clear of y = 0 and z = 0, so no rim edge also lies on another plane.
    xs = np.linspace(0.0, 60.0, 7)
    ys = np.linspace(-40.0, 40.0, 9)
    zs = np.linspace(-90.0, -10.0, 9)
    points: list[tuple[float, float, float]] = []
    index: dict[tuple[float, float, float], int] = {}

    def vertex(x: float, y: float, z: float) -> int:
        key = (round(x, 9), round(y, 9), round(z, 9))
        if key not in index:
            index[key] = len(points)
            points.append(key)
        return index[key]

    triangles: list[tuple[int, int, int]] = []
    tags: list[int] = []
    rigid, source = 1, 101

    def wall(corner: Any, u_values: np.ndarray, v_values: np.ndarray, *, x_is_u: bool) -> None:
        for i in range(len(u_values) - 1):
            for j in range(len(v_values) - 1):
                a = vertex(*corner(u_values[i], v_values[j]))
                b = vertex(*corner(u_values[i], v_values[j + 1]))
                c = vertex(*corner(u_values[i + 1], v_values[j + 1]))
                d = vertex(*corner(u_values[i + 1], v_values[j]))
                rim_column = x_is_u and i == 0
                # (a, b) lies on x = 0 in the rim column.
                triangles.append((a, b, c))
                tags.append(source if rim_column else rigid)
                triangles.append((a, c, d))
                tags.append(rigid)

    # Four walls meeting x = 0, with x as their first parameter.
    wall(lambda x, y: (x, y, -10.0), xs, ys, x_is_u=True)
    wall(lambda x, y: (x, y, -90.0), xs, ys, x_is_u=True)
    wall(lambda x, z: (x, 40.0, z), xs, zs, x_is_u=True)
    wall(lambda x, z: (x, -40.0, z), xs, zs, x_is_u=True)
    # The back wall at x = 60 closes the box everywhere but x = 0.
    wall(lambda y, z: (60.0, y, z), ys, zs, x_is_u=False)
    return (
        np.asarray(points, dtype=float),
        np.asarray(triangles, dtype=np.int64),
        np.asarray(tags, dtype=np.int64),
        {source: "rim-source"},
    )


def test_source_edges_on_a_rigid_shells_rim_are_not_a_cut_after_component_separation() -> None:
    """Review finding (open shell, c/1): sensitive to the ``& rigid_free`` filter.

    Component separation alone cannot exclude these edges: their vertices are
    the rigid shell's. Removing the source-edge filter makes this read as a
    cut rim, and the open-shell refusal would fire on a model with no cut.
    """

    points, triangles, tags, source_tags = _source_bordered_open_box()
    observed = observe(points, triangles, tags, source_tags=source_tags, solver_from_assembly=np.eye(4))
    x0 = observed.planes["x0"]
    assert x0.rim_edges == 2 * (8 + 8) >= 3
    assert x0.positive > 0 and x0.negative == 0
    assert x0.rigid_cut_rim_edges == 0
    assert "rim-source" in x0.sources_bisected
    assert cut_shaped_open_rim({"observations": observed.to_json()}) is None

    # Control: the same mesh with nothing tagged a source is a spanning cut rim.
    untagged = observe(points, triangles, tags, source_tags={}, solver_from_assembly=np.eye(4))
    assert untagged.planes["x0"].rigid_cut_rim_edges == x0.rim_edges
    assert cut_shaped_open_rim({"observations": untagged.to_json()}) is not None

    # The rim vertices are the rigid shell's (so the component filter cannot
    # exclude these edges); only the four corners are reached by sources alone.
    rigid_vertices = set(triangles[tags != 101].ravel().tolist())
    rim = {i for i, point in enumerate(points) if abs(point[0]) < 1e-9}
    assert len(rim) == 32 and len(rim - rigid_vertices) <= 4
