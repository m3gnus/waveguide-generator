"""Consumer coverage for the pinned experimental whole-meridian profile."""
from __future__ import annotations

import asyncio
import json
import re

import numpy as np
import pytest
from fastapi import HTTPException
from hornlab_mesher.config_builder import build_geometry_params
from hornlab_mesher.preview.api import build_preview_geometry
from hornlab_mesher.profile_formulas import lookup_profile_array
from pydantic import ValidationError

from server.cadlink.store import CadLinkStore
from server.exports.api import WgLinkExportRequest, _export_wglink_sync
from server.design.schema import DesignConfig
from server.design.textcfg import TextConfigError, parse, serialize
from server.design_io.api import open_design, save_design, serialize_design
from server.preview.core import preview_options
from server.preview.translate import design_to_mesher_config, outer_body_mode
from server.solver.symmetry import resolve_symmetry, validate_symmetry_mode

TABLE = [[0, 12.7], [20, 25], [80, 60]]


def design(**changes):
    return DesignConfig.model_validate({"formula": "LOOKUP", "lookup_profile": TABLE, **changes})


@pytest.mark.parametrize("table,reason", [
    ([], "at least two"), ([[0, 1]], "at least two"),
    ([[0, 1, 2], [1, 2, 3]], "at least two"),
    ([[0, float('nan')], [1, 2]], "only finite"),
    ([[0, 1], [float('inf'), 2]], "only finite"),
    ([[0, 1], [0, 2]], "strictly increasing"),
    ([[1, 1], [0, 2]], "strictly increasing"),
])
def test_table_validation_matches_pin(table, reason):
    with pytest.raises(ValueError, match=reason) as pinned:
        lookup_profile_array({"lookupProfile": table})
    with pytest.raises(ValidationError, match=re.escape(str(pinned.value))):
        design(lookup_profile=table)


def test_only_z_is_monotonic_and_alias_is_supported():
    table = [[-10, 0], [0, -2], [10, 1]]
    assert design(lookup_profile=table).root.lookup_profile == [tuple(row) for row in table]
    assert DesignConfig.model_validate({"formula": "LOOKUP", "lookupProfile": TABLE}) == design()


@pytest.mark.parametrize("key", ["L", "R", "n", "s", "s1", "s2", "rotation", "tmax", "m", "r", "b", "a", "k", "q", "h", "throat_adapter"])
def test_analytic_keys_refused_even_at_zero(key):
    with pytest.raises(ValidationError, match="formula LOOKUP does not (accept|support)"):
        design(**{key: 0})


@pytest.mark.parametrize("key", ["throat_ext_angle", "throat_ext_length", "slot_length"])
def test_extensions_refused(key):
    with pytest.raises(ValidationError, match="defines the whole meridian"):
        design(**{key: 10})
    assert design(**{key: 0})


def test_guiding_curve_refused_with_a_lookup_message():
    with pytest.raises(ValidationError, match="formula LOOKUP does not support guiding_curve"):
        design(guiding_curve={"curve_type": 1, "width": 100})


@pytest.mark.parametrize("changes", [
    {"ath_nac": 3}, {"ath_nac_present": False}, {"ath_axial_sampling_rule": "ath-v2025-06"},
])
def test_ath_only_fields_refused(changes):
    with pytest.raises(ValidationError, match="LOOKUP does not accept imported ATH geometry"):
        design(**changes)


@pytest.mark.parametrize("table", [
    [[True, 1], [2, 3]], [[0, 1], ["2", 3]], [[0, 1], [2, None]],
])
def test_non_number_cells_refused_before_numpy_coercion(table):
    with pytest.raises(ValidationError, match="cells must be numbers"):
        design(lookup_profile=table)


def test_a0_is_always_emitted_so_server_cli_and_ui_agree():
    assert design_to_mesher_config(design())["profile"]["a0"] == 0
    assert design_to_mesher_config(design(a0=0))["profile"]["a0"] == 0
    assert design_to_mesher_config(design(a0=12))["profile"]["a0"] == 12


