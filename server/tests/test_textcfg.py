"""Grammar, dialect, expression, and passthrough round-trip tests."""

from __future__ import annotations

import pytest

from server.design.schema import DesignConfig, Expr, OSSEConfig
from server.design.textcfg import TextConfigError, parse, serialize
from server.preview.translate import design_to_mesher_config


SOURCE = """; Parameter config
; Generated: 2026-01-02 03:04
Coverage.Angle = 45 + 10*cos(p)^2 ; raw expression
Length = 120
Term.n = 4
Term.q = 0.991
Term.s = 0.8
Throat.Angle = 10
Throat.Diameter = 25.4
OS.k = 7
Future.Key = alpha=beta
Report = {
; keep this block comment
free form row
Title = \"Example\"
}
"""

FREEFORM_SOURCE = """; Parameter config
Freeform.Length = 100
Freeform.ThroatRadius = 10
Freeform.H = {
MouthRadius = 50
}
Freeform.V = {
MouthRadius = 40
}
Freeform.CrossSections = {
0 ellipse
1 ellipse
}
"""


def test_parse_preserves_unknowns_comments_and_first_equals_value() -> None:
    parsed = parse(SOURCE)
    assert parsed.dialect == "mwg"
    assert parsed.design.formula == "OSSE"
    assert parsed.design.root.a.raw == "45 + 10*cos(p)^2"  # type: ignore[union-attr]
    assert parsed.design.root.r0.value == 12.7  # type: ignore[union-attr]
    assert parsed.extra_keys["Future.Key"] == "alpha=beta"
    assert parsed.extra_blocks["Report"].lines == ["free form row"]
    assert parsed.extra_blocks["Report"].items == {"Title": '"Example"'}


def test_pristine_round_trip_is_byte_identical_and_semantically_lossless() -> None:
    first = parse(SOURCE)
    emitted = serialize(first)
    second = parse(emitted)
    assert emitted == SOURCE
    assert first.semantic_data() == second.semantic_data()


def test_ath_dialect_sniff_is_content_based_not_suffix_based() -> None:
    parsed = parse("OSSE = {\nL = 120\na = 45\n}\n")
    assert parsed.dialect == "ath"


def test_multiline_v1_function_value_is_one_raw_expression() -> None:
    source = """; MWG config
Coverage.Angle = function anonymous(p
) {
  const pi = Math.PI;
  return 45 - 2*Math.cos(p)**2;
}
Length = 120
Term.n = 4
"""
    parsed = parse(source)
    value = parsed.design.root.a  # type: ignore[union-attr]
    assert value.raw.startswith("function anonymous(p")
    assert "const pi" not in parsed.extra_keys
    assert serialize(parsed) == source

    canonical = serialize(parsed.design)
    reopened = parse(canonical)
    assert reopened.design.root.a is not None  # type: ignore[union-attr]
    assert reopened.design.root.a.raw == value.raw  # type: ignore[union-attr]


def test_new_design_uses_v1_header_v2_discriminator_and_writer_order() -> None:
    design = DesignConfig(
        root=OSSEConfig(
            formula="OSSE",
            a=Expr.model_validate("45"),
            L=Expr(value=120),
            n=Expr(value=4),
            q=Expr(value=0.99),
            s=Expr(value=0.8),
            a0=Expr(value=10),
            r0=Expr(value=12.7),
            k=Expr(value=7),
        )
    )
    text = serialize(design)
    assert text.startswith("; Parameter config\n; Waveguide Generator design-format: 2\n")
    assert text.index("Coverage.Angle") < text.index("Length") < text.index("Term.n")
    assert parse(text).design.formula == "OSSE"


def test_parse_rejects_design_format_from_a_future_writer() -> None:
    source = "; Parameter config\n; Waveguide Generator design-format: 3\nOSSE = {\n}\n"

    with pytest.raises(TextConfigError, match="unsupported design format 3"):
        parse(source)


@pytest.mark.parametrize(
    "source",
    [
        "; Parameter config\nOSSE = {\n}\n",
        "; Parameter config\n; Waveguide Generator design-format: 1\nOSSE = {\n}\n",
        "; Parameter config\n; Waveguide Generator design-format: 2\nOSSE = {\n}\n",
    ],
)
def test_parse_accepts_legacy_and_supported_design_formats(source: str) -> None:
    assert parse(source).design.formula == "OSSE"


@pytest.mark.parametrize(
    "payload",
    [
        {"formula": "OSSE", "coverage_mode": "controlled\n}\nOSSE = {"},
        {"formula": "OSSE", "extra_keys": {"Future": "value\n}"}},
        {"formula": "OSSE", "extra_keys": {"Future": "value\u2028Length = 1"}},
        {"formula": "OSSE", "extra_keys": {"Future\n}": "value"}},
        {
            "formula": "OSSE",
            "extra_blocks": {"Report": {"entries": ["row\n}\nOSSE = {"]}},
        },
        {
            "formula": "OSSE",
            "extra_blocks": {"Report": {"entries": ["}"]}},
        },
        {
            "formula": "OSSE",
            "extra_blocks": {"Report": {"entries": ["OSSE = {"]}},
        },
        {
            "formula": "OSSE",
            "extra_blocks": {"Report": {"items": {"Title": "value\n}"}}},
        },
    ],
)
def test_serialize_rejects_structural_text_from_api_payloads(
    payload: dict[str, object],
) -> None:
    design = DesignConfig.model_validate(payload)

    with pytest.raises(TextConfigError):
        serialize(design)


