"""Dormant ROSSE regressions from the independent round-three review.

Base WG is the oracle for legacy documents. The mesher importer remains the
oracle for canonical OSSE/R-OSSE and genuinely active ROSSE imports.
"""
import numpy as np
import pytest

from server.design.textcfg import TextConfigError, parse, serialize
from server.preview.translate import design_to_mesher_config


_DORMANT = ["s1 = 0\ns2 = .2", "s1 = .5\ns2 = 0", "s1 = .5", "s2 = .2"]


def _source(prefix, coeff, top):
    return prefix + "ROSSE = {\nR = 60\nr0 = 6\n" + coeff + "\n}\n" + top


@pytest.mark.parametrize("prefix", ["", "; MWG config\n"])
@pytest.mark.parametrize("coeff", _DORMANT)
def test_inactive_rosse_keeps_base_family(prefix, coeff, monkeypatch):
    source = _source(prefix, coeff, "Length = 60\nThroat.Diameter = 12\n"
                     "Coverage.Angle = 30\nThroat.Angle = 5\nOS.k = 1")
    parsed = parse(source)
    d = parsed.design
    assert d.formula == "OSSE"
    assert d.root.L.constant_value() == 60
    assert d.root.s1 is None and d.root.s2 is None
    assert d.extra_blocks["ROSSE"].items == {
        "R": "60", "r0": "6", **dict(line.split(" = ") for line in coeff.splitlines()),
    }
    assert serialize(parsed) == source
    saved = serialize(d)
    assert "ROSSE = {" in saved
    reopened = parse(saved).design
    assert reopened.formula == d.formula
    assert reopened.root.L.constant_value() == 60
    assert reopened.extra_blocks["ROSSE"].items == d.extra_blocks["ROSSE"].items
    # An ignored legacy coefficient must never reach the old-pin refusal.
    monkeypatch.setattr("server.design.throat_stretch.mesher_supports_stretch", lambda: False)
    config = design_to_mesher_config(d)
    assert config["formula"] == "OSSE"
    assert "s1" not in config["profile"] and "s2" not in config["profile"]
    from hornlab_mesher.config_builder import build_geometry_params, build_point_grid_arrays

    def grid(config):
        config = {**config, "mesh": {"wallThickness": 0, "angularSegments": 8, "lengthSegments": 8}}
        return build_point_grid_arrays(build_geometry_params(config)[0])["inner_grid"]

    np.testing.assert_array_equal(grid(design_to_mesher_config(reopened)), grid(config))


@pytest.mark.parametrize("prefix", ["", "; MWG config\n"])
@pytest.mark.parametrize("coeff", _DORMANT)
@pytest.mark.parametrize("top", ["", "Coverage.Angle = 50", "Term.n = 6"])
def test_dormant_rosse_uses_base_family_detection(prefix, coeff, top):
    source = _source(prefix, coeff, top)
    if not top:
        # Base never recognized ROSSE alone as a profile. A dormant coefficient
        # cannot make an otherwise unsupported document select a new family.
        with pytest.raises(TextConfigError, match="could not find an OSSE"):
            parse(source)
    else:
        d = parse(source).design
        assert d.formula == "OSSE"
        assert "ROSSE" in d.extra_blocks


@pytest.mark.parametrize("key,other", [("s1", "s2"), ("s2", "s1")])
@pytest.mark.parametrize("value,reason", [
    ("-.1", "must not be negative"), ("11", "<= 10"),
    ("nan", "plain finite number"), ("0*p", "per-azimuth"),
])
@pytest.mark.parametrize("other_value", [None, "0", ".2"])
def test_rosse_validates_coefficients_even_when_dormant(key, other, value, reason, other_value):
    coeff = f"{key} = {value}" + (f"\n{other} = {other_value}" if other_value is not None else "")
    with pytest.raises(TextConfigError, match=reason):
        parse(_source("", coeff, "Length = 60"))
