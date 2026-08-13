"""Version ordering and FREEFORM state-migration tests."""

from __future__ import annotations

import pytest

from server.design.migrate import MIGRATIONS, apply_migrations
from server.design.schema import DesignConfig
from server.design.textcfg import parse


def test_migrations_are_numeric_and_ordered() -> None:
    assert [item.name for item in MIGRATIONS] == sorted(item.name for item in MIGRATIONS)


def test_corner_ratio_uses_local_minimum_profile_radius_and_is_idempotent() -> None:
    legacy = {
        "formula": "FREEFORM",
        "length": 100,
        "throatRadius": 10,
        "mouthRadiusH": 110,
        "mouthRadiusV": 60,
        "cross_sections": [
            {"t": 0, "shape": "ellipse"},
            {"t": 0.5, "shape": "rounded_rectangle", "corner_ratio": 0.2},
            {"t": 1, "shape": "ellipse"},
        ],
    }
    migrated, applied = apply_migrations(legacy)
    station = migrated["cross_sections"][1]
    assert station["corner_radius_mm"] == 4.8
    assert "corner_ratio" not in station
    assert [item.name for item in applied] == ["001_corner_ratio_to_corner_grid"]
    again, second_applied = apply_migrations(migrated)
    assert again == migrated
    assert second_applied == []


def test_allow_alias_becomes_warn() -> None:
    migrated, applied = apply_migrations({"formula": "FREEFORM", "inflection_policy": "ALLOW"})
    assert migrated["inflection_policy"] == "warn"
    assert [item.name for item in applied] == ["002_inflection_allow_to_warn"]


def test_text_freeform_applies_both_migrations_before_validation() -> None:
    source = """; Parameter config
Freeform.Length = 100
Freeform.ThroatRadius = 10
Freeform.ThroatAngle = 12
Freeform.OvershootPolicy = reject
Freeform.InflectionPolicy = allow
Freeform.H = {
MouthRadius = 110
MouthAngle = 55
ThroatTangentScale = 1
MouthTangentScale = 1
}
Freeform.H.Points = {
}
Freeform.V = {
MouthRadius = 60
MouthAngle = 45
ThroatTangentScale = 1
MouthTangentScale = 1
}
Freeform.V.Points = {
}
Freeform.CrossSections = {
0 circle
0.5 rounded_rectangle ratio:0.2
1 ellipse
}
"""
    parsed = parse(source)
    assert parsed.migration_names == [
        "001_corner_ratio_to_corner_grid",
        "002_inflection_allow_to_warn",
        "004_freeform_solved_tangent_contract",
    ]
    assert parsed.design.root.cross_sections[1].corner_radius_mm.value == 5.3  # type: ignore[union-attr]
    assert parsed.design.root.inflection_policy == "warn"  # type: ignore[union-attr]


def test_js_undefined_lines_dropped() -> None:
    """Real v1 job snapshots contain 'Term.s = undefined' lines (JS exporter
    artifact); migration 003 drops them so absent params fall back to family
    defaults instead of breaking expression evaluation (found live via
    output/260311_simulation_2/script.snapshot.mwg)."""
    from server.design.migrate import apply_migrations

    payload = {
        "formula": "OSSE",
        "a": {"raw": "undefined", "value": None},
        "s": "undefined",
        "r0": {"raw": "NaN", "value": None},
        "mesh": {"angular_segments": {"raw": "undefined"}, "length_segments": 40},
    }
    migrated, applied = apply_migrations(payload)
    assert "a" not in migrated
    assert "s" not in migrated
    assert "r0" not in migrated
    assert "angular_segments" not in migrated["mesh"]
    assert migrated["mesh"]["length_segments"] == 40
    assert any(item.name == "003_js_undefined_lines_dropped" for item in applied)