def test_canonical_design_writes_v1_ath_directivity_blocks() -> None:
    design = DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "extra_blocks": {
                "Report": {"items": {"Title": '"kept"'}},
                "ABEC.Polars:SPL_H": {
                    "items": {
                        "MapAngleRange": "-30,90,13",
                        "NormAngle": "7",
                        "Distance": "3",
                    }
                },
                "ABEC.Polars:SPL_D": {
                    "items": {
                        "MapAngleRange": "-30,90,13",
                        "NormAngle": "7",
                        "Distance": "3",
                        "Inclination": "30",
                    }
                },
            },
        }
    )

    text = serialize(design)
    assert """ABEC.Polars:SPL_H = {
MapAngleRange = -30,90,13
NormAngle = 7
Distance = 3
}""" in text
    assert """ABEC.Polars:SPL_D = {
MapAngleRange = -30,90,13
NormAngle = 7
Distance = 3
Inclination = 30
}""" in text
    reopened = parse(text).design.root
    assert reopened.extra_blocks["Report"].items == {"Title": '"kept"'}
    assert reopened.extra_blocks["ABEC.Polars:SPL_D"].items["Inclination"] == "30"


@pytest.mark.parametrize("exponent", [6, "4 + 2*cos(p)"])
def test_morph_exponent_round_trips_through_exact_text_key(exponent: int | str) -> None:
    design = DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "morph": {"target_shape": 3, "target_exponent": exponent},
        }
    )

    text = serialize(design)
    assert f"Morph.Exponent = {exponent}\n" in text

    reopened = parse(text).design.root.morph.target_exponent
    assert reopened is not None
    assert reopened.text() == str(exponent)


def test_morph_exponent_is_mapped_while_unknown_key_remains_passthrough() -> None:
    parsed = parse(
        "OSSE = {\n}\nMorph.Exponent = 6\nMorph.FutureOption = alpha=beta\n"
    )

    exponent = parsed.design.root.morph.target_exponent
    assert exponent is not None
    assert exponent.value == 6
    assert "Morph.Exponent" not in parsed.extra_keys
    assert parsed.extra_keys["Morph.FutureOption"] == "alpha=beta"


@pytest.mark.parametrize(
    ("raw", "value"),
    [("6.35*2", 12.7), ("10 + 2*p", None)],
)
def test_osse_r0_raw_spelling_round_trips_schema_cfg_reopen_and_mesher(
    raw: str, value: float | None
) -> None:
    # This payload is the exact schema-wire Expr shape emitted by the UI.
    design = DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 120,
            "a": 45,
            "r0": {"value": value, "raw": raw},
        }
    )
    text = serialize(design)
    assert f"Throat.Diameter = 2*({raw})" in text

    reopened = parse(text).design
    assert reopened.root.r0 is not None
    assert reopened.root.r0.raw == raw
    assert reopened.root.r0.value == value
    assert design_to_mesher_config(reopened)["profile"]["r0"] == raw


@pytest.mark.parametrize(
    "source",
    [
        "OSSE = {\nL = 120\n",
        "Coverage.Angle = function anonymous(p\n) {\nreturn 45;\n",
    ],
)
def test_eof_with_unbalanced_block_or_function_is_a_parse_error(source: str) -> None:
    with pytest.raises(TextConfigError, match="unterminated"):
        parse(source)


@pytest.mark.parametrize(
    "source",
    [
        FREEFORM_SOURCE.replace("MouthRadius = 50\n", ""),
        FREEFORM_SOURCE.replace("0 ellipse\n", "0 ellipse 999\n"),
        FREEFORM_SOURCE.replace("1 ellipse\n", ""),
        FREEFORM_SOURCE.replace("1 ellipse\n", "0.5 mystery\n1 ellipse\n"),
    ],
)
def test_freeform_text_grammar_rejects_missing_or_illegal_rows(source: str) -> None:
    with pytest.raises(TextConfigError):
        parse(source)


def test_unknown_freeform_keys_and_blocks_remain_passthrough_data() -> None:
    source = FREEFORM_SOURCE + """Freeform.Future = raw=text
Freeform.FutureBlock = {
row one
Option = alpha
}
"""
    parsed = parse(source)
    assert parsed.extra_keys["Freeform.Future"] == "raw=text"
    assert parsed.extra_blocks["Freeform.FutureBlock"].entries == ["row one", "Option = alpha"]


