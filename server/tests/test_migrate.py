"""Version ordering and FREEFORM state-migration tests."""

from __future__ import annotations

import pytest

from server.design.migrate import MIGRATIONS, apply_migrations
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
            {"t": 0, "shape": "circle"},
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