def test_cli_reads_a_lookup_json_design_and_refuses_other_extensions(tmp_path):
    import argparse

    from server.cli.solve import _read_request

    original = design()
    path = tmp_path / "lookup.json"
    path.write_text(original.model_dump_json(indent=2) + "\n", encoding="utf-8")
    args = argparse.Namespace(request=None, design=path, overlay=None, engine=None)
    assert _read_request(args).design.formula == "LOOKUP"
    other = tmp_path / "lookup.txt"
    other.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"\.mwg, \.cfg or \.json"):
        _read_request(argparse.Namespace(request=None, design=other, overlay=None, engine=None))


def test_translation_scales_only_dimensions_and_keeps_source_angle():
    config = design_to_mesher_config(design(scale=2, a0=15))
    assert config['profile'] == {"formula": "LOOKUP", "lookupProfile": [[0, 25.4], [40, 50], [160, 120]], "a0": 15}
    params, formula, _mode = build_geometry_params(config)
    assert formula == 'LOOKUP'
    np.testing.assert_allclose(params['lookupProfile'], config['profile']['lookupProfile'])


def test_native_text_json_and_endpoints_round_trip():
    original = design(extra_keys={"Note": "literal ; braces {}"})
    native = serialize(original)
    assert 'WG.NativeDesign' in native
    assert parse(native).design == original
    assert serialize(parse(native)) == native
    assert DesignConfig.model_validate(json.loads(original.model_dump_json())) == original
    json_saved = asyncio.run(serialize_design({"design": original.model_dump(mode='json'), "filename": 'lookup.json'}))
    assert json_saved['suggestedFilename'] == 'lookup.json'
    assert parse(json_saved['text']).design == original
    assert serialize(parse(json_saved['text'])) == json_saved['text']
    assert DesignConfig.model_validate(asyncio.run(open_design(json_saved['text']))['design']) == original
    saved = asyncio.run(save_design({"design": original.model_dump(mode='json'), "filename": 'lookup.mwg'}))
    assert saved['suggestedFilename'] == 'lookup.mwg'
    assert DesignConfig.model_validate(asyncio.run(open_design(saved['text']))['design']) == original
    assert asyncio.run(serialize_design({"design": original.model_dump(mode='json')}))['suggestedFilename'] == 'untitled.mwg'


@pytest.mark.parametrize('endpoint', [save_design, serialize_design])
def test_cfg_export_refused(endpoint):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint({"design": design().model_dump(mode='json'), "filename": 'lookup.cfg'}))
    assert exc.value.status_code == 422
    assert 'ATH has no LOOKUP profile' in exc.value.detail


@pytest.mark.parametrize('options,mode', [
    ({'mesh': {'wall_thickness': 0}}, 'bare'),
    ({'mesh': {'wall_thickness': 2}}, 'freestanding'),
    ({'enclosure': {'depth': 100}}, 'enclosure'),
])
def test_preview_and_composition(options, mode):
    original = design(cross_section={'exponent': 4, 'aspect_ratio': 1.2},
                      morph={'target_shape': 2}, **options)
    assert outer_body_mode(original.root) == mode
    geometry = build_preview_geometry(design_to_mesher_config(original), preview_options('coarse'))
    assert geometry.surfaces
    for surface in geometry.surfaces:
        assert np.isfinite(surface.positions).all()


def test_custom_sampling_and_solve_domain():
    original = design(mesh={'sampling_mode': 'zmap', 'z_map_points': '0.25,0.1;0.75,0.9'})
    config = design_to_mesher_config(original)
    assert config['mesh']['samplingMode'] == 'zmap'
    assert build_preview_geometry(config, preview_options('coarse')).surfaces
    resolution = resolve_symmetry(design())
    assert resolution.xz and resolution.yz
    assert validate_symmetry_mode('quarter', resolution) == 1


def test_cad_cfg_transport_refused_before_writing(tmp_path):
    with pytest.raises(HTTPException, match="ATH has no LOOKUP profile"):
        _export_wglink_sync(WgLinkExportRequest(design=design(), designRevision=1), CadLinkStore(":memory:"), tmp_path, "lookup")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("text", ['{"formula": "OSSE"}\n', '{\n "setting": 1\n}\n', "{ not json"])
def test_only_lookup_json_takes_the_json_design_path(text: str) -> None:
    # Anything else starting with "{" keeps the config parser's own refusal.
    with pytest.raises(TextConfigError) as raised:
        parse(text)
    assert "LOOKUP" not in str(raised.value)
