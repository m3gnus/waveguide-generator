"""Unit coverage for the discriminated design schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.design.schema import DesignConfig, Expr, FreeformConfig, ICWConfig, OSSEConfig, ROSSEConfig


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        ({"formula": "OSSE", "L": "100 + 20", "a": "45 + cos(p)"}, OSSEConfig),
        ({"formula": "R-OSSE", "R": "140", "m": 0.85}, ROSSEConfig),
        (
            {
                "formula": "ICW",
                "termination": "rollback",
                "depth": 90,
                "curl": "0.25",
                "coverage_mode": "controlled",
            },
            ICWConfig,
        ),
        (
            {
                "formula": "FREEFORM",
                "profile_h": {"points": [{"z": 0, "r": 12.7}, {"z": 120, "r": 100}]},
                "profile_v": {"points": [{"z": 0, "r": 12.7}, {"z": 120, "r": 80}]},
                "cross_sections": [{"t": 0, "shape": "circle"}, {"t": 1, "shape": "ellipse"}],
                "corner_grids": [{"t": 0.5, "values": [[0, 1], [2, 3]]}],
            },
            FreeformConfig,
        ),
    ],
)
def test_formula_discriminator(payload: dict[str, object], expected_type: type[object]) -> None:
    design = DesignConfig.model_validate(payload)
    assert isinstance(design.root, expected_type)
    assert design.formula == payload["formula"]


def test_expr_evaluates_constants_and_preserves_parameterized_source() -> None:
    constant = Expr.model_validate("45 + 10")
    angular = Expr.model_validate("48.5 - 5.6*cos(2*p)^5")
    assert constant.value == 55
    assert constant.raw == "45 + 10"
    assert angular.value is None
    assert angular.raw == "48.5 - 5.6*cos(2*p)^5"
    assert angular.text() == angular.raw


def test_expr_object_with_raw_only_derives_scalar_value() -> None:
    assert Expr.model_validate({"raw": "140 * 2"}).value == 280
    assert Expr.model_validate({"raw": "45 + cos(p)"}).value is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {"value": "NaN"}])
def test_expr_rejects_non_finite_values(value: object) -> None:
    with pytest.raises(ValidationError, match="finite"):
        Expr.model_validate(value)


def test_expr_turns_huge_integer_overflow_into_validation_error() -> None:
    with pytest.raises(ValidationError, match="representable"):
        Expr.model_validate(10**10000)


def test_explicit_extra_containers_are_typed() -> None:
    design = DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "extra_keys": {"Future.Option": "a = b"},
            "extra_blocks": {"Report": {"items": {"Title": '"demo"'}, "lines": []}},
        }
    )
    assert design.extra_keys == {"Future.Option": "a = b"}
    assert design.extra_blocks["Report"].items["Title"] == '"demo"'


@pytest.mark.parametrize(
    "payload",
    [
        {"formula": "ICW", "termination": "legacy-flat"},
    ],
)
def test_literal_fields_reject_unmigrated_legacy_or_unknown_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        DesignConfig.model_validate(payload)


def test_design_config_routes_legacy_literal_aliases_through_migrations() -> None:
    design = DesignConfig.model_validate(
        {
            "formula": "FREEFORM",
            "profile_h": {"points": [{"z": 0, "r": 1}, {"z": 2, "r": 2}]},
            "profile_v": {"points": [{"z": 0, "r": 1}, {"z": 2, "r": 2}]},
            "cross_sections": [{"t": 0, "shape": "circle"}, {"t": 1, "shape": "ellipse"}],
            "inflection_policy": "allow",
        }
    )
    assert design.root.inflection_policy == "warn"  # type: ignore[union-attr]


def test_freeform_requires_v1_readable_cross_section_domain() -> None:
    payload = {
        "formula": "FREEFORM",
        "profile_h": {"points": [{"z": 0, "r": 1}, {"z": 2, "r": 2}]},
        "profile_v": {"points": [{"z": 0, "r": 1}, {"z": 2, "r": 2}]},
        "cross_sections": [],
    }
    with pytest.raises(ValidationError, match="at least 2"):
        DesignConfig.model_validate(payload)


def test_v1_sim_type_convention_has_named_schema_values() -> None:
    infinite = DesignConfig.model_validate({"formula": "OSSE", "simulation": {"sim_type": "1"}})
    free = DesignConfig.model_validate({"formula": "OSSE", "simulation": {"sim_type": 2}})
    assert infinite.root.simulation.sim_type == "infinite-baffle"
    assert free.root.simulation.sim_type == "freestanding"
