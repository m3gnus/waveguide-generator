"""M1c-auto without geometry: the manifest contract, the detector's rules, the
frame rule, approvals, the excitation gate and the Change route.

The geometry fixtures (real gmsh, real ingest) are
``test_cadlink_domain_automatic.py``.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from server.cadlink import domain_interpretation as di
from server.cadlink import ingest as ingest_module
from server.cadlink import preparation
from server.cadlink.fusion_delivery import capabilities
from server.cadlink.preparation import PreparationInput, prepare_operation
from server.cadlink.solver_frame import AXES, allowed_axes, axes_in_planes, confirm_frame
from server.cadlink.wgreturn import WgReturnValidationError, validate_manifest
from test_cad_preparation import Harness, _revision, _setup
from test_cad_preparation_design_gate import MesherStandIn
from test_cad_preparation_solver_frame import _authored, _received, _with_degraded_skip


REPOSITORY = Path(__file__).resolve().parents[2]
AUTOMATIC = "domain-automatic-v1"


def _unlinked() -> dict[str, Any]:
    return _authored(b"STEP")


def _new_add_in(manifest: dict[str, Any] | None = None, *, cut: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What a new add-in writes: an explicit automatic domain under its feature."""

    manifest = copy.deepcopy(manifest or _unlinked())
    manifest["required_features"].append(AUTOMATIC)
    manifest["assembly"]["domain"] = {"kind": "automatic"}
    if cut is not None:
        manifest["assembly"]["cut_provenance"] = cut
    return manifest


def _cut(**changes: Any) -> dict[str, Any]:
    entry = {
        "body_object_id": "speaker",
        "feature": {"kind": "split-body", "name": "Split Body 3"},
        "tool": {"kind": "origin-plane", "origin_plane": "YZ"},
        "plane": "x0",
        "kept_side": "positive",
        "export_frame": "root-component",
    }
    entry.update(changes)
    return entry


# -- the manifest contract -------------------------------------------------------------------


def test_a_new_add_in_manifest_with_provenance_validates() -> None:
    manifest = _new_add_in(cut=[_cut(), _cut(plane="y0", tool={"kind": "construction-plane", "origin_plane": "XZ"})])

    validated = validate_manifest(manifest)

    assert validated["assembly"]["domain"] == {"kind": "automatic"}
    assert len(validated["assembly"]["cut_provenance"]) == 2


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        # The feature and the automatic domain are paired both ways.
        (lambda m: m["required_features"].remove(AUTOMATIC), "domain-automatic-v1 is required exactly when"),
        (lambda m: m["assembly"].pop("domain"), "domain-automatic-v1 is required exactly when"),
        (lambda m: m["assembly"]["domain"].update(cut_planes=["x0"]), "states nothing else"),
        (lambda m: m["assembly"]["cut_provenance"][0].update(body_object_id="elsewhere"), "must name a \\$.scope.included body"),
        (lambda m: m["assembly"]["cut_provenance"][0].update(plane="y0"), "the YZ plane is x0, not y0"),
        (lambda m: m["assembly"]["cut_provenance"][0].update(kept_side="both"), "kept_side: must be one of"),
        (lambda m: m["assembly"]["cut_provenance"][0]["feature"].update(kind="loft"), "feature.kind: must be one of"),
        (lambda m: m["assembly"]["cut_provenance"][0].update(export_frame="world"), "export_frame: must be one of"),
        (lambda m: m["assembly"]["cut_provenance"][0].update(signature="x"), "unknown member"),
    ],
)
def test_the_automatic_domain_contract_refuses_what_it_does_not_define(edit, message) -> None:
    manifest = _new_add_in(cut=[_cut()])
    edit(manifest)

    with pytest.raises(WgReturnValidationError, match=message):
        validate_manifest(manifest)


def test_cut_provenance_needs_the_automatic_domain() -> None:
    manifest = _unlinked()
    manifest["assembly"]["cut_provenance"] = [_cut()]

    with pytest.raises(WgReturnValidationError, match="only with domain-automatic-v1"):
        validate_manifest(manifest)


