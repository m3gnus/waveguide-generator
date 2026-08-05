"""Regression coverage for reopening v1 job parameter snapshots."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from server.design.legacy_snapshot import (
    LegacySnapshotError,
    snapshot_to_ath_text,
    snapshot_to_design,
)
from server.design.textcfg import ParsedDesign


DB_PATH = Path("/Users/magnus/Code/hornlab-workspace/Waveguide Generator/server/data/simulations.db")


# These are reduced parameter bags copied from real rows 250917asro, horn_design,
# and 260609-big- respectively.  Keeping them here makes the migration path run
# in CI, where the v1 checkout and its database are not present.
INLINE_OSSE = {
    "params": {
        "scale": 1,
        "L": 130,
        "a": 40,
        "a0": 10,
        "r0": 12.7,
        "k": 7,
        "s": 0.85,
        "n": 4,
        "q": 0.991,
        "h": 0,
        "throatProfile": 1,
        "throatExtAngle": 0,
        "throatExtLength": 0,
        "slotLength": 0,
        "rot": 0,
        "gcurveType": 0,
        "circArcTermAngle": 1,
        "circArcRadius": 0,
        "morphTarget": 0,
        "morphWidth": 0,
        "morphHeight": 0,
        "morphCorner": 0,
        "morphRate": 3,
        "morphFixed": 0,
        "morphAllowShrinkage": 0,
        "angularSegments": 40,
        "lengthSegments": 20,
        "cornerSegments": 4,
        "throatSegments": 0,
        "throatResolution": 6,
        "mouthResolution": 20,
        "throatSliceDensity": None,
        "verticalOffset": 0,
        "wallThickness": 0,
        "rearResolution": 40,
        "quadrants": "1234",
        "sourceShape": 1,
        "sourceRadius": -1,
        "sourceCurv": 0,
        "sourceVelocity": 1,
        "sourceContours": "",
        "freqStart": 400,
        "freqEnd": 16000,
        "numFreqs": 20,
        "simType": 1,
        "solverMode": "auto",
        "encDepth": 0,
        "encEdge": 18,
        "encEdgeType": 1,
        "encSpaceL": 25,
        "encSpaceT": 25,
        "encSpaceR": 25,
        "encSpaceB": 25,
        "encFrontResolution": "25,25,25,25",
        "encBackResolution": "40,40,40,40",
        "type": "OSSE",
    }
}

INLINE_ROSSE = {
    "params": {
        "scale": 1,
        "R": 140,
        "a": 40,
        "a0": 15.5,
        "r0": 12.7,
        "k": 2,
        "m": 0.85,
        "b": 0.2,
        "r": 0.4,
        "q": 3.4,
        "tmax": 1,
        "throatProfile": 1,
        "morphTarget": 1,
        "morphWidth": 0,
        "morphHeight": 0,
        "morphCorner": 0,
        "morphRate": 3,
        "morphFixed": 0,
        "morphAllowShrinkage": 0,
        "angularSegments": 40,
        "lengthSegments": 20,
        "cornerSegments": 4,
        "throatSegments": 0,
        "throatResolution": 6,
        "mouthResolution": 20,
        "throatSliceDensity": None,
        "verticalOffset": 0,
        "wallThickness": 0,
        "rearResolution": 40,
        "apertureResolutionScale": 1.5,
        "quadrants": "1234",
        "sourceShape": 1,
        "sourceRadius": -1,
        "sourceCurv": 0,
        "sourceVelocity": 1,
        "sourceContours": "",
        "freqStart": 400,
        "freqEnd": 16000,
        "numFreqs": 20,
        "simType": "2",
        "solverMode": "auto",
        "encDepth": 0,
        "encEdge": 18,
        "encEdgeType": 1,
        "encSpaceL": 25,
        "encSpaceT": 25,
        "encSpaceR": 25,
        "encSpaceB": 25,
        "encFrontResolution": "25,25,25,25",
        "encBackResolution": "40,40,40,40",
        "type": "R-OSSE",
    }
}

INLINE_ENCLOSURE = {
    "params": {
        "scale": 1,
        "L": 310,
        "a0": 15.5,
        "r0": 12.7,
        "k": 2,
        "s": 0.8,
        "n": 5,
        "q": 0.993,
        "h": 0,
        "throatProfile": 1,
        "morphTarget": 1,
        "morphWidth": 0,
        "morphHeight": 0,
        "morphCorner": 18,
        "morphRate": 3,
        "morphFixed": 0,
        "morphAllowShrinkage": "false",
        "angularSegments": 80,
        "lengthSegments": 20,
        "cornerSegments": 4,
        "throatSegments": 0,
        "throatResolution": 5,
        "mouthResolution": 25,
        "throatSliceDensity": None,
        "verticalOffset": 80,
        "wallThickness": 0,
        "rearResolution": 40,
        "apertureResolutionScale": 1.5,
        "quadrants": 1,
        "sourceShape": 2,
        "sourceRadius": -1,
        "sourceCurv": 0,
        "sourceVelocity": 1,
        "sourceContours": "",
        "freqStart": 100,
        "freqEnd": 20000,
        "numFreqs": 60,
        "simType": "2",
        "solverMode": "auto",
        "encDepth": 500,
        "encEdge": 1,
        "encEdgeType": 1,
        "encSpaceL": 20,
        "encSpaceT": 20,
        "encSpaceR": 20,
        "encSpaceB": 20,
        "encFrontResolution": "40,40,40,40",
        "encBackResolution": "40,40,40,40",
        "Scale": 1,
        "Coverage.Angle": "45 - 16*cos(1*p)^2 -40*sin(p*1)^16",
        "_blocks": {
            "Mesh.Enclosure": {
                "_items": {
                    "Depth": "500",
                    "EdgeRadius": "1",
                    "EdgeType": "1",
                    "Spacing": "20,20,20,20",
                },
                "_lines": [],
            },
            "Report": {
                "_items": {
                    "Title": '"ATH Tritonia-M"',
                    "PolarData": "SPL_H",
                },
                "_lines": ["report row"],
            },
        },
        "type": "OSSE",
    }
}

INLINE_FREEFORM = {
    "params": {
        "scale": 1,
        "profileH": [[0, 12.7], [60, 80], [120, 160]],
        "profileV": [[0, 12.7], [60, 60], [120, 110]],
        "throatAngleH": 15.5,
        "mouthAngleH": 70,
        "crossSections": [
            {"t": 0, "shape": "circle"},
            {"t": 0.4, "shape": "rounded_rectangle", "cornerRatio": 0.12},
            {"t": 1, "shape": "rounded_rectangle", "cornerRatio": 0.12},
        ],
        "type": "FREEFORM",
    }
}


def _db_snapshots() -> list[dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as connection:
        return [
            json.loads(row[0])
            for row in connection.execute(
                "select script_snapshot_json from simulation_jobs "
                "where script_snapshot_json is not null"
            )
        ]


def _expr_value(parsed: ParsedDesign, name: str) -> float:
    value = getattr(parsed.design.root, name)
    assert value is not None
    assert value.value is not None
    return value.value


def test_inline_real_snapshots_validate_and_omit_timestamp() -> None:
    for snapshot in (INLINE_OSSE, INLINE_ROSSE, INLINE_ENCLOSURE):
        text = snapshot_to_ath_text(snapshot["params"])
        assert text.startswith("; Parameter config\n")
        assert "; Generated:" not in text
        assert isinstance(snapshot_to_design(snapshot), ParsedDesign)


def test_inline_real_family_values_and_passthrough_order() -> None:
    osse = snapshot_to_design(INLINE_OSSE)
    assert osse.design.formula == "OSSE"
    assert _expr_value(osse, "r0") == pytest.approx(12.7)
    assert _expr_value(osse, "L") == pytest.approx(130)
    assert _expr_value(osse, "a0") == pytest.approx(10)
    assert _expr_value(osse, "q") == pytest.approx(0.991)

    rosse = snapshot_to_design(INLINE_ROSSE["params"])
    assert rosse.design.formula == "R-OSSE"
    assert _expr_value(rosse, "r0") == pytest.approx(12.7)
    assert _expr_value(rosse, "R") == pytest.approx(140)
    assert _expr_value(rosse, "a0") == pytest.approx(15.5)
    assert _expr_value(rosse, "q") == pytest.approx(3.4)

    text = snapshot_to_ath_text(INLINE_ENCLOSURE["params"])
    assert text.count("Mesh.Enclosure = {") == 1
    assert text.index("report row") < text.index('Title = "ATH Tritonia-M"')
    assert "Freeform." not in text
    assert "Mesh.Enclosure" not in text[text.index("Report = {") :]


def test_freeform_legacy_snapshot_requires_reentry() -> None:
    with pytest.raises(LegacySnapshotError, match="FREEFORM legacy snapshots are not supported"):
        snapshot_to_design(INLINE_FREEFORM)


@pytest.mark.skipif(not DB_PATH.exists(), reason="v1 simulation database is not available")
def test_all_25_real_non_freeform_snapshots_validate() -> None:
    snapshots = [snapshot for snapshot in _db_snapshots() if snapshot["params"].get("type") != "FREEFORM"]
    assert len(snapshots) == 25
    for snapshot in snapshots:
        assert isinstance(snapshot_to_design(snapshot), ParsedDesign)


@pytest.mark.skipif(not DB_PATH.exists(), reason="v1 simulation database is not available")
def test_real_family_values_are_preserved() -> None:
    snapshots = _db_snapshots()
    by_type = {
        formula: next(snapshot for snapshot in snapshots if snapshot["params"].get("type") == formula)
        for formula in ("OSSE", "R-OSSE")
    }
    for formula, snapshot in by_type.items():
        params = snapshot["params"]
        parsed = snapshot_to_design(snapshot)
        assert _expr_value(parsed, "r0") == pytest.approx(params["r0"])
        assert _expr_value(parsed, "a0") == pytest.approx(params["a0"])
        assert _expr_value(parsed, "q") == pytest.approx(params["q"])
        length_key = "L" if formula == "OSSE" else "R"
        assert _expr_value(parsed, length_key) == pytest.approx(params[length_key])


@pytest.mark.skipif(not DB_PATH.exists(), reason="v1 simulation database is not available")
def test_all_5_real_enclosures_are_preserved() -> None:
    snapshots = [snapshot for snapshot in _db_snapshots() if snapshot["params"].get("encDepth", 0) > 0]
    assert len(snapshots) == 5
    for snapshot in snapshots:
        params = snapshot["params"]
        enclosure = snapshot_to_design(snapshot).design.root.enclosure
        assert enclosure is not None
        assert enclosure.depth is not None
        assert enclosure.edge_radius is not None
        assert enclosure.edge_type is not None
        assert enclosure.depth.value == pytest.approx(params["encDepth"])
        assert enclosure.edge_radius.value == pytest.approx(params["encEdge"])
        assert enclosure.edge_type.value == pytest.approx(params["encEdgeType"])
        for field, key in (
            ("space_l", "encSpaceL"),
            ("space_t", "encSpaceT"),
            ("space_r", "encSpaceR"),
            ("space_b", "encSpaceB"),
        ):
            value = getattr(enclosure, field)
            assert value is not None
            assert value.value == pytest.approx(params[key])
