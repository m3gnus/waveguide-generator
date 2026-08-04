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
    assert text.startswith("; Parameter config\n; Waveguide Generator v2 design-format: 2\n")
    assert text.index("Coverage.Angle") < text.index("Length") < text.index("Term.n")
    assert parse(text).design.formula == "OSSE"


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
    assert "; FREEFORM point rows: z r [angleDeg]" in emitted
    assert "50 25 20" in emitted
    assert "50 22 15" in emitted
    assert "0 ellipse" in emitted
    assert parse(emitted).migration_names == []


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