def test_an_old_add_in_manifest_still_validates_and_declares_nothing() -> None:
    validated = validate_manifest(_unlinked())

    assert "domain" not in validated["assembly"]
    plan = di.resolve_domain_plan(None, validated, "sha256:" + "0" * 64)
    assert (plan.manifest_domain, plan.source, plan.identity()) == ("absent", None, None)


def _old_wg_reader(tmp_path: Path) -> Any:
    """``wgreturn.py`` as WG shipped it before M1c-auto (the branch's base)."""

    text = subprocess.run(
        ["git", "-C", str(REPOSITORY), "show", "d55edb4e:server/cadlink/wgreturn.py"],
        check=True, capture_output=True, text=True,
    ).stdout
    path = tmp_path / "old_wgreturn.py"
    path.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("server.cadlink.old_wgreturn", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_new_add_in_is_refused_visibly_by_an_old_wg(tmp_path: Path) -> None:
    old = _old_wg_reader(tmp_path)
    assert AUTOMATIC not in old.SUPPORTED_FEATURES

    with pytest.raises(old.WgReturnValidationError, match="unknown required feature\\(s\\): domain-automatic-v1"):
        old.validate_manifest(_new_add_in(cut=[_cut()]))
    # Even a writer that forgot the feature is refused, never read as a declaration.
    forgot = _new_add_in()
    forgot["required_features"].remove(AUTOMATIC)
    with pytest.raises(old.WgReturnValidationError, match="cut_planes: is required"):
        old.validate_manifest(forgot)
    # Positive control: the old reader takes an old add-in's manifest.
    assert old.validate_manifest(_unlinked())["sources"]


def test_wg_advertises_the_automatic_domain_to_the_add_in() -> None:
    assert capabilities()["automaticDomain"] == 1


# -- the frame rule --------------------------------------------------------------------------


def test_automatic_is_not_a_declared_domain_for_the_frame() -> None:
    """Before M1c-auto any truthy domain object forced +Z."""

    automatic = validate_manifest(_new_add_in())
    declared = validate_manifest(_unlinked())
    declared["assembly"]["domain"] = {"kind": "half", "cut_planes": ["x0"], "declared_by": "cad-author"}

    assert allowed_axes(automatic) == AXES
    assert allowed_axes(validate_manifest(_unlinked())) == AXES
    assert allowed_axes(declared) == ("+z",)


def test_validated_precut_planes_restrict_the_axes_and_reduced_stays_plus_z() -> None:
    assert axes_in_planes(["x0"]) == ("+z", "-z", "+y", "-y")
    assert axes_in_planes(["y0"]) == ("+z", "-z", "+x", "-x")
    assert axes_in_planes(["x0", "y0"]) == ("+z", "-z")
    automatic = validate_manifest(_new_add_in())
    assert allowed_axes(automatic, ["x0"]) == ("+z",)
    assert allowed_axes(automatic, ["x0", "y0"]) == ("+z",)


# -- the plan --------------------------------------------------------------------------------


def test_provenance_on_the_negative_side_or_z0_is_refused_before_meshing() -> None:
    negative = di.resolve_domain_plan(None, validate_manifest(_new_add_in(cut=[_cut(kept_side="negative")])), "s")
    assert negative.refusal is not None and "keep the x ≥ 0 side and leave the cut open" in negative.refusal
    top = di.resolve_domain_plan(
        None,
        validate_manifest(_new_add_in(cut=[_cut(plane="z0", tool={"kind": "origin-plane", "origin_plane": "XY"})])),
        "s",
    )
    assert top.refusal is not None and "cannot mirror yet" in top.refusal
    # Positive control.
    ok = di.resolve_domain_plan(None, validate_manifest(_new_add_in(cut=[_cut()])), "s")
    assert (ok.refusal, ok.source, ok.planes, ok.strict) == (None, di.PROVENANCE, ("x0",), True)


def test_provenance_recorded_in_another_frame_is_not_this_snapshots_evidence() -> None:
    manifest = validate_manifest(_new_add_in(cut=[_cut(export_frame="selected-occurrence-component")]))

    plan = di.resolve_domain_plan(None, manifest, "s")

    assert plan.source is None and plan.evidenced_planes == ()
    assert "frame" in plan.ignored[0]["reason"]


def test_a_users_reading_wins_over_provenance(tmp_path: Path) -> None:
    from server.cadlink.store import CadLinkStore

    store = CadLinkStore(tmp_path / "cadlink.db")
    manifest = validate_manifest(_new_add_in(cut=[_cut()]))
    key = di.reading_key(store, manifest, "sha256:this")
    store.record_domain_reading(key, {"source": "user", "reading": "as-shown", "planes": [], "snapshot": "sha256:this"})

    plan = di.resolve_domain_plan(store, manifest, "sha256:this")

    assert (plan.source, plan.reading, plan.evidenced_planes) == (di.USER, "as-shown", ())
    # With no project, a reading is keyed by its exact snapshot and carries to
    # no other: another snapshot reads its own provenance.
    assert di.resolve_domain_plan(store, manifest, "sha256:other").source == di.PROVENANCE


def test_a_lineage_reading_does_not_carry_to_other_bodies(tmp_path: Path) -> None:
    from server.cadlink.store import CadLinkStore

    store = CadLinkStore(tmp_path / "cadlink.db")
    first = validate_manifest(_new_add_in())
    key = di.reading_key(store, first, "sha256:first")
    store.record_domain_reading(
        key,
        {
            "source": "user",
            "reading": "reduced",
            "planes": ["x0"],
            "snapshot": "sha256:first",
            "body_object_ids": ["speaker"],
            "export_frame": "root-component",
        },
    )
    other = copy.deepcopy(first)
    other["scope"]["included"][0]["object_id"] = "another-body"

    plan = di.resolve_domain_plan(store, other, "sha256:later")

    assert plan.source is None
    assert plan.evidenced_planes == ()


def test_reconstruction_requires_a_clean_self_intersection_report() -> None:
    clean = {
        "integrity": {
            "self_intersection": {
                "checked": True,
                "proper_crossing_count": 0,
                "coplanar_overlap_count": 0,
            }
        }
    }
    crossing = copy.deepcopy(clean)
    crossing["integrity"]["self_intersection"]["proper_crossing_count"] = 34

    assert ingest_module._reconstruction_integrity_problem(clean) is None
    assert "34 crossing" in ingest_module._reconstruction_integrity_problem(crossing)
    assert "unavailable" in ingest_module._reconstruction_integrity_problem({})


# -- the detector on synthetic meshes ----------------------------------------------------------


def _open_box(*, open_x0: bool = True, source_touches: bool = True, cap: bool = False, slot: bool = False, x0: float = 0.0):
    """A unit-ish box [x0, x0+2] x [-1, 1] x [-2, 0], triangulated per face, with a
    source patch on the top face (z = 0): at the x = x0 edge, or clear of it."""

    points: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    tags: list[int] = []

    def quad(a, b, c, d, tag=1):
        base = len(points)
        points.extend([a, b, c, d])
        triangles.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
        tags.extend([tag, tag])

    x1 = x0 + 2.0
    # Bottom, back (y=-1), front (y=1), far wall (x = x1).
    quad((x0, -1, -2), (x1, -1, -2), (x1, 1, -2), (x0, 1, -2))
    quad((x0, -1, -2), (x0, -1, 0), (x1, -1, 0), (x1, -1, -2))
    quad((x0, 1, -2), (x1, 1, -2), (x1, 1, 0), (x0, 1, 0))
    quad((x1, -1, -2), (x1, -1, 0), (x1, 1, 0), (x1, 1, -2))
    # Top at z = 0: a source strip, either along the x0 edge or in the middle.
    if source_touches:
        quad((x0, -1, 0), (x0 + 0.5, -1, 0), (x0 + 0.5, 1, 0), (x0, 1, 0), tag=101)
        quad((x0 + 0.5, -1, 0), (x1, -1, 0), (x1, 1, 0), (x0 + 0.5, 1, 0))
    else:
        quad((x0, -1, 0), (x0 + 0.5, -1, 0), (x0 + 0.5, 1, 0), (x0, 1, 0))
        quad((x0 + 0.5, -1, 0), (x0 + 1.0, -1, 0), (x0 + 1.0, 1, 0), (x0 + 0.5, 1, 0), tag=101)
        quad((x0 + 1.0, -1, 0), (x1, -1, 0), (x1, 1, 0), (x0 + 1.0, 1, 0))
    if cap or not open_x0:
        quad((x0, -1, -2), (x0, 1, -2), (x0, 1, 0), (x0, -1, 0))
    if slot:
        # A hole in the far wall: two triangles of it removed.
        del triangles[6:8]
        del tags[6:8]
    points_array = np.asarray(points, dtype=float) * 1000.0
    # Weld coincident corners so shared edges are shared.
    unique, inverse = np.unique(np.round(points_array, 6), axis=0, return_inverse=True)
    faces = inverse.reshape(-1)[np.asarray(triangles)]
    return unique, faces, np.asarray(tags)


def _observe(points, faces, tags, matrix=None, cut=()):
    return di.observe(
        points, faces, tags,
        source_tags={101: "throat"},
        solver_from_assembly=np.eye(4) if matrix is None else matrix,
        wg_cut_planes=cut,
    )


def _status(observations, plane="x0"):
    return di.conclude(observations.planes[plane], other_open_edges=observations.other_open_edges)


def test_a_clean_open_half_with_its_source_on_the_plane_is_a_candidate() -> None:
    observations = _observe(*_open_box())
    assert _status(observations) == ("candidate", [])
    assert di.valid_choices(observations) == [("x0",)]


def test_the_detector_separates_what_makes_a_rim_ambiguous() -> None:
    assert _status(_observe(*_open_box(source_touches=False))) == ("ambiguous", ["no-source-on-plane"])
    assert _status(_observe(*_open_box(slot=True))) == ("ambiguous", ["other-openings"])
    # A closed box on the plane is not a cut; with the source bisected by it,
    # it may be a capped one.
    closed = _observe(*_open_box(open_x0=False, source_touches=False))
    assert _status(closed) == ("none", [])


def test_a_closed_box_off_centre_is_not_evidence_of_anything() -> None:
    observations = _observe(*_open_box(open_x0=False, x0=5.0))
    assert {plane: _status(observations, plane)[0] for plane in ("x0", "y0")} == {"x0": "none", "y0": "straddles"}
    assert di.valid_choices(observations) == []


def test_the_detector_reports_planes_in_the_cad_frame() -> None:
    """Meshed in the +x frame (solver = R cad), a CAD x = 0 cut is still x0."""

    points, faces, tags = _open_box()
    rotation = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=float)  # solver from CAD
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    solver_points = points @ rotation.T
    observations = _observe(solver_points, faces, tags, matrix)
    assert _status(observations, "x0") == ("candidate", [])
    assert observations.planes["x0"].positive > 0 and observations.planes["x0"].negative == 0


