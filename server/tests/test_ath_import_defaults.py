"""ATH V2026-08c simulation, polar origin, and mesh dimension defaults."""

from pathlib import Path

import pytest

from server.cli.request import build_request
from server.design.schema import DesignConfig
from server.design.solve_block import solve_options_from_blocks
from server.design.text_import import GEOMETRY_STAMP_LABEL, TEXT_IMPORT_VERSION
from server.design.textcfg import parse, serialize
from server.preview.translate import design_to_mesher_config


FIXTURES = Path(__file__).parent / "fixtures" / "ath_import_defaults"


def _text(name: str) -> str:
    return (FIXTURES / f"{name}.cfg").read_text(encoding="utf-8")


def _stamp(text: str, version: str = TEXT_IMPORT_VERSION) -> str:
    return f"; {GEOMETRY_STAMP_LABEL} {version}\n{text}"


def test_omitted_simtype_matches_ath_infinite_baffle_default() -> None:
    parsed = parse(_text("osse_no_simtype"))
    assert parsed.design.root.simulation.sim_type == "infinite-baffle"
    config = design_to_mesher_config(parsed.design)
    assert config["mode"] == "infinite-baffle"
    assert config["mesh"]["wallThickness"] == 0


@pytest.mark.parametrize("key", ["ABEC.SimType", "Simulation.SimType"])
def test_explicit_simtype_keeps_free_standing_wall_default(key: str) -> None:
    parsed = parse(_text("osse_no_simtype") + f"{key} = 2\n")
    assert parsed.design.root.simulation.sim_type == "freestanding"
    config = design_to_mesher_config(parsed.design)
    assert config["mode"] == "freestanding"
    assert config["mesh"]["wallThickness"] == 5


@pytest.mark.parametrize(
    "prefix", [f"; {GEOMETRY_STAMP_LABEL} {TEXT_IMPORT_VERSION}\n", "; Parameter config\n"]
)
def test_saved_or_native_text_keeps_omitted_simtype(prefix: str) -> None:
    parsed = parse(prefix + _text("osse_no_simtype"))
    assert parsed.design.root.simulation.sim_type is None


