"""Recovering a design from v1's prepared mesher payload.

This is the fallback for the rows whose ``script_snapshot_json`` is NULL. The
cross-check against the design-state path is what makes it trustworthy: on
every live v1 job that stored *both*, the two independent routes have to land
on the same geometry, or the rename table is wrong.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.design.legacy_payload import (
    job_config_payload,
    payload_to_design,
    payload_to_params,
)
from server.design.legacy_snapshot import LegacySnapshotError, snapshot_to_design


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "v1_jobs.json").read_text(encoding="utf-8")
)
BY_NAME = {row["_fixture"]: row for row in FIXTURES}
V1_DB = (
    Path(__file__).resolve().parents[2].parent
    / "Waveguide Generator"
    / "server"
    / "data"
    / "simulations.db"
)


def _payload(name: str) -> dict[str, Any]:
    payload = job_config_payload(json.loads(BY_NAME[name]["config_json"]))
    assert payload is not None
    return dict(payload)


def test_a_snapshot_less_row_still_carries_the_design_it_was_solved_with() -> None:
    design = payload_to_design(_payload("rosse-no-snapshot")).design
    assert design.formula == "R-OSSE"
    assert design.root.R is not None and design.root.R.value == pytest.approx(140)
    assert design.root.r0 is not None and design.root.r0.value == pytest.approx(12.7)
    assert design.root.a0 is not None and design.root.a0.value == pytest.approx(15.5)
    assert design.root.q is not None and design.root.q.value == pytest.approx(3.4)
    assert design.root.mesh.angular_segments is not None
    assert design.root.mesh.angular_segments.value == pytest.approx(40)


def test_the_payload_is_v1s_own_bag_renamed_not_the_meshers_nested_config() -> None:
    params = payload_to_params(_payload("rosse-no-snapshot"))
    assert params["type"] == "R-OSSE"
    assert params["angularSegments"] == 40
    assert params["encFrontResolution"] == "25,25,25,25"
    # v1 and the bag agree that 2 is a flat disc; only the mesher renumbers it.
    assert params["sourceShape"] == 1
    # Nulls mean "v1 sent no value", which is what an absent key means here.
    assert "samplingMode" not in params
    assert "zMapPoints" not in params


def test_an_unrecognised_payload_field_is_named_rather_than_dropped() -> None:
    """A new mesher field must break loudly, not migrate silently as a default."""

    payload = dict(_payload("rosse-no-snapshot"), some_new_mesher_field=3)
    with pytest.raises(LegacySnapshotError, match="some_new_mesher_field"):
        payload_to_params(payload)


def test_a_payload_without_a_family_is_refused() -> None:
    payload = {key: value for key, value in _payload("rosse-no-snapshot").items() if key != "formula_type"}
    with pytest.raises(LegacySnapshotError, match="no formula_type"):
        payload_to_params(payload)


def test_a_freeform_payload_is_refused_like_a_freeform_snapshot() -> None:
    with pytest.raises(LegacySnapshotError, match="FREEFORM"):
        payload_to_design(_payload("freeform-no-snapshot"))


def test_job_config_payload_tolerates_every_missing_level() -> None:
    assert job_config_payload(None) is None
    assert job_config_payload({}) is None
    assert job_config_payload({"options": None}) is None
    assert job_config_payload({"options": {"mesh": None}}) is None
    assert job_config_payload({"options": {"mesh": {"waveguide_params": []}}}) is None


# The design state carries editor-only material the payload never received, and
# spells one boolean differently. Everything else has to agree exactly.
_EXPECTED_DIVERGENCE = {
    "comments",
    "extra_blocks",
    "extra_keys",
    "output",
    "scale",
    "simulation",
    # v1's editor stores the checkbox as the string "false"; the payload sends
    # the number 0. Same value, and neither is more correct than the other.
    "morph",
}


def _differences(left: Any, right: Any, path: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        return [
            item
            for key in sorted(set(left) | set(right))
            for item in _differences(left.get(key), right.get(key), f"{path}.{key}")
        ]
    if left == right:
        return []
    try:
        if float(left) == float(right):  # '20' and '20.0' are the same number
            return []
    except (TypeError, ValueError):
        pass
    return [f"{path}: state={left!r} payload={right!r}"]


@pytest.mark.parametrize(
    "name", ["rosse-plain", "rosse-formula-superformula", "osse-formula-coverage-angle"]
)
def test_both_recovery_routes_agree_on_the_same_real_job(name: str) -> None:
    snapshot = json.loads(BY_NAME[name]["script_snapshot_json"])
    from_state = snapshot_to_design(snapshot).design.model_dump(mode="json")
    from_payload = payload_to_design(_payload(name)).design.model_dump(mode="json")
    unexpected = [
        difference
        for difference in _differences(from_state, from_payload)
        if difference.split(".")[1].split(":")[0] not in _EXPECTED_DIVERGENCE
    ]
    assert unexpected == []


@pytest.mark.skipif(not V1_DB.exists(), reason="v1 simulation database is not available")
def test_both_recovery_routes_agree_across_the_whole_live_corpus() -> None:
    import sqlite3

    connection = sqlite3.connect(f"file:{V1_DB}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "select config_json, script_snapshot_json from simulation_jobs "
            "where script_snapshot_json is not null"
        ).fetchall()
    finally:
        connection.close()

    compared = 0
    for row in rows:
        payload = job_config_payload(json.loads(row["config_json"] or "{}"))
        snapshot = json.loads(row["script_snapshot_json"])
        try:
            from_state = snapshot_to_design(snapshot).design.model_dump(mode="json")
            from_payload = payload_to_design(payload or {}).design.model_dump(mode="json")
        except LegacySnapshotError:
            continue  # FREEFORM, refused identically by both routes
        unexpected = [
            difference
            for difference in _differences(from_state, from_payload)
            if difference.split(".")[1].split(":")[0] not in _EXPECTED_DIVERGENCE
        ]
        assert unexpected == [], unexpected
        compared += 1
    assert compared, "the live corpus holds no job that stored both copies"