def test_freeform_per_axis_throat_angles_round_trip_exactly() -> None:
    source = FREEFORM_SOURCE.replace(
        "MouthRadius = 50\n",
        "MouthRadius = 50\nThroatAngle = 12.5\n",
    ).replace(
        "MouthRadius = 40\n",
        "MouthRadius = 40\nThroatAngle = 27.25\n",
    )
    first = parse(source).design
    emitted = serialize(first)

    assert "Freeform.ThroatAngle = 12.5\n" in emitted
    assert "Freeform.H = {\nMouthRadius = 50\nThroatAngle = 12.5\n}" in emitted
    assert "Freeform.V = {\nMouthRadius = 40\nThroatAngle = 27.25\n}" in emitted

    reopened = parse(emitted).design.root
    assert reopened.profile_h.throat_angle_deg is not None  # type: ignore[union-attr]
    assert reopened.profile_v.throat_angle_deg is not None  # type: ignore[union-attr]
    assert reopened.profile_h.throat_angle_deg.text() == "12.5"  # type: ignore[union-attr]
    assert reopened.profile_v.throat_angle_deg.text() == "27.25"  # type: ignore[union-attr]


def test_legacy_top_level_freeform_throat_angle_applies_to_both_axes() -> None:
    source = FREEFORM_SOURCE.replace(
        "Freeform.ThroatRadius = 10\n",
        "Freeform.ThroatRadius = 10\nFreeform.ThroatAngle = 18.75\n",
    )
    design = parse(source).design.root
    assert design.profile_h.throat_angle_deg is not None  # type: ignore[union-attr]
    assert design.profile_v.throat_angle_deg is not None  # type: ignore[union-attr]
    assert design.profile_h.throat_angle_deg.text() == "18.75"  # type: ignore[union-attr]
    assert design.profile_v.throat_angle_deg.text() == "18.75"  # type: ignore[union-attr]


def test_legacy_freeform_controls_load_and_rewrite_without_removed_keys() -> None:
    source = """; Parameter config
Freeform.Length = 100
Freeform.ThroatRadius = 10
Freeform.OvershootPolicy = allow
Freeform.H = {
MouthRadius = 50
ThroatTangentScale = 1.2
MouthTangentScale = 0.8
}
Freeform.H.Points = {
50 25 20 1.5
}
Freeform.V = {
MouthRadius = 40
ThroatTangentScale = 1.1
MouthTangentScale = 0.9
}
Freeform.V.Points = {
50 22 15 0.7
}
Freeform.CrossSections = {
0 circle
1 ellipse
}
"""
    parsed = parse(source)
    assert parsed.migration_names == ["004_freeform_solved_tangent_contract"]
    emitted = serialize(parsed)
    for removed in (
        "Freeform.OvershootPolicy",
        "ThroatTangentScale",
        "MouthTangentScale",
        "strength",
        " 1.5",
        " 0.7",
        "circle",
    ):
        assert removed not in emitted
    assert "; FREEFORM point rows in mm: z r [angleDeg]" in emitted
    assert "50 25 20" in emitted
    assert "50 22 15" in emitted
    assert "0 ellipse" in emitted
    assert parse(emitted).migration_names == []


def test_freeform_mm_point_rows_round_trip_without_noise_or_drops() -> None:
    source = FREEFORM_SOURCE.replace(
        "Freeform.H = {\nMouthRadius = 50\n}",
        "Freeform.H = {\nMouthRadius = 50\n}\nFreeform.H.Points = {\n33.3 22.25 17\n70 35\n}",
    ).replace(
        "Freeform.V = {\nMouthRadius = 40\n}",
        "Freeform.V = {\nMouthRadius = 40\n}\nFreeform.V.Points = {\n25 18\n69.125 31.5 -12\n}",
    )
    parsed = parse(source)
    design = parsed.design.root
    assert design.length.value == 100  # type: ignore[union-attr]
    assert design.profile_h.points[1].t.value == pytest.approx(0.333)  # type: ignore[union-attr]

    emitted = serialize(parsed.design)
    for row in ("33.3 22.25 17", "70 35", "25 18", "69.125 31.5 -12"):
        assert row in emitted
    assert "69.124999999" not in emitted
    reopened = parse(emitted).design.root
    assert reopened.profile_h == design.profile_h  # type: ignore[union-attr]
    assert reopened.profile_v == design.profile_v  # type: ignore[union-attr]


def test_modified_design_preserves_duplicate_extra_assignments_and_token_order() -> None:
    source = SOURCE + """Ordered = {
first row
Value = one
; between duplicates
Value = two
last row
}
"""
    parsed = parse(source)
    block = parsed.extra_blocks["Ordered"]
    assert block.items == {"Value": "two"}
    assert block.entries == [
        "first row",
        "Value = one",
        "; between duplicates",
        "Value = two",
        "last row",
    ]
    parsed.design.root.L = Expr(value=121)  # type: ignore[union-attr]
    emitted = serialize(parsed)
    expected = """Ordered = {
first row
Value = one
; between duplicates
Value = two
last row
}"""
    assert expected in emitted