def _plan(source: str, planes=("x0",)) -> di.DomainPlan:
    return di.DomainPlan(manifest_domain="automatic", source=source, reading="reduced", planes=planes)


def test_evidence_is_applied_only_where_it_revalidates() -> None:
    clean = _observe(*_open_box())
    assert di.apply_evidence(_plan(di.PROVENANCE), clean).applied == ("x0",)
    capped = _observe(*_open_box(cap=True))
    refused = di.apply_evidence(_plan(di.PROVENANCE), capped)
    assert refused.applied == () and "capped" in refused.refusal
    # Lineage evidence that no longer fits is set aside, never a refusal.
    lenient = di.apply_evidence(_plan(di.LINEAGE), capped)
    assert (lenient.applied, lenient.refusal) == ((), None)
    # A plane the model now spans is not a cut: not applied, whatever the source.
    whole = _observe(*_open_box(open_x0=False, x0=-1.0))
    spans = di.apply_evidence(_plan(di.PROVENANCE), whole)
    assert (spans.applied, spans.refusal) == ((), None)
    assert "spans both sides" in spans.not_applicable["x0"]
    # An unvalidated source identity refuses this snapshot's own evidence.
    assert di.apply_evidence(_plan(di.USER), clean, identity_problem="source s was not found").refusal


