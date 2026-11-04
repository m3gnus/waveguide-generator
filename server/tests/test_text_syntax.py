"""Keep expression validation and text serialization on the same syntax boundary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.design.schema import DesignConfig, Expr
from server.design.textcfg import TextConfigError, parse, serialize


LEGACY_FUNCTION = "function anonymous(p\n) {\nreturn 45 - 2*Math.cos(p)**2;\n}"
LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"


@pytest.mark.parametrize(
    "raw",
    [
        LEGACY_FUNCTION,
        LEGACY_FUNCTION + " trailing text on the closing line",
        "function anonymous(p\n) {\nif (p) {\nreturn 45;\n}\n}",
        LEGACY_FUNCTION.replace("\n", "\u2028"),
        "45 + 10*cos(p)^2",
    ],
)
def test_both_public_paths_preserve_complete_expression_values(raw: str) -> None:
    expression = Expr.model_validate(raw)
    assert expression.raw == raw
    design = DesignConfig.model_validate({"formula": "OSSE", "extra_keys": {"Future.Value": raw}})
    assert f"Future.Value = {raw}\n" in serialize(design)


@pytest.mark.parametrize("line_break", LINE_BREAKS)
def test_both_public_paths_refuse_trailing_and_injected_line_breaks(line_break: str) -> None:
    for raw in (LEGACY_FUNCTION + line_break, f"45{line_break}Length = 1"):
        with pytest.raises(ValidationError, match="expression source must not contain structural newlines"):
            Expr.model_validate(raw)
        design = DesignConfig.model_validate({"formula": "OSSE", "extra_keys": {"Future.Value": raw}})
        with pytest.raises(TextConfigError, match="value for 'Future.Value' contains a structural newline"):
            serialize(design)


@pytest.mark.parametrize(
    ("raw", "schema_error", "writer_error"),
    [
        (LEGACY_FUNCTION + "\ntrailing", "structural newlines", "structural newline"),
        (LEGACY_FUNCTION[:-1], "structural newlines", "structural newline"),
        (LEGACY_FUNCTION + "}", "structural newlines", "structural newline"),
        ("{45", "balanced braces", "unbalanced braces"),
        ("45}", "balanced braces", "unbalanced braces"),
        ("} {", "balanced braces", "unbalanced braces"),
        ("function anonymous(p) {}", "complete multiline value", "incomplete legacy function"),
        ("OSSE = { ; }", "config block syntax", "config block syntax"),
        ("} ; {", "balanced braces", "unbalanced braces"),
    ],
)
def test_both_public_paths_keep_their_existing_refusals(
    raw: str, schema_error: str, writer_error: str,
) -> None:
    with pytest.raises(ValidationError, match=schema_error):
        Expr.model_validate(raw)
    design = DesignConfig.model_validate({"formula": "OSSE", "extra_keys": {"Future.Value": raw}})
    with pytest.raises(TextConfigError, match=writer_error):
        serialize(design)


def test_legacy_file_bytes_and_captured_block_names_survive_both_writer_paths() -> None:
    source = f"; MWG config\nCoverage.Angle = {LEGACY_FUNCTION}\nLength = 120\nThroat.Profile = 1\nFuture.Block:1 = {{\nName = kept\n}}\n"
    parsed = parse(source)
    assert serialize(parsed).encode() == source.encode()
    assert parsed.extra_blocks["Future.Block:1"].items == {"Name": "kept"}
    canonical = serialize(parsed.design)
    reopened = parse(canonical)
    assert reopened.design.root.a.raw == LEGACY_FUNCTION
    assert reopened.design.root.a.model_dump() == parsed.design.root.a.model_dump()
    assert serialize(reopened.design).encode() == canonical.encode()


def test_shared_syntax_recognizes_blocks_and_complete_legacy_functions() -> None:
    from server.design.text_syntax import (
        BLOCK_START, LINE_BREAK, has_balanced_braces, is_complete_legacy_function,
        line_is_config_block_syntax,
    )

    assert BLOCK_START.fullmatch("Future.Block:1 = {").group(1) == "Future.Block:1"
    assert all(LINE_BREAK.fullmatch(character) for character in LINE_BREAKS)
    assert has_balanced_braces("{{}}")
    assert not has_balanced_braces("}{")
    assert is_complete_legacy_function(LEGACY_FUNCTION)
    assert not is_complete_legacy_function(LEGACY_FUNCTION + "\n")
    assert line_is_config_block_syntax("Future.Block:1 = { ; comment")
    assert line_is_config_block_syntax("} ; comment")
    assert not line_is_config_block_syntax("name = value ; {")
