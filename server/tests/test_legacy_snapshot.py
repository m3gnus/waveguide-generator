"""Regression coverage for reopening v1 job parameter snapshots."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from server.design.legacy_snapshot import (
    LegacySnapshotError,
    resolve_legacy_params,
    snapshot_to_ath_text,
    snapshot_to_design,
)
from server.design.textcfg import ParsedDesign


# Sibling to this checkout, the way every other v1-corpus test locates it. An
# absolute path would exist on exactly one machine.
DB_PATH = (
    Path(__file__).resolve().parents[2].parent
    / "Waveguide Generator"
    / "server"
    / "data"
    / "simulations.db"
)
SUPPORTED_SNAPSHOT_FAMILIES = frozenset({"OSSE", "R-OSSE"})


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
    """Read the live v1 database without being able to write to it.

    ``sqlite3.connect(path)`` opens read-write, and using the connection as a
    context manager commits on exit -- which bumped the file change counter in
    the header of a 109 MB database this suite has no business touching, three
    times per run. ``mode=ro`` makes that impossible rather than unlikely, and
    ``closing`` releases the handle instead of leaving it to the collector.
    """

    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        return [
            json.loads(row[0])
            for row in connection.execute(
                "select script_snapshot_json from simulation_jobs "
                "where script_snapshot_json is not null"
            )
        ]
    finally:
        connection.close()


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


@pytest.mark.parametrize(
    ("snapshot", "expected_sha256"),
    [
        (
            INLINE_OSSE,
            "062a24da5b1f559c54fbb29eef15bcde4d6db2646a1509064e10d62ee412435c",
        ),
        (
            INLINE_ROSSE,
            "1ebb6d3c1eb60a0c4055e10c4379fb0c3a67e9dabecbddbf313a941084044c48",
        ),
    ],
    ids=("OSSE", "R-OSSE"),
)
def test_supported_families_keep_their_exact_ath_output(
    snapshot: dict[str, Any], expected_sha256: str
) -> None:
    text = snapshot_to_ath_text(snapshot["params"])
    assert hashlib.sha256(text.encode()).hexdigest() == expected_sha256


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


@pytest.mark.parametrize(
    ("r0", "expected"),
    [
        (12.7, "25.4"),
        ("12.7", "25.4"),
        (0, "0"),
        (12.699882, "25.399764"),
        (None, "0"),
        ("NaN", "NaN"),
        ("undefined", "NaN"),
        ("function anonymous(p\n) {\nreturn 12.7;\n}", "NaN"),
    ],
)
def test_numeric_throat_radius_keeps_the_historical_doubled_literal(r0: Any, expected: str) -> None:
    """Byte-for-byte guard on the pre-formula spelling, v1 mwgConfig.js parity."""

    params = dict(INLINE_OSSE["params"], r0=r0)
    assert f"\nThroat.Diameter = {expected}\n" in snapshot_to_ath_text(params)


@pytest.mark.parametrize("raw", ["6.35*2", "10 + 2*p", "12.7 - 2*sin(p)^2"])
def test_formula_throat_radius_exports_a_doubling_expression(raw: str) -> None:
    text = snapshot_to_ath_text(dict(INLINE_OSSE["params"], r0=raw))
    assert "Throat.Diameter = NaN" not in text
    assert f"\nThroat.Diameter = 2*({raw})\n" in text


def _import_r0(diameter: str) -> Any:
    from server.design.textcfg import parse as parse_text

    text = "\n".join(
        [
            "; Parameter config",
            "Coverage.Angle = 45",
            "Length = 120",
            "Term.n = 4",
            "Term.q = 1",
            "Term.s = 0.6",
            "Throat.Angle = 15.5",
            f"Throat.Diameter = {diameter}",
            "OS.k = 7",
            "",
        ]
    )
    r0 = parse_text(text).design.root.r0
    assert r0 is not None
    return r0


@pytest.mark.parametrize(
    ("diameter", "expected"),
    [
        ("2*(10 + 2*p)", "10 + 2*p"),
        ("25.4*cos(p)", "(25.4*cos(p)) / 2"),
        ("50 - 10*sin(p)^2", "(50 - 10*sin(p)^2) / 2"),
    ],
)
def test_formula_throat_diameter_imports_as_a_halved_radius(diameter: str, expected: str) -> None:
    assert _import_r0(diameter).raw == expected


@pytest.mark.parametrize(
    ("diameter", "expected"),
    [
        # A greedy unwrap stopped at the last ')' and produced the unbalanced
        # fragment 'a)*(b'. These are products, not the writer's doubling spelling.
        ("2*(a)*(b)", "(2*(a)*(b)) / 2"),
        ("2*(p)*(1 + k)", "(2*(p)*(1 + k)) / 2"),
        # Nested parens that really are one group still unwrap.
        ("2*((a+b))", "(a+b)"),
        ("2*(a*(b+c))", "a*(b+c)"),
        # Trailing text after the matching paren disqualifies the unwrap.
        ("2*(a) + 3", "(2*(a) + 3) / 2"),
        # An unterminated group is not the generated spelling either.
        ("2*(a", "(2*(a) / 2"),
    ],
)
def test_hand_written_doubling_only_unwraps_when_the_parens_balance(
    diameter: str, expected: str
) -> None:
    assert _import_r0(diameter).raw == expected


def test_unbalanced_doubling_survives_a_second_export_import_lap() -> None:
    from server.design.textcfg import parse as parse_text

    halved = _import_r0("2*(a)*(b)").raw
    assert halved == "(2*(a)*(b)) / 2"
    text = snapshot_to_ath_text(dict(INLINE_OSSE["params"], r0=halved))
    assert f"\nThroat.Diameter = 2*({halved})\n" in text
    assert parse_text(text).design.root.r0.raw == halved


@pytest.mark.parametrize("raw", ["6.35*2", "10 + 2*p"])
def test_formula_throat_radius_survives_a_snapshot_export_import_round_trip(raw: str) -> None:
    parsed = snapshot_to_design(dict(INLINE_OSSE["params"], r0=raw))
    r0 = parsed.design.root.r0
    assert r0 is not None
    assert r0.raw == raw

    # A second lap is a fixed point, so repeated save/open does not grow the text.
    from server.design.textcfg import parse as parse_text
    from server.design.textcfg import serialize

    reparsed = parse_text(serialize(parsed.design)).design.root.r0
    assert reparsed is not None
    assert reparsed.raw == raw


# Every ATH key whose value reaches the writer through a bare `${...}` template,
# so a JSON null in the snapshot lands in the file as the literal text "null".
# Confirmed field by field against v1 generateMWGConfigContent over the inline
# fixtures and every row of the live v1 job database.
_NULL_INTERPOLATED_OSSE = (
    ("a", "Coverage.Angle"),
    ("L", "Length"),
    ("n", "Term.n"),
    ("q", "Term.q"),
    ("s", "Term.s"),
    ("a0", "Throat.Angle"),
    ("k", "OS.k"),
    ("angularSegments", "Mesh.AngularSegments"),
    ("lengthSegments", "Mesh.LengthSegments"),
)
_NULL_INTERPOLATED_ROSSE = (
    ("R", "R"),
    ("a", "a"),
    ("a0", "a0"),
    ("b", "b"),
    ("k", "k"),
    ("m", "m"),
    ("q", "q"),
    ("r", "r"),
    ("r0", "r0"),
    ("tmax", "tmax"),
    ("angularSegments", "Mesh.AngularSegments"),
    ("lengthSegments", "Mesh.LengthSegments"),
)
_NULL_INTERPOLATED_ENCLOSURE = (
    ("encEdge", "EdgeRadius"),
    ("encEdgeType", "EdgeType"),
)


@pytest.mark.parametrize(
    ("base", "param", "key"),
    [
        *(("OSSE", param, key) for param, key in _NULL_INTERPOLATED_OSSE),
        *(("R-OSSE", param, key) for param, key in _NULL_INTERPOLATED_ROSSE),
        *(("ENCLOSURE", param, key) for param, key in _NULL_INTERPOLATED_ENCLOSURE),
    ],
)
def test_json_null_is_written_as_null_not_undefined(base: str, param: str, key: str) -> None:
    """v1 interpolates ``${null}`` as "null"; only a missing key spells "undefined"."""

    fixtures = {"OSSE": INLINE_OSSE, "R-OSSE": INLINE_ROSSE, "ENCLOSURE": INLINE_ENCLOSURE}
    params = dict(fixtures[base]["params"], **{param: None})
    text = snapshot_to_ath_text(params)
    assert f"\n{key} = null\n" in text
    assert f"\n{key} = undefined\n" not in text


@pytest.mark.parametrize("base", ["OSSE", "R-OSSE", "ENCLOSURE"])
def test_a_missing_key_still_spells_undefined(base: str) -> None:
    """The undefined spelling is reserved for an absent property, as in v1."""

    fixtures = {"OSSE": INLINE_OSSE, "R-OSSE": INLINE_ROSSE, "ENCLOSURE": INLINE_ENCLOSURE}
    params = {k: v for k, v in fixtures[base]["params"].items() if k != "angularSegments"}
    assert "\nMesh.AngularSegments = undefined\n" in snapshot_to_ath_text(params)


def test_null_passthrough_block_lines_join_as_empty_text() -> None:
    """``Array.prototype.join`` spells null "", unlike an ``${...}`` item value."""

    params = json.loads(json.dumps(INLINE_ENCLOSURE["params"]))
    params["_blocks"]["Report"]["_lines"] = ["report row", None, "second row"]
    params["_blocks"]["Report"]["_items"]["PolarData"] = None
    text = snapshot_to_ath_text(params)
    assert "\nreport row\n\nsecond row\n" in text
    assert "\nPolarData = null\n" in text
    assert "= undefined" not in text[text.index("Report = {") :]


@pytest.mark.parametrize(
    ("param", "path"),
    [("a", ("a",)), ("L", ("L",)), ("angularSegments", ("mesh", "angular_segments"))],
)
def test_null_lines_are_dropped_on_load_like_undefined_and_nan(
    param: str, path: tuple[str, ...]
) -> None:
    """Migration 003 has to cover "null" too, or it validates as a value-less Expr.

    Without it the field survives as ``Expr(value=None, raw='null')``, which is a
    non-finite coordinate by the time it reaches the mesher.
    """

    parsed = snapshot_to_design(dict(INLINE_OSSE["params"], **{param: None}))
    assert "003_js_undefined_lines_dropped" in parsed.migration_names
    field: Any = parsed.design.root
    for name in path:
        field = getattr(field, name)
    assert field is None


# Every expectation below was read off `node -e 'Number(...)'`, not guessed.
# `float()` is the looser of the two: it takes "inf", "nan", digit separators,
# and non-ASCII decimal digits, none of which are JavaScript numeric literals.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("inf", "NaN"),
        ("-inf", "NaN"),
        ("infinity", "NaN"),
        ("INFINITY", "NaN"),
        ("nan", "NaN"),
        ("NaN", "NaN"),
        ("Infinity", "Infinity"),
        ("+Infinity", "Infinity"),
        ("-Infinity", "-Infinity"),
        ("1_000", "NaN"),
        ("١٢", "NaN"),
        ("", "0"),
        ("   ", "0"),
        (" 12.5 ", "12.5"),
        ("﻿12", "12"),
        ("0x1F", "31"),
        ("0X1f", "31"),
        ("-0x10", "NaN"),
        ("+0x10", "NaN"),
        ("0b101", "5"),
        ("0o17", "15"),
        ("017", "17"),
        ("1.", "1"),
        (".5", "0.5"),
        ("1e400", "Infinity"),
    ],
)
def test_js_number_matches_javascript_number_coercion(text: str, expected: str) -> None:
    from server.design.legacy_snapshot import _js_number, _js_string

    assert _js_string(_js_number(text)) == expected


def test_freeform_legacy_snapshot_requires_reentry() -> None:
    with pytest.raises(LegacySnapshotError, match="FREEFORM legacy snapshots are not supported"):
        snapshot_to_design(INLINE_FREEFORM)


@pytest.mark.parametrize("family", ["ICW", "LOOKUP"])
def test_unsupported_legacy_family_is_refused_by_name(family: str) -> None:
    params = dict(INLINE_OSSE["params"], type=family)
    with pytest.raises(LegacySnapshotError) as caught:
        snapshot_to_ath_text(params)
    message = str(caught.value)
    assert family in message
    assert "cannot be recovered faithfully" in message
    assert "must be re-entered" in message


@pytest.mark.parametrize("family", [None, "FUTURE"], ids=("missing", "unknown"))
def test_missing_or_unknown_legacy_family_is_not_assumed_to_be_osse(
    family: str | None,
) -> None:
    params = dict(INLINE_OSSE["params"])
    if family is None:
        params.pop("type")
    else:
        params["type"] = family
    with pytest.raises(LegacySnapshotError, match="must be re-entered"):
        snapshot_to_ath_text(params)


@pytest.mark.skipif(not DB_PATH.exists(), reason="v1 simulation database is not available")
def test_every_real_snapshot_is_converted_or_refused_by_family() -> None:
    """Every snapshot converts faithfully or is refused before ATH is written.

    This database is Magnus's working solve history, so it grows and shrinks as
    he runs and deletes jobs. Asserting a fixed count made the suite fail the
    first time one was deleted, which says nothing about the converter. What
    matters is that supported families convert and every other family fails by
    name; the inline fixtures carry the fixed expectations.
    """

    snapshots = _db_snapshots()
    assert snapshots, "the v1 database exists but holds no snapshots"
    families = {snapshot["params"].get("type") for snapshot in snapshots}
    assert {"OSSE", "R-OSSE"} <= families, f"corpus lost a family: {families}"
    for snapshot in snapshots:
        family = snapshot["params"].get("type")
        if family in SUPPORTED_SNAPSHOT_FAMILIES:
            assert isinstance(snapshot_to_design(snapshot), ParsedDesign)
            continue
        with pytest.raises(LegacySnapshotError) as caught:
            snapshot_to_design(snapshot)
        if family:
            assert str(family) in str(caught.value)
        assert "must be re-entered" in str(caught.value)


def _assert_field_matches(parsed: ParsedDesign, name: str, expected: Any) -> None:
    """Compare one field against the bag, in whichever form the bag holds it.

    Formula-valued fields have no scalar to compare, so they are checked as
    text -- which is the stronger assertion of the two, because it is exactly
    the material the prepared-parameter bag threw away.
    """

    field = getattr(parsed.design.root, name)
    assert field is not None, name
    if isinstance(expected, str) and not expected.strip().replace(".", "", 1).isdigit():
        assert field.raw == expected, name
    else:
        assert field.value == pytest.approx(float(expected)), name


@pytest.mark.skipif(not DB_PATH.exists(), reason="v1 simulation database is not available")
def test_real_family_values_are_preserved() -> None:
    snapshots = _db_snapshots()
    by_type = {
        formula: next(snapshot for snapshot in snapshots if snapshot["params"].get("type") == formula)
        for formula in ("OSSE", "R-OSSE")
    }
    for formula, snapshot in by_type.items():
        # The bag v1 itself would have reopened, not its derived mesher copy:
        # that copy has no R, a or k at all on most of this corpus.
        params, source = resolve_legacy_params(snapshot)
        assert source == "design-state"
        parsed = snapshot_to_design(snapshot)
        for name in ("r0", "a0", "q", "a", "L" if formula == "OSSE" else "R"):
            _assert_field_matches(parsed, name, params[name])


@pytest.mark.skipif(not DB_PATH.exists(), reason="v1 simulation database is not available")
def test_no_real_formula_field_is_lost_on_the_way_back_in() -> None:
    """Every expression the live corpus stores has to survive the round trip.

    Reading v1's prepared bag instead of its design state dropped these
    silently: the job still opened, and drew a different waveguide.
    """

    checked = 0
    for snapshot in _db_snapshots():
        params, _ = resolve_legacy_params(snapshot)
        if params.get("type") not in SUPPORTED_SNAPSHOT_FAMILIES:
            continue
        parsed = snapshot_to_design(snapshot)
        for name in ("L", "R", "a", "a0", "k", "q", "s", "n", "m", "b", "r"):
            raw = params.get(name)
            if not isinstance(raw, str) or raw.strip().replace(".", "", 1).isdigit():
                continue
            field = getattr(parsed.design.root, name, None)
            assert field is not None, f"{name} was dropped"
            assert field.raw == raw
            checked += 1
    assert checked, "the corpus lost every formula-valued field it used to hold"


@pytest.mark.skipif(not DB_PATH.exists(), reason="v1 simulation database is not available")
def test_real_enclosures_are_preserved() -> None:
    snapshots = [
        snapshot
        for snapshot in _db_snapshots()
        if snapshot["params"].get("type") in SUPPORTED_SNAPSHOT_FAMILIES
        and snapshot["params"].get("encDepth", 0) > 0
    ]
    assert snapshots, "no enclosure snapshots left in the live database to check"
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