def test_evidence_is_refused_when_the_cut_is_not_the_only_opening() -> None:
    """A mirrored half with another hole would leak through it (and its image)."""

    leaking = _observe(*_open_box(slot=True))
    refused = di.apply_evidence(_plan(di.PROVENANCE), leaking)
    assert refused.applied == () and "other open edge" in refused.refusal
    assert di.valid_choices(leaking) == []
    # Control: the same box without the hole takes the evidence.
    assert di.apply_evidence(_plan(di.PROVENANCE), _observe(*_open_box())).applied == ("x0",)


def test_no_evidence_applies_nothing_even_to_a_clean_candidate() -> None:
    clean = _observe(*_open_box())
    none = di.DomainPlan(manifest_domain="automatic")
    assert di.apply_evidence(none, clean).applied == ()
    record = di.interpretation_record(none, clean, {"cut_planes": [], "domain_planes": []})
    assert (record["reading"], record["looks_cut"], record["planes"]) == ("as-shown", ["x0"], [])


def test_the_tolerance_is_the_meshers_cut_tolerance() -> None:
    from server.mesh.imported import SYMMETRY_SNAP_TOLERANCE_MM

    assert di.TOLERANCE_MM == SYMMETRY_SNAP_TOLERANCE_MM


# -- acoustic compatibility ----------------------------------------------------------------------


