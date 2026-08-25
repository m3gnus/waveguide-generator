"""Unit coverage for the discriminated design schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.design.migrate import apply_migrations
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
                "length": 120,
                "profile_h": {"points": [{"t": 0, "r": 12.7}, {"t": 1, "r": 100}]},
                "profile_v": {"points": [{"t": 0, "r": 12.7}, {"t": 1, "r": 80}]},
                "cross_sections": [{"t": 0, "shape": "ellipse"}, {"t": 1, "shape": "ellipse"}],
                "corner_grids": [],
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


@pytest.mark.parametrize(
    "raw",
    [
        "120\n}",
        "12.7\n}\nOSSE = {",
        "12.7\u2028Length = 1",
        "120}",
        "{120",
        "OSSE = { ; }",
        "} ; {",
    ],
)
def test_expr_rejects_text_that_can_change_cfg_structure(raw: str) -> None:
    with pytest.raises(ValidationError, match="expression source"):
        Expr.model_validate(raw)


def test_expr_preserves_complete_legacy_multiline_function() -> None:
    raw = "function anonymous(p\n) {\nreturn 45 - 2*Math.cos(p)**2;\n}"

    expression = Expr.model_validate(raw)

    assert expression.raw == raw
    assert expression.value is None


@pytest.mark.parametrize(
    "raw",
    [
        "'x' * 1000000000",
        "[0] * 100000000",
        "10 ** 1000000000",
        "pow(10, 1000000000)",
        " + ".join(["1"] * 300),
        "(" * 1500 + "1" + ")" * 1500,
    ],
)
def test_expr_rejects_resource_exhausting_source_before_adapter_execution(
    raw: str,
) -> None:
    with pytest.raises(ValidationError, match="expression"):
        Expr.model_validate(raw)


def test_expr_rejects_parameter_dependent_exponents() -> None:
    with pytest.raises(ValidationError, match="bounded constants"):
        Expr.model_validate("10 ** (1000000 * p)")


def test_expr_preserves_bounded_ath_superformula_tuple() -> None:
    expression = Expr.model_validate("1,1,8,0.6,5,2")
    assert expression.value is None
    assert expression.raw == "1,1,8,0.6,5,2"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2^8", 256.0),
        ("sqrt(81) + abs(-3)", 12.0),
        ("min(9, 4, 7) + max(1, 2)", 6.0),
        ("cos(pi)", -1.0),
    ],
)
def test_expr_bounded_evaluator_preserves_numeric_ath_arithmetic(
    raw: str, expected: float
) -> None:
    assert Expr.model_validate(raw).value == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["ceil(1.2)", "floor(1.2)", "log(10)", "exp(1)"])
def test_expr_rejects_functions_the_pinned_mesher_cannot_execute(raw: str) -> None:
    with pytest.raises(ValidationError, match="unknown expression function"):
        Expr.model_validate(raw)


def test_expr_v1_only_function_error_explains_compatibility_limit() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Expr.model_validate("exp(1)")

    message = str(exc_info.value)
    assert "unknown expression function 'exp'" in message
    assert "the geometry engine supports a fixed set of functions" in message
    assert "abs, acos, asin, atan, atan2, cos, max, min, pow, sin, sqrt, tan" in message
    assert "accepted by the v1 UI" in message
    assert "original-to-current compatibility limit" in message


def test_expr_unknown_function_error_does_not_claim_v1_compatibility() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Expr.model_validate("sqrtt(9)")

    message = str(exc_info.value)
    assert "unknown expression function 'sqrtt'" in message
    assert "the geometry engine supports a fixed set of functions" in message
    assert "abs, acos, asin, atan, atan2, cos, max, min, pow, sin, sqrt, tan" in message
    assert "original-to-current compatibility limit" not in message


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("abs(-2)", 2.0),
        ("acos(1)", 0.0),
        ("asin(0)", 0.0),
        ("atan(1)", 0.7853981633974483),
        ("atan2(1, 1)", 0.7853981633974483),
        ("cos(0)", 1.0),
        ("max(1, 2)", 2.0),
        ("min(1, 2)", 1.0),
        ("pow(2, 3)", 8.0),
        ("sin(pi / 2)", 1.0),
        ("sqrt(9)", 3.0),
        ("tan(0)", 0.0),
    ],
)
def test_expr_all_mesher_supported_functions_still_evaluate(
    raw: str, expected: float
) -> None:
    assert Expr.model_validate(raw).value == pytest.approx(expected)


def test_expr_supports_mesher_atan2_and_e_constant() -> None:
    expression = Expr.model_validate("atan2(1, 1) + e")
    assert expression.value == pytest.approx(3.5036799919)


def test_expr_preserves_legacy_math_source_but_normalizes_execution_text() -> None:
    expression = Expr.model_validate({"value": 45, "raw": "45 + Math.sin(p)"})
    assert expression.raw == "45 + Math.sin(p)"
    assert expression.execution_text() == "45 + sin(p)"


@pytest.mark.parametrize("raw", ["1e309", "-1e309"])
def test_expr_rejects_nonfinite_numeric_spellings(raw: str) -> None:
    with pytest.raises(ValidationError, match="finite"):
        Expr.model_validate(raw)


def test_expr_normalizes_legacy_boolean_execution_without_losing_source() -> None:
    expression = Expr.model_validate("false")
    assert expression.value == 0
    assert expression.raw == "false"
    assert expression.execution_text() == "0"


def test_expr_rejects_conflicting_value_and_raw_representations() -> None:
    with pytest.raises(ValidationError, match="does not match constant raw source"):
        Expr.model_validate({"value": 1, "raw": "999"})

    parameterized = Expr.model_validate({"value": 45, "raw": "45 + cos(p)"})
    assert parameterized.value == 45
    assert parameterized.raw == "45 + cos(p)"


def test_parameterized_expression_cached_value_survives_schema_wire_round_trip() -> None:
    design = DesignConfig.model_validate(
        {"formula": "OSSE", "a": {"value": 45, "raw": "45 + cos(p)"}}
    )
    reopened = DesignConfig.model_validate(design.model_dump(mode="json"))
    assert reopened.root.a is not None
    assert reopened.root.a.value == 45
    assert reopened.root.a.raw == "45 + cos(p)"


def test_morph_target_shape_remains_expression_capable() -> None:
    design = DesignConfig.model_validate(
        {"formula": "OSSE", "morph": {"target_shape": "2 + (p > 0)"}}
    )

    assert design.root.morph.target_shape is not None
    assert design.root.morph.target_shape.raw == "2 + (p > 0)"


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


def test_freeform_requires_shared_profile_throat_radius() -> None:
    payload = {
        "formula": "FREEFORM",
        "length": 120,
        "profile_h": {"points": [{"t": 0, "r": 10}, {"t": 1, "r": 50}]},
        "profile_v": {"points": [{"t": 0, "r": 11}, {"t": 1, "r": 40}]},
        "cross_sections": [{"t": 0, "shape": "ellipse"}, {"t": 1, "shape": "ellipse"}],
    }

    with pytest.raises(ValidationError, match="profile throat radii must match"):
        DesignConfig.model_validate(payload)


@pytest.mark.parametrize(
    "points",
    [
        [{"t": 0, "r": 10, "angle_deg": 5}, {"t": 1, "r": 50}],
        [{"t": 0, "r": 10}, {"t": 1, "r": 50, "angle_deg": 25}],
    ],
)
def test_freeform_keeps_profile_endpoint_angles(
    points: list[dict[str, float]],
) -> None:
    """Endpoint angles reach the mesher (translate sends every point's angle)
    and v1 designs migrate carrying them, so they must stay accepted.

    The text format writes only the interior points, so these do not survive a
    save and reload -- a real defect, but one that has to be fixed by
    persisting them, not by rejecting a design the mesher happily builds."""

    payload = {
        "formula": "FREEFORM",
        "length": 120,
        "profile_h": {"points": points},
        "profile_v": {"points": [{"t": 0, "r": 10}, {"t": 1, "r": 40}]},
        "cross_sections": [{"t": 0, "shape": "ellipse"}, {"t": 1, "shape": "ellipse"}],
    }

    assert DesignConfig.model_validate(payload) is not None


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        (
            {"corner_grids": [{"t": 0.5, "values": [[0, 1], [2, 3]]}]},
            "corner_grids are not supported",
        ),
        (
            {
                "cross_sections": [
                    {"t": 0, "shape": "ellipse", "corner_grid": [[0, 1]]},
                    {"t": 1, "shape": "ellipse"},
                ]
            },
            "corner_grid is not supported",
        ),
    ],
)
def test_freeform_rejects_unsupported_corner_grids(
    replacement: dict[str, object], message: str
) -> None:
    payload: dict[str, object] = {
        "formula": "FREEFORM",
        "length": 120,
        "profile_h": {"points": [{"t": 0, "r": 10}, {"t": 1, "r": 50}]},
        "profile_v": {"points": [{"t": 0, "r": 10}, {"t": 1, "r": 40}]},
        "cross_sections": [{"t": 0, "shape": "ellipse"}, {"t": 1, "shape": "ellipse"}],
    }
    payload.update(replacement)

    with pytest.raises(ValidationError, match=message):
        DesignConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("points", "message"),
    [
        ([{"t": 0.1, "r": 1}, {"t": 1, "r": 2}], "first FREEFORM profile point"),
        ([{"t": 0, "r": 1}, {"t": 0.9, "r": 2}], "last FREEFORM profile point"),
        ([{"t": 0, "r": 1}, {"t": 0.7, "r": 2}, {"t": 0.6, "r": 3}, {"t": 1, "r": 4}], "strictly increasing"),
        ([{"t": 0, "r": 1}, {"t": 1.1, "r": 2}], "between 0 and 1"),
    ],
)
def test_freeform_rejects_invalid_normalized_point_domains(
    points: list[dict[str, float]], message: str
) -> None:
    payload = {
        "formula": "FREEFORM",
        "length": 120,
        "profile_h": {"points": points},
        "profile_v": {"points": [{"t": 0, "r": 1}, {"t": 1, "r": 2}]},
        "cross_sections": [{"t": 0, "shape": "ellipse"}, {"t": 1, "shape": "ellipse"}],
    }
    with pytest.raises(ValidationError, match=message):
        DesignConfig.model_validate(payload)


@pytest.mark.parametrize("length", [0, -1])
def test_freeform_rejects_non_positive_length(length: float) -> None:
    payload = {
        "formula": "FREEFORM",
        "length": length,
        "profile_h": {"points": [{"t": 0, "r": 1}, {"t": 1, "r": 2}]},
        "profile_v": {"points": [{"t": 0, "r": 1}, {"t": 1, "r": 2}]},
        "cross_sections": [{"t": 0, "shape": "ellipse"}, {"t": 1, "shape": "ellipse"}],
    }
    with pytest.raises(ValidationError, match="greater than 0"):
        DesignConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        (
            {"length": {"value": 120, "raw": "120 + p"}},
            "FREEFORM length must be scalar",
        ),
        (
            {
                "profile_h": {
                    "points": [
                        {"t": 0, "r": 1},
                        {"t": {"value": 1, "raw": "1 - p"}, "r": 2},
                    ]
                }
            },
            "point t values must be scalar",
        ),
        (
            {
                "cross_sections": [
                    {"t": 0, "shape": "ellipse"},
                    {"t": {"value": 1, "raw": "1 - p"}, "shape": "ellipse"},
                ]
            },
            "station t values must be scalar",
        ),
    ],
)
def test_freeform_rejects_cached_parameterized_structural_values(
    replacement: dict[str, object], message: str
) -> None:
    payload: dict[str, object] = {
        "formula": "FREEFORM",
        "length": 120,
        "profile_h": {"points": [{"t": 0, "r": 1}, {"t": 1, "r": 2}]},
        "profile_v": {"points": [{"t": 0, "r": 1}, {"t": 1, "r": 2}]},
        "cross_sections": [
            {"t": 0, "shape": "ellipse"},
            {"t": 1, "shape": "ellipse"},
        ],
    }
    payload.update(replacement)
    with pytest.raises(ValidationError, match=message):
        DesignConfig.model_validate(payload)


def test_freeform_length_change_preserves_interior_anchors_and_validation() -> None:
    payload = {
        "formula": "FREEFORM",
        "length": 120,
        "profile_h": {"points": [{"t": 0, "r": 10}, {"t": 70 / 120, "r": 30}, {"t": 1, "r": 50}]},
        "profile_v": {"points": [{"t": 0, "r": 10}, {"t": 25 / 120, "r": 20}, {"t": 1, "r": 40}]},
        "cross_sections": [{"t": 0, "shape": "ellipse"}, {"t": 1, "shape": "ellipse"}],
    }
    original = DesignConfig.model_validate(payload)
    shortened = original.model_dump(mode="json")
    shortened["length"] = 60
    validated = DesignConfig.model_validate(shortened)
    assert [point.t.value for point in validated.root.profile_h.points] == [0, 70 / 120, 1]  # type: ignore[union-attr]


def test_v1_sim_type_convention_has_named_schema_values() -> None:
    infinite = DesignConfig.model_validate({"formula": "OSSE", "simulation": {"sim_type": "1"}})
    free = DesignConfig.model_validate({"formula": "OSSE", "simulation": {"sim_type": 2}})
    assert infinite.root.simulation.sim_type == "infinite-baffle"
    assert free.root.simulation.sim_type == "freestanding"


@pytest.mark.parametrize("stated", [" FULL_3D ", "circsym", "auto", "fastest"])
def test_a_design_stated_solver_mode_is_dropped_and_reported(stated: str) -> None:
    """No design may set the solver path, and none may lose it quietly.

    Which formulation this host can run is a machine fact, so the value lives
    in solve options and export strips it. Nothing read it out of the design,
    which made a stated mode a silent no-op. Every spelling is now treated the
    same -- dropped, with a note the open report and ``wg validate`` show --
    including one that is not a valid mode at all, because no spelling of it
    was ever going to be honoured.
    """

    migrated, applied = apply_migrations(
        {"formula": "OSSE", "simulation": {"solver_mode": stated}}
    )

    assert "solver_mode" not in migrated["simulation"]
    assert [item.name for item in applied] == ["006_machine_solver_mode_not_portable"]
    assert "Solve options" in applied[0].note

    design = DesignConfig.model_validate(
        {"formula": "OSSE", "simulation": {"solver_mode": stated}}
    )
    assert design.root.simulation.solver_mode is None