def test_freeform_keeps_omitted_simtype_without_import_defaults() -> None:
    text = """Freeform.Length = 100
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
    parsed = parse(text)
    assert parsed.design.root.text_import_version is None
    assert parsed.design.root.simulation.sim_type is None


@pytest.mark.parametrize(
    "enclosure", ["Mesh.Enclosure = {\n Depth = 0\n}\n", "Mesh.Enclosure.Depth = 0\n"]
)
def test_enclosure_items_keep_omitted_simtype(enclosure: str) -> None:
    parsed = parse(_text("osse_no_simtype") + enclosure)
    assert parsed.design.root.simulation.sim_type is None
    assert design_to_mesher_config(parsed.design)["mesh"]["wallThickness"] == 5


def test_empty_enclosure_does_not_replace_baffle_default() -> None:
    parsed = parse(_text("osse_no_simtype") + "Mesh.Enclosure = {\n}\n")
    assert parsed.design.root.simulation.sim_type == "infinite-baffle"


def test_baffle_default_survives_serialization() -> None:
    parsed = parse(_text("osse_no_simtype"))
    written = serialize(parsed.design)
    assert "Simulation.SimType = 1" in written
    assert parse(written).design.root.simulation.sim_type == "infinite-baffle"


@pytest.mark.parametrize(
    ("name", "origin"),
    [("osse_freestanding_polars_unstated", "throat"), ("osse_baffle_polars_unstated", "mouth")],
)
def test_imported_polars_origin_matches_simulation(name: str, origin: str) -> None:
    options = build_request(parse(_text(name))).request.options
    assert options.polar_config.observation_origin == origin


def test_stated_polar_values_are_applied_with_throat_origin() -> None:
    options = build_request(parse(_text("osse_freestanding_polars_stated"))).request.options
    assert options.polar_config.distance == 2
    assert options.polar_config.norm_angle == 0
    assert options.polar_config.angle_range == (0, 90, 19)
    assert options.polar_config.observation_origin == "throat"


@pytest.mark.parametrize("origin", ["mouth", "throat", "unknown"])
def test_wg_origin_overrides_import_default_when_valid(origin: str) -> None:
    text = _text("osse_freestanding_polars_unstated")
    text += f"WG.Solve = {{\n ObservationOrigin = {origin}\n}}\n"
    options = build_request(parse(text)).request.options
    assert options.polar_config.observation_origin == ("mouth" if origin == "mouth" else "throat")


@pytest.mark.parametrize(
    "prefix", ["; Parameter config\n", f"; {GEOMETRY_STAMP_LABEL} native-v1\n"]
)
def test_native_polars_keep_existing_origin(prefix: str) -> None:
    parsed = parse(prefix + _text("osse_freestanding_polars_unstated"))
    assert parsed.design.root.text_import_version is None
    assert build_request(parsed).request.options.polar_config.observation_origin == "mouth"
    assert parsed.ignored_settings == []


def test_saved_import_polars_use_import_origin_without_new_notes() -> None:
    parsed = parse(_stamp(_text("osse_freestanding_polars_unstated")))
    assert build_request(parsed).request.options.polar_config.observation_origin == "throat"
    assert parsed.ignored_settings == []


def test_solve_origin_accepts_design_root_and_keeps_base_without_polars() -> None:
    parsed = parse(_text("osse_freestanding_polars_unstated"))
    assert (
        solve_options_from_blocks(
            parsed.extra_blocks, design=parsed.design.root
        ).polar_config.observation_origin
        == "throat"
    )
    base = solve_options_from_blocks({"WG.Solve": {"items": {"ObservationOrigin": "mouth"}}})
    assert solve_options_from_blocks({}, base, design=parsed.design) == base


def test_unstated_polars_report_measurement_limits() -> None:
    notes = parse(_text("osse_freestanding_polars_unstated")).ignored_settings
    assert [note.key for note in notes] == [
        "ABEC.Polars:SPL_H.Distance",
        "ABEC.Polars:SPL_H.NormAngle",
    ]
    assert all(note.value == "" for note in notes)
    assert "far field" in notes[0].note
    assert "2 m unless changed" in notes[0].note
    assert "unnormalised" in notes[1].note
    assert "5 degrees unless changed" in notes[1].note


def test_stated_distance_and_angle_need_no_notes() -> None:
    assert parse(_text("osse_freestanding_polars_stated")).ignored_settings == []


@pytest.mark.parametrize(
    ("simtype", "offset", "reported"),
    [(2, "50", True), (2, "0", False), (2, "0x0", False), (1, "0", True), (1, "50", True)],
)
def test_offset_report_matches_available_origin(simtype: int, offset: str, reported: bool) -> None:
    text = _text("osse_freestanding_polars_offset").replace(
        "ABEC.SimType = 2", f"ABEC.SimType = {simtype}"
    )
    text = text.replace("Offset = 50", f"Offset = {offset}")
    notes = parse(text).ignored_settings
    assert len(notes) == int(reported)
    if reported:
        assert notes[0].key == "ABEC.Polars:SPL_H.Offset"
        assert notes[0].value == offset
        assert ("mouth" if simtype == 1 else "throat") in notes[0].note
        assert "does not apply Offset" in notes[0].note


def test_notes_use_first_polar_block_in_case_insensitive_order() -> None:
    text = _text("osse_freestanding_polars_unstated")
    text += "ABEC.Polars:a = {\n Distance = 3\n NormAngle = 0\n Offset = 25\n}\n"
    notes = parse(text).ignored_settings
    assert [(note.key, note.value) for note in notes] == [("ABEC.Polars:a.Offset", "25")]


def test_offset_notes_scan_every_polar_block() -> None:
    text = _text("osse_freestanding_polars_stated")
    text += "ABEC.Polars:SPL_V = {\n Offset = 50\n}\n"
    text += "ABEC.Polars:SPL_W = {\n Offset = 25\n}\n"
    text += "ABEC.Polars:SPL_Z = {\n Offset = 0\n}\n"
    notes = parse(text).ignored_settings
    assert [(note.key, note.value) for note in notes] == [
        ("ABEC.Polars:SPL_V.Offset", "50"),
        ("ABEC.Polars:SPL_W.Offset", "25"),
    ]


@pytest.mark.parametrize("simtype", [1, 2])
@pytest.mark.parametrize("origin", ["mouth", "throat", "unknown"])
@pytest.mark.parametrize("offset", ["0", "50"])
def test_offset_notes_use_effective_solve_origin(simtype: int, origin: str, offset: str) -> None:
    text = _text("osse_freestanding_polars_offset").replace(
        "ABEC.SimType = 2", f"ABEC.SimType = {simtype}"
    )
    text = text.replace("Offset = 50", f"Offset = {offset}")
    text += f"WG.Solve = {{\n ObservationOrigin = {origin}\n}}\n"
    parsed = parse(text)
    effective_origin = origin if origin != "unknown" else ("mouth" if simtype == 1 else "throat")
    assert build_request(parsed).request.options.polar_config.observation_origin == effective_origin
    notes = parsed.ignored_settings
    assert len(notes) == int(effective_origin != "throat" or offset != "0")
    if notes:
        assert notes[0].key == "ABEC.Polars:SPL_H.Offset"
        assert notes[0].value == offset
        assert f"about the {effective_origin}" in notes[0].note


def test_notes_use_baffle_default_when_simtype_is_omitted() -> None:
    text = _text("osse_no_simtype") + "ABEC.Polars:SPL_H = {\n Offset = 0\n}\n"
    notes = parse(text).ignored_settings
    assert len(notes) == 3
    assert "mouth" in notes[-1].note


@pytest.mark.parametrize("explicit_wall", [True, False])
def test_import_scale_keeps_wall_and_resolutions_in_millimetres(explicit_wall: bool) -> None:
    text = _text("osse_scale")
    if not explicit_wall:
        text = text.replace("Mesh.WallThickness = 5\n", "")
    text += "Mesh.ThroatResolution = 3\nMesh.MouthResolution = 7\nMesh.RearResolution = 9\n"
    design = parse(text).design
    imported = design_to_mesher_config(design)
    assert float(imported["mesh"]["wallThickness"]) == 5
    for key, value in [("throatResolution", 3), ("mouthResolution", 7), ("rearResolution", 9)]:
        assert float(imported["mesh"][key]) == value
    assert float(imported["profile"]["L"]) == 120
    assert imported["scale"] == 1.5

    native = DesignConfig(design.root.model_copy(update={"text_import_version": None}))
    translated = design_to_mesher_config(native)
    assert translated["mesh"]["wallThickness"] == 7.5
    for key, value in [("throatResolution", 3), ("mouthResolution", 7), ("rearResolution", 9)]:
        assert translated["mesh"][key] == value * 1.5
    assert translated["profile"]["L"] == 180


@pytest.mark.parametrize('stamp,wall,origin', [
    ('native-v1', 7.5, 'mouth'), ('ath-2026-08c-v1', 7.5, 'mouth'),
    ('ath-2026-09a-v2', 5, 'throat'), (None, 5, 'throat'),
])
def test_v2_defaults_compose_without_changing_native_or_saved_v1(stamp, wall, origin):
    text = _text('osse_scale')
    text += 'Mesh.ThroatResolution=3\nMesh.MouthResolution=7\nMesh.RearResolution=9\n'
    text += 'ABEC.Polars:SPL_H = {\nMapAngleRange=0,90,19\n}\n'
    if stamp:
        text = _stamp(text, stamp)
    parsed = parse(text)
    assert parsed.design.root.text_import_version == (None if stamp == 'native-v1' else stamp or TEXT_IMPORT_VERSION)
    for design in (parsed.design, parse(serialize(parsed.design)).design,
                   DesignConfig.model_validate_json(parsed.design.model_dump_json())):
        config = design_to_mesher_config(design)
        assert float(config['profile']['L']) * config.get('scale', 1) == 180
        if stamp in (None, 'ath-2026-09a-v2'):
            assert float(config['profile']['L']) == 120
            assert config['scale'] == 1.5
        else:
            assert 'scale' not in config
        assert float(config['mesh']['wallThickness']) == wall
        for key, value in [('throatResolution', 3), ('mouthResolution', 7), ('rearResolution', 9)]:
            assert float(config['mesh'][key]) == value * wall / 5
        options = solve_options_from_blocks(design.root.extra_blocks, design=design)
        assert options.polar_config.observation_origin == origin
    assert len(parsed.ignored_settings) == (2 if stamp is None else 0)


def test_saved_v1_omitted_simtype_and_polar_origin_are_not_upgraded():
    text = _text('osse_no_simtype') + 'ABEC.Polars:SPL_H = {\nMapAngleRange=0,90,19\n}\n'
    parsed = parse(_stamp(text, 'ath-2026-08c-v1'))
    assert parsed.design.root.simulation.sim_type is None
    assert design_to_mesher_config(parsed.design)['mode'] == 'freestanding'
    assert build_request(parsed).request.options.polar_config.observation_origin == 'mouth'
    assert parsed.ignored_settings == []