def test_a_mirrored_domain_takes_only_a_reflection_invariant_excitation() -> None:
    reduced = {"symmetry": {"domain_planes": ["x0"]}}
    normal = SimpleNamespace(id="lf", source_ids=["lf"], motion="normal")
    axial = SimpleNamespace(id="hf", source_ids=["hf"], motion="axial")
    assert di.excitation_problem(reduced, [normal, axial]) is None
    twisted = SimpleNamespace(id="mf", source_ids=["mf"], motion="tangential")
    assert "not the same under the mirror" in di.excitation_problem(reduced, [normal, twisted])
    doubled = SimpleNamespace(id="lf2", source_ids=["lf"], motion="normal")
    assert "driven by two channels" in di.excitation_problem(reduced, [normal, doubled])
    # A full domain has no mirror to be incompatible with.
    assert di.excitation_problem({"symmetry": {"domain_planes": []}}, [twisted]) is None


# -- preparation: approvals and the excitation gate ----------------------------------------------


@pytest.fixture
def real(tmp_path, monkeypatch):
    harness = Harness(tmp_path)
    mesher = MesherStandIn()
    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesher)
    return harness, mesher


def _context(harness):
    return dataclasses.replace(harness.context(), ingest=ingest_module.ingest_bundle)


def _prepare(harness, **kwargs):
    kwargs.setdefault("setup_revision_id", _revision(harness.store, _setup()))
    return asyncio.run(prepare_operation(_context(harness), "cmd-1", PreparationInput(**kwargs)))


def _record(harness, summary):
    return json.loads(harness.store.get_ingest(str(summary["preparationId"]))["record_json"])


def _blocking(record):
    return tuple(finding["id"] for finding in record["findings"] if finding.get("blocking"))


def test_a_changed_interpretation_is_a_new_preparation_and_approvals_do_not_carry(real) -> None:
    harness, mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _with_degraded_skip(_new_add_in(_authored(step))), step)
    waiting = _prepare(harness)
    confirm_frame(harness.store, _record(harness, waiting), "+z")
    review = _prepare(harness)
    assert (review["state"], review["reason"]) == ("needs_user_input", "findings_need_review"), review
    blocking = _blocking(_record(harness, review))

    # The user reads the model differently before approving: the approval they
    # then send names the old preparation.
    record = _record(harness, review)
    harness.store.record_domain_reading(
        di.record_key(record),
        {"source": "user", "reading": "as-shown", "planes": [], "snapshot": record["manifest_sha256"]},
    )
    again = _prepare(
        harness, approve_preparation_id=review["preparationId"], approve_finding_ids=blocking
    )

    assert again["preparationId"] != review["preparationId"]
    assert (again["state"], again["reason"]) == ("needs_user_input", "findings_need_review"), again
    assert harness.submitted == []
    assert _record(harness, again)["domain_interpretation"]["plan"]["source"] == "user"
    assert len(mesher.calls) == 2
    # Control: approving the new preparation solves it, once.
    solved = _prepare(
        harness,
        approve_preparation_id=again["preparationId"],
        approve_finding_ids=_blocking(_record(harness, again)),
    )
    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved
    assert len(mesher.calls) == 2