def test_js_null_lines_dropped() -> None:
    """A JSON-null parameter reaches the v1 writer as 'key = null' (``${null}``).

    ``Number('null')`` is NaN just like the other two artifact tokens, so it has
    to be dropped with them; otherwise it validates as ``Expr(raw='null')`` with
    no value and reaches the mesher as a non-finite coordinate.
    """
    from server.design.migrate import apply_migrations

    payload = {
        "formula": "OSSE",
        "a": {"raw": "null", "value": None},
        "L": "null",
        "coverage_mode": "null",
        "mesh": {"angular_segments": "null", "length_segments": 40},
        "extra_keys": {"Report.PolarData": "null"},
    }
    migrated, applied = apply_migrations(payload)
    assert "a" not in migrated
    assert "L" not in migrated
    assert "angular_segments" not in migrated["mesh"]
    assert migrated["mesh"]["length_segments"] == 40
    # Only schema-numeric paths are touched: string fields and passthrough keep it.
    assert migrated["coverage_mode"] == "null"
    assert migrated["extra_keys"] == {"Report.PolarData": "null"}
    assert [item.name for item in applied] == ["003_js_undefined_lines_dropped"]


def test_js_artifacts_are_dropped_only_for_schema_numeric_paths() -> None:
    payload = {
        "formula": "OSSE",
        "L": "NaN",
        "coverage_mode": "NaN",
        "Future": "undefined",
        "source": {"contours": "NaN"},
        "extra_keys": {"L": "NaN", "Future": "undefined"},
    }
    migrated, applied = apply_migrations(payload)
    assert "L" not in migrated
    assert migrated["coverage_mode"] == "NaN"
    assert migrated["Future"] == "undefined"
    assert migrated["source"]["contours"] == "NaN"
    assert migrated["extra_keys"] == {"L": "NaN", "Future": "undefined"}
    assert [item.name for item in applied] == ["003_js_undefined_lines_dropped"]


def test_removed_freeform_controls_migrate_once_and_validate() -> None:
    legacy = {
        "formula": "FREEFORM",
        "profile_h": {
            "points": [
                {"z": 0, "r": 10, "angle_deg": 12, "strength": 0.8},
                {"z": 100, "r": 50},
            ],
            "throat_tangent_scale": 1.2,
            "mouth_tangent_scale": 0.9,
        },
        "profile_v": {
            "points": [
                {"z": 0, "r": 10},
                {"z": 50, "r": 28, "angle_deg": 20, "strength": 1.5},
                {"z": 100, "r": 40},
            ],
            "throat_tangent_scale": 1.1,
            "mouth_tangent_scale": 0.7,
        },
        "cross_sections": [
            {"t": 0, "shape": "circle"},
            {"t": 1, "shape": "ellipse"},
        ],
        "overshoot_policy": "reject",
    }
    migrated, applied = apply_migrations(legacy)
    assert [item.name for item in applied] == [
        "004_freeform_solved_tangent_contract",
        "005_freeform_normalized_axis",
    ]
    assert "strength" not in migrated["profile_h"]["points"][0]
    assert "strength" not in migrated["profile_v"]["points"][1]
    for profile_name in ("profile_h", "profile_v"):
        assert "throat_tangent_scale" not in migrated[profile_name]
        assert "mouth_tangent_scale" not in migrated[profile_name]
    assert "overshoot_policy" not in migrated
    assert migrated["cross_sections"][0]["shape"] == "ellipse"
    DesignConfig.model_validate(migrated)


def test_removed_freeform_controls_migration_handles_list_rows() -> None:
    migrated, applied = apply_migrations(
        {
            "formula": "FREEFORM",
            "profile_h": {"points": [[0, 10, 12, 0.8], [100, 50]]},
            "profile_v": {"points": [[0, 10], [100, 40, 45, 1.2]]},
        }
    )
    assert [item.name for item in applied] == [
        "004_freeform_solved_tangent_contract",
        "005_freeform_normalized_axis",
    ]
    assert migrated["profile_h"]["points"][0] == {"t": 0, "r": 10, "angle_deg": 12}
    assert migrated["profile_v"]["points"][1] == {"t": 1, "r": 40, "angle_deg": 45}


