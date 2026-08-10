"""What v2 does with a job imported from v1, row by real row.

Every fixture in ``fixtures/v1_jobs.json`` is a verbatim copy of a row from the
live v1 database -- the same rows ``scripts/migrate_v1.py`` moves -- with the
results and mesh blobs left behind. Synthetic rows would have agreed with
whatever the converter happened to do; these disagree, and that is the point:
they are the rows that exposed v1 storing the design twice and v2 reading the
lossy copy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.design.legacy_snapshot import resolve_legacy_params
from server.jobs.legacy_design import (
    DesignResolution,
    reset_design_cache,
    resolve_job_design,
)
from server.jobs.models import JobItem
from server.jobs.runtime import JobRuntime
from server.design.schema import DesignConfig


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "v1_jobs.json").read_text(encoding="utf-8")
)
BY_NAME = {row["_fixture"]: row for row in FIXTURES}


def _row(name: str) -> dict[str, Any]:
    """Decode a stored v1 row the way ``JobStore._row_to_job`` does."""

    fixture = BY_NAME[name]
    row = dict(fixture)
    row["run_number"] = 1
    row["parent_job_id"] = None
    row["config_json"] = json.loads(fixture["config_json"] or "{}")
    row["config_summary_json"] = json.loads(fixture["config_summary_json"] or "{}")
    row["script_snapshot"] = (
        json.loads(fixture["script_snapshot_json"]) if fixture["script_snapshot_json"] else None
    )
    row["task_metadata"] = json.loads(fixture["task_metadata_json"] or "{}")
    row["mesh_stats"] = None
    row["has_results"] = bool(fixture["has_results"])
    row["has_mesh_artifact"] = bool(fixture["has_mesh_artifact"])
    return row


def _resolve(name: str) -> DesignResolution:
    row = _row(name)
    return resolve_job_design(row["script_snapshot"], row["config_json"])


def _design(name: str) -> DesignConfig:
    resolution = _resolve(name)
    assert resolution.snapshot is not None
    return DesignConfig.model_validate(resolution.snapshot["design"])


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    reset_design_cache()


def test_the_fixtures_are_real_v1_rows_of_every_shape_that_exists() -> None:
    assert len(FIXTURES) == 7
    assert {row["_fixture"] for row in FIXTURES} == {
        "osse-formula-coverage-angle",
        "rosse-formula-superformula",
        "rosse-plain",
        "osse-enclosure",
        "freeform-with-snapshot",
        "rosse-no-snapshot",
        "freeform-no-snapshot",
    }
    # The 9-of-31 group whose script_snapshot_json is NULL, and the rest.
    without = [row for row in FIXTURES if not row["script_snapshot_json"]]
    assert len(without) == 2
    assert {row["status"] for row in FIXTURES} == {"complete", "error"}


@pytest.mark.parametrize(
    ("name", "source", "formula"),
    [
        ("osse-formula-coverage-angle", "v1-design-state", "OSSE"),
        ("rosse-formula-superformula", "v1-design-state", "R-OSSE"),
        ("rosse-plain", "v1-design-state", "R-OSSE"),
        ("osse-enclosure", "v1-design-state", "OSSE"),
        ("rosse-no-snapshot", "v1-mesher-payload", "R-OSSE"),
    ],
)
def test_a_recoverable_v1_job_arrives_as_a_native_v2_snapshot(
    name: str, source: str, formula: str
) -> None:
    resolution = _resolve(name)
    assert resolution.reopenable is True
    assert resolution.source == source
    assert resolution.reason_code == "recovered"
    assert resolution.reason is None
    # The shape the frontend already understands: no legacy branch anywhere.
    assert resolution.snapshot is not None
    assert resolution.snapshot["version"] == 1
    assert resolution.snapshot["design"]["formula"] == formula
    assert DesignConfig.model_validate(resolution.snapshot["design"]).formula == formula


def test_a_payload_recovered_job_states_what_the_payload_could_not_carry() -> None:
    resolution = _resolve("rosse-no-snapshot")
    assert resolution.note is not None
    assert "no design snapshot" in resolution.note
    assert "ATH passthrough blocks" in resolution.note
    # A design-state recovery is lossless, so it must not carry the caveat.
    assert _resolve("rosse-plain").note is None


def test_the_coverage_angle_formula_survives_reopening() -> None:
    """v1's prepared bag has no ``a`` at all for this row -- only ``Coverage.Angle``.

    Reading that copy produced an OSSE design with no coverage angle, which is
    not a design at all. The regression this guards is silent: the job still
    listed, still opened, and drew a different waveguide.
    """

    design = _design("osse-formula-coverage-angle")
    assert design.root.a is not None
    assert design.root.a.raw == "45 - 16*cos(1*p)^2 -40*sin(p*1)^16"
    prepared = BY_NAME["osse-formula-coverage-angle"]
    prepared_params = json.loads(prepared["script_snapshot_json"])["params"]
    assert "a" not in prepared_params


def test_the_r_osse_superformula_fields_survive_reopening() -> None:
    """Same defect, worse consequence: an R-OSSE with no ``R`` has no radius."""

    design = _design("rosse-formula-superformula")
    for field, expected in (
        ("R", "160 * (abs(cos(p)/1.8)^3 + abs(sin(p)/1)^4)^(-1/7)"),
        ("a", "22 * (abs(cos(p)/1.2)^8 + abs(sin(p)/1)^4)^(-1/4)"),
        ("k", "4 * (abs(cos(p)/1.2)^8 + abs(sin(p)/1)^4)^(-1/4)"),
    ):
        value = getattr(design.root, field)
        assert value is not None, field
        assert value.raw == expected
    prepared = json.loads(BY_NAME["rosse-formula-superformula"]["script_snapshot_json"])["params"]
    assert not {"R", "a", "k"} & set(prepared)


def test_the_authoritative_bag_is_v1s_own_design_state() -> None:
    snapshot = json.loads(BY_NAME["rosse-formula-superformula"]["script_snapshot_json"])
    params, source = resolve_legacy_params(snapshot)
    assert source == "design-state"
    assert params["type"] == "R-OSSE"
    # stateSnapshot.params carries no family of its own; reading it unmerged
    # would send a FREEFORM design down the OSSE branch.
    assert "type" not in snapshot["stateSnapshot"]["params"]


def test_a_snapshot_without_a_state_snapshot_still_reads_its_prepared_bag() -> None:
    params, source = resolve_legacy_params({"params": {"type": "OSSE", "L": 120}})
    assert source == "prepared-params"
    assert params["L"] == 120
    params, source = resolve_legacy_params({"type": "OSSE", "L": 120})
    assert source == "params-bag"
    assert params["L"] == 120


@pytest.mark.parametrize("name", ["freeform-with-snapshot", "freeform-no-snapshot"])
def test_a_freeform_v1_job_is_refused_by_name_not_by_silence(name: str) -> None:
    resolution = _resolve(name)
    assert resolution.reopenable is False
    assert resolution.reason_code == "freeform_legacy_design"
    assert resolution.reason is not None
    assert "FREEFORM" in resolution.reason
    assert "Re-enter its profiles" in resolution.reason
    # Nothing is invented in place of the design that could not be read.
    assert resolution.snapshot is None


@pytest.mark.parametrize("family", ["ICW", "LOOKUP"])
def test_an_unsupported_v1_family_surfaces_an_actionable_refusal(family: str) -> None:
    resolution = resolve_job_design({"params": {"type": family}}, None)
    assert resolution.reopenable is False
    assert resolution.reason_code == "unreadable_design"
    assert resolution.snapshot is None
    assert resolution.reason is not None
    assert family in resolution.reason
    assert "must be re-entered" in resolution.reason
    assert "cannot be reopened or rerun" in resolution.reason


def test_a_row_with_neither_a_design_nor_a_payload_says_so() -> None:
    resolution = resolve_job_design(None, {"options": {}})
    assert resolution.reopenable is False
    assert resolution.reason_code == "no_stored_design"
    assert "predates design snapshots" in (resolution.reason or "")
    assert resolve_job_design(None, None).reason_code == "no_stored_design"


@pytest.mark.parametrize(
    "snapshot",
    [
        {"params": "this row's design is not a mapping"},
        {"params": {"type": "OSSE", "L": 120, "_blocks": "corrupted"}},
        {"params": {"type": "OSSE", "_blocks": {"Report": {"_items": "corrupted"}}}},
    ],
)
def test_an_unreadable_design_degrades_instead_of_raising(snapshot: dict[str, Any]) -> None:
    resolution = resolve_job_design(snapshot, None)
    assert resolution.reopenable is False
    assert resolution.reason_code == "unreadable_design"
    assert "could not be read back" in (resolution.reason or "")


def test_a_native_v2_snapshot_is_passed_through_untouched() -> None:
    wire = {"formula": "OSSE", "L": 120, "a": 45, "a0": 10, "r0": 12.7, "k": 1}
    snapshot = {"version": 1, "design": wire}
    resolution = resolve_job_design(snapshot, None)
    assert resolution.reopenable is True
    assert resolution.source == "v2-snapshot"
    assert resolution.reason_code == "ok"
    assert resolution.snapshot == snapshot
    # The pre-versioned v2 shape the frontend also still accepts.
    assert resolve_job_design(wire, None).source == "v2-snapshot"


def test_conversion_is_memoised_on_the_stored_bytes() -> None:
    row = _row("rosse-formula-superformula")
    first = resolve_job_design(row["script_snapshot"], row["config_json"])
    second = resolve_job_design(row["script_snapshot"], row["config_json"])
    assert first is second
    reset_design_cache()
    assert resolve_job_design(row["script_snapshot"], row["config_json"]) is not first


@pytest.mark.parametrize(
    ("name", "reopenable"),
    [
        ("rosse-formula-superformula", True),
        ("rosse-no-snapshot", True),
        ("freeform-with-snapshot", False),
        ("freeform-no-snapshot", False),
    ],
)
def test_the_job_item_a_client_receives_carries_the_verdict(name: str, reopenable: bool) -> None:
    item = JobItem.model_validate(JobRuntime._serialize_job(_row(name)))
    assert item.design_availability.reopenable is reopenable
    if reopenable:
        assert item.script_snapshot is not None
        assert item.script_snapshot["version"] == 1
        assert item.design_availability.reason is None
    else:
        assert item.design_availability.reason
        # The untranslatable original is still shipped, not dropped: it is the
        # only surviving record of a design that must be re-entered by hand.
        stored = BY_NAME[name]["script_snapshot_json"]
        assert (item.script_snapshot is not None) is bool(stored)


# Sibling to this checkout, the way every other v1-corpus test locates it.
V1_DB = (
    Path(__file__).resolve().parents[2].parent
    / "Waveguide Generator"
    / "server"
    / "data"
    / "simulations.db"
)


def _live_rows() -> list[dict[str, Any]]:
    import sqlite3

    connection = sqlite3.connect(f"file:{V1_DB}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "select config_json, script_snapshot_json from simulation_jobs"
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "config_json": json.loads(row["config_json"] or "{}"),
            "script_snapshot": (
                json.loads(row["script_snapshot_json"]) if row["script_snapshot_json"] else None
            ),
        }
        for row in rows
    ]


@pytest.mark.skipif(not V1_DB.exists(), reason="v1 simulation database is not available")
def test_every_job_in_the_live_v1_database_is_recovered_or_explained() -> None:
    """The release claim, checked against the whole corpus rather than a sample.

    Deliberately not a count: this is a live working history that grows and
    shrinks as jobs are run and deleted, and pinning a number made the suite
    fail the first time one was removed. What has to hold is that no row lands
    in a third state -- silently unopenable, with nothing to tell the user.
    """

    verdicts = [
        resolve_job_design(row["script_snapshot"], row["config_json"]) for row in _live_rows()
    ]
    assert verdicts, "the v1 database exists but holds no jobs"
    for verdict in verdicts:
        if verdict.reopenable:
            assert verdict.snapshot is not None
            assert DesignConfig.model_validate(verdict.snapshot["design"])
        else:
            assert verdict.reason
            # Nothing in the real corpus is merely broken; every refusal is a
            # known, named cause the user can act on.
            assert verdict.reason_code in {"freeform_legacy_design", "no_stored_design"}
    sources = {verdict.source for verdict in verdicts}
    assert {"v1-design-state", "v1-mesher-payload"} <= sources, sources


def test_a_recovered_design_meshes_end_to_end() -> None:
    """legacy bag -> ATH text -> textcfg.parse -> DesignConfig -> preview geometry."""

    from hornlab_mesher.preview.api import build_preview_geometry

    from server.preview.core import preview_options
    from server.preview.translate import design_to_mesher_config

    for name in ("rosse-plain", "rosse-no-snapshot"):
        geometry = build_preview_geometry(
            design_to_mesher_config(_design(name)), preview_options("coarse")
        )
        assert geometry.surfaces
        assert sum(len(surface.positions) for surface in geometry.surfaces) > 0