def test_an_unchanged_interpretation_resumes_the_preparation(real) -> None:
    harness, mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _new_add_in(_authored(step)), step)
    waiting = _prepare(harness)
    confirm_frame(harness.store, _record(harness, waiting), "+z")

    solved = _prepare(harness)

    assert solved["preparationId"] == waiting["preparationId"]
    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved
    assert len(mesher.calls) == 1


def test_a_known_excitation_incompatibility_stops_the_solve(real, monkeypatch) -> None:
    harness, _mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _new_add_in(_authored(step)), step)
    confirm_frame(harness.store, _record(harness, _prepare(harness)), "+z")
    seen = []

    def incompatible(record, channels):
        seen.append([channel.id for channel in channels])
        return "Drive channel mf moves its sources 'tangential', which is not the same under the mirror."

    monkeypatch.setattr(preparation, "excitation_problem", incompatible)
    stopped = _prepare(harness)

    assert (stopped["state"], stopped["reason"]) == ("needs_user_input", "submission_refused"), stopped
    assert "not the same under the mirror" in stopped["message"]
    assert harness.submitted == [] and seen and seen[0]


# -- the Change route ---------------------------------------------------------------------------


def _interpreted_record(**interpretation: Any) -> dict[str, Any]:
    from test_cad_solver_frame_api import _record as frame_record

    record = frame_record()
    record["ingest_id"] = "wgi_01J5A8QK3M9T2XVBH0RD7NWE00"
    record["domain_interpretation"] = {
        "contract": di.CONTRACT,
        "reading": "as-shown",
        "planes": [],
        "looks_cut": ["x0"],
        "ambiguous": [],
        "evidence": {"source": None},
        "choices": [{"reading": "reduced", "planes": ["x0"]}],
        **interpretation,
    }
    return record


def test_change_records_an_offered_reading_and_refuses_one_not_offered(tmp_path, monkeypatch) -> None:
    from fastapi import FastAPI

    from server.cadlink.domain_interpretation_api import router
    from server.cadlink.store import CadLinkStore
    from test_cad_solver_frame_api import _Client

    record = _interpreted_record()
    monkeypatch.setattr("server.cadlink.solver_frame_api.get_ingestion_record", lambda _store, _id: record)
    app = FastAPI()
    app.state.cadlink_store = CadLinkStore(tmp_path / "cadlink.db")
    app.include_router(router)
    client = _Client(app)

    shown = client.get("/api/cadlink/domain-interpretation", {"ingestId": record["ingest_id"]})
    assert shown.status_code == 200
    assert shown.json()["interpretation"]["looks_cut"] == ["x0"] and shown.json()["pending"] is None

    refused = client.put("/api/cadlink/domain-interpretation", {"ingestId": record["ingest_id"], "reading": "reduced", "planes": ["y0"]})
    assert refused.status_code == 422, refused.text
    malformed = client.put("/api/cadlink/domain-interpretation", {"ingestId": record["ingest_id"], "reading": "reduced"})
    assert malformed.status_code == 422

    changed = client.put("/api/cadlink/domain-interpretation", {"ingestId": record["ingest_id"], "reading": "reduced", "planes": ["x0"]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["pending"] == {"reading": "reduced", "planes": ["x0"]}
    assert changed.json()["remembered"]["source"] == "user"
    assert changed.json()["remembered"]["snapshot"] == record["manifest_sha256"]


def test_change_is_refused_for_a_declared_domain(tmp_path) -> None:
    from server.cadlink.store import CadLinkStore

    record = _interpreted_record(evidence={"source": "declaration"}, reading="reduced", planes=["x0"], choices=[])
    with pytest.raises(di.DomainReadingError, match="declares its domain in Fusion"):
        di.record_reading(CadLinkStore(tmp_path / "cadlink.db"), record, {"reading": "as-shown"})
