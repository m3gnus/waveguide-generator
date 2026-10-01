"""Feature-off ROSSE documents retain the pre-C4 interpretation."""
import pytest

from server.design.textcfg import parse, serialize


@pytest.mark.parametrize("prefix", ["", "; MWG config\n"])
@pytest.mark.parametrize("coefficients", ["", "s1 = 0\ns2 = 0"])
@pytest.mark.parametrize("top", ["Length = 180", "Length = 180\nCoverage.Angle = 40"])
def test_zero_absent_alias_preserves_accepted_base_design(prefix, coefficients, top):
    text = prefix + "ROSSE = {\nR = 200\n" + coefficients + "\n}\n" + top
    # Executed base 6db729c4 parses these documents as OSSE, keeping ROSSE.
    parsed = parse(text)
    design = parsed.design
    assert design.formula == "OSSE"
    assert design.root.L.constant_value() == 180
    assert design.extra_blocks["ROSSE"].items == {
        "R": "200", **({"s1": "0", "s2": "0"} if coefficients else {})
    }
    assert serialize(parsed) == text
    saved = serialize(design)
    assert "ROSSE = {" in saved
    reopened = parse(saved).design
    assert reopened.formula == "OSSE"
    assert reopened.root.L.constant_value() == 180
    assert reopened.extra_blocks["ROSSE"].items == design.extra_blocks["ROSSE"].items