def test_current_freeform_payload_triggers_only_axis_migration() -> None:
    payload = {
        "formula": "FREEFORM",
        "profile_h": {"points": [{"z": 0, "r": 10}, {"z": 100, "r": 50}]},
        "profile_v": {"points": [{"z": 0, "r": 10}, {"z": 100, "r": 40}]},
        "cross_sections": [
            {"t": 0, "shape": "ellipse"},
            {"t": 1, "shape": "ellipse"},
        ],
        "inflection_policy": "warn",
    }
    migrated, applied = apply_migrations(payload)
    assert migrated["length"] == 100
    assert migrated["profile_h"]["points"] == [{"t": 0, "r": 10}, {"t": 1, "r": 50}]
    assert [item.name for item in applied] == ["005_freeform_normalized_axis"]


def test_normalized_axis_migration_applies_once_and_reports_once() -> None:
    legacy = {
        "formula": "FREEFORM",
        "profile_h": {"points": [{"z": 0, "r": 10}, {"z": 70, "r": 30}, {"z": 120, "r": 50}]},
        "profile_v": {"points": [{"z": 0, "r": 10}, {"z": 120, "r": 40}]},
        "cross_sections": [{"t": 0, "shape": "ellipse"}, {"t": 1, "shape": "ellipse"}],
    }
    migrated, applied = apply_migrations(legacy)
    assert [item.name for item in applied] == ["005_freeform_normalized_axis"]
    assert len(migrated["profile_h"]["points"]) == 3
    assert migrated["profile_h"]["points"][1]["t"] == 70 / 120
    DesignConfig.model_validate(migrated)

    again, second_applied = apply_migrations(migrated)
    assert again == migrated
    assert second_applied == []


def test_normalized_payload_does_not_trigger_axis_migration() -> None:
    payload = {
        "formula": "FREEFORM",
        "length": 100,
        "profile_h": {"points": [{"t": 0, "r": 10}, {"t": 1, "r": 50}]},
        "profile_v": {"points": [{"t": 0, "r": 10}, {"t": 1, "r": 40}]},
        "cross_sections": [{"t": 0, "shape": "ellipse"}, {"t": 1, "shape": "ellipse"}],
    }
    migrated, applied = apply_migrations(payload)
    assert migrated == payload
    assert all(item.name != "005_freeform_normalized_axis" for item in applied)


@pytest.mark.parametrize("mouth_z", [0, "p + 1"])
def test_axis_migration_does_not_invent_an_invalid_length(mouth_z: object) -> None:
    payload = {
        "formula": "FREEFORM",
        "profile_h": {"points": [{"z": 0, "r": 10}, {"z": mouth_z, "r": 50}]},
        "profile_v": {"points": [{"z": 0, "r": 10}, {"z": 100, "r": 40}]},
    }
    migrated, _applied = apply_migrations(payload)
    assert migrated == payload
    with pytest.raises(Exception):
        DesignConfig.model_validate(migrated)


def test_axis_migration_does_not_report_success_for_malformed_second_profile() -> None:
    payload = {
        "formula": "FREEFORM",
        "profile_h": {"points": [{"z": 0, "r": 10}, {"z": 100, "r": 50}]},
        "profile_v": {"points": [{"z": 0, "r": 10}, {"r": 40}]},
    }

    migrated, applied = apply_migrations(payload)

    assert migrated == payload
    assert all(item.name != "005_freeform_normalized_axis" for item in applied)


@pytest.mark.parametrize(
    "ratio",
    ["NaN", "Infinity", 10**10000],
    ids=["nan", "infinity", "integer-overflow"],
)
def test_corner_ratio_non_finite_or_overflow_is_a_migration_error(ratio: object) -> None:
    payload = {
        "formula": "FREEFORM",
        "cross_sections": [{"t": 0.5, "shape": "rounded_rectangle", "corner_ratio": ratio}],
    }
    with pytest.raises(ValueError, match="finite"):
        apply_migrations(payload)
