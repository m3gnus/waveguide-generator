"""Retired table files import through S4 into the one Custom profile mode."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from server.design.migrate import apply_migrations
from server.design.schema import DesignConfig
from server.design.textcfg import parse, serialize
from server.design_io.api import open_design, serialize_design
from server.jobs.design_availability import resolve_job_design
from server.preview.freeform_conversion import convert_design, legacy_lookup_source, _legacy_grid
from server.preview.translate import design_to_mesher_config
from server.exports.core import _inner_grid

DATA = Path(__file__).parent / 'data' / 'lookup_oldconfig_corpus'
CASES = ('implicit-dimensions', 'morph-snapping', 'scale-source-quarter', 'large-table', 'dense-curve', 'close-corners')


def frozen(name):
    return json.loads((DATA / f'{name}.json').read_text())


def authored(design, z):
    from hornlab_mesher import freeform_evaluate_profile
    config = design_to_mesher_config(design)
    # Authored H/V are measured before the separately preserved morph.
    config['morph'] = {}
    return [freeform_evaluate_profile(config, plane, z) for plane in ('H', 'V')]


@pytest.mark.parametrize('name', CASES)
def test_table_file_opens_as_custom_profile_and_roundtrips(name):
    case = frozen(name)
    text = json.dumps(case['design'])
    parsed = parse(text)
    design = parsed.design
    assert design.formula == 'FREEFORM'
    assert design.root.shared_profile is True
    assert len(design.root.profile_h.points) <= 64
    rows = np.asarray(case['design']['lookup_profile'])
    scale = case['design'].get('scale', 1)
    z = np.unique(np.r_[rows[:, 0], np.linspace(rows[0, 0], rows[-1, 0], 10001)])*scale
    expected = np.interp(z, rows[:, 0]*scale, rows[:, 1]*scale)
    for values in authored(design, z):
        np.testing.assert_allclose(values, expected, atol=.01, rtol=0)
    note = parsed.migrations[-1].note
    assert 'Opened as Custom profile.' in note
    assert 'Maximum generated-grid change' in note
    assert 'Source shape preserved' in note
    assert 'interpolate' in note
    assert 'LOOKUP' not in note
    saved = serialize(parsed)
    assert saved != text
    assert 'Freeform.Shared = 1' in saved
    assert 'WG.NativeDesign' not in saved
    reopened = parse(saved)
    assert reopened.migrations == []
    for before, after in zip(authored(design, z), authored(reopened.design, z)):
        np.testing.assert_allclose(before, after, atol=1e-9, rtol=0)
    if name == 'large-table':
        assert len(design.root.profile_h.points) == 2
        assert 'Profile curve unchanged.' in note
    if name in ('implicit-dimensions', 'morph-snapping'):
        assert 'Morph-start snapping' in note
    if name == 'implicit-dimensions':
        assert 'Implicit mouth rounding' in note


@pytest.mark.parametrize('name', CASES[:4])
def test_generated_morph_and_source_cap_preserved(name):
    from hornlab_mesher.config_builder import build_geometry_params
    from hornlab_mesher.preview.source_cap import _source_geometry
    from hornlab_mesher.builders.point_grid_sources import _source_cap_height, _source_cap_radius
    case = frozen(name)
    source = legacy_lookup_source(case['design'])
    result = convert_design(source, name, importing=True)
    before, _ = _legacy_grid(source)
    after = _inner_grid(result.design, profile_sampling=True)
    # Compare at all old ring locations; never pin today's OCC corner-wall
    # approximation error, which belongs to the independent mesher branch.
    for old_ring, new_ring in zip(before, after):
        radius = np.linalg.norm(old_ring[:, :2], axis=1)
        new_radius = np.linalg.norm(new_ring[:, :2], axis=1)
        np.testing.assert_allclose(np.interp(old_ring[:, 2], new_ring[:, 2], new_radius), radius, atol=1e-8, rtol=0)
    old_params = build_geometry_params(case['config']['translated'])[0]
    new_params = build_geometry_params(design_to_mesher_config(result.design))[0]
    throat = float(np.linalg.norm(before[0, 0, :2]))
    caps = [_source_geometry(params, formula, before) for params, formula in ((old_params, 'LOOKUP'), (new_params, 'FREEFORM'))]
    assert _source_cap_height(throat, caps[0]) == pytest.approx(_source_cap_height(throat, caps[1]), abs=1e-10)
    old_radius, new_radius = [_source_cap_radius(throat, cap) for cap in caps]
    if np.isfinite(old_radius):
        assert old_radius == pytest.approx(new_radius, abs=1e-9)
    else:
        # A flat cap has no meaningful spherical radius; the builder ignores it.
        assert _source_cap_height(throat, caps[1]) == 0
    assert caps[0].source_curv == caps[1].source_curv
    assert result.report.source_cap_angle_before_deg == pytest.approx(result.report.source_cap_angle_after_deg, abs=1e-9)


def test_native_payload_api_and_run_open_use_same_import_without_changing_original():
    payload = frozen('morph-snapping')['design']
    original = deepcopy(payload)
    expected, applications = apply_migrations(payload)
    assert payload == original
    encoded = json.dumps(payload, separators=(',', ':'))
    native = '; Parameter config\n; Waveguide Generator geometry-interpretation: native-v1\nWG.NativeDesign = {\nPayload = '+encoded+'\n}\n'
    assert parse(native).design.model_dump(mode='json') == expected
    opened = asyncio.run(open_design(json.dumps(payload)))
    assert opened['design'] == expected
    assert opened['migrationsApplied'][-1]['note'] == applications[-1].note
    saved = asyncio.run(serialize_design({'design': payload, 'filename': 'old.mwg'}))
    assert saved['suggestedFilename'] == 'old.cfg'
    assert parse(saved['text']).design.formula == 'FREEFORM'
    snapshot = {'version': 1, 'design': payload}
    old_snapshot = deepcopy(snapshot)
    resolution = resolve_job_design(snapshot)
    assert snapshot == old_snapshot
    assert resolution.snapshot['design'] == expected
    assert resolution.note == applications[-1].note


def test_import_keeps_best_fit_when_explicit_conversion_would_refuse():
    payload = {'formula': 'LOOKUP', 'lookup_profile': [[float(i), 20+i*.2+2*(i%2)] for i in range(90)]}
    migrated, applications = apply_migrations(payload)
    assert migrated['formula'] == 'FREEFORM'
    assert len(migrated['profile_h']['points']) <= 64
    assert 'best fit' in applications[-1].note
    assert 'exceeds the 0.01 mm' in applications[-1].note


@pytest.mark.parametrize('origin', (200, -200))
def test_disjoint_axial_origin_still_opens_and_reports_actual_placement(origin):
    payload = {'formula': 'LOOKUP', 'lookup_profile': [[origin, 12.7], [origin+100, 80]]}
    result = convert_design(legacy_lookup_source(payload), '', importing=True)
    assert result.design.formula == 'FREEFORM'
    assert result.design.root.length.constant_value() == 100
    assert result.report.axial_extent_deviation_mm == pytest.approx(abs(origin))
    assert result.report.mouth.max_deviation_mm == pytest.approx(abs(origin))
    assert any(feature.name == 'Axial origin' for feature in result.report.features)
    assert DesignConfig.model_validate(payload).formula == 'FREEFORM'


def test_a_failed_run_design_import_does_not_hide_historical_results(monkeypatch):
    import server.design.migrate as migration
    from hornlab_mesher.errors import UserRefusal
    original = {'version': 1, 'design': frozen('implicit-dimensions')['design']}
    before = deepcopy(original)
    def refusal(_payload):
        raise UserRefusal('Import', 'Unrepresentable geometry.', 'Edit the original data.')
    monkeypatch.setattr(migration, 'apply_migrations', refusal)
    resolved = resolve_job_design(original)
    assert not resolved.reopenable
    assert resolved.snapshot == before
    assert original == before


def test_exactly_one_mm_interior_corner_gap_survives_t_roundoff():
    payload = {'formula': 'LOOKUP', 'lookup_profile': [[0, 12.7], [1.1, 13.5], [2.1, 13.8], [120, 140]]}
    design = DesignConfig.model_validate(payload)
    assert len(design.root.profile_h.points) == 4
    assert all(point.join == 'corner' for point in design.root.profile_h.points)
    z = np.linspace(0, 120, 10001)
    for values in authored(design, z):
        np.testing.assert_allclose(values, np.interp(z, *np.asarray(payload['lookup_profile']).T), atol=1e-9, rtol=0)


def test_section_exponent_uses_s4_station_fit_with_measured_outline_change():
    payload = {'formula': 'LOOKUP', 'lookup_profile': [[0, 12.7], [30, 28], [90, 65], [160, 110]],
               'cross_section': {'exponent': 4, 'aspect_ratio': 1}}
    result = convert_design(legacy_lookup_source(payload), '', importing=True)
    assert result.design.root.shared_profile is False
    assert 2 < len(result.design.root.cross_sections) <= 32
    assert any(feature.name == 'Cross-section exponent' for feature in result.report.features)
    assert result.report.wall.max_deviation_mm >= 0
    z = np.linspace(0, 160, 10001)
    for values in authored(result.design, z):
        np.testing.assert_allclose(values, np.interp(z, *np.asarray(payload['lookup_profile']).T), atol=1e-9, rtol=0)


@pytest.mark.parametrize('aspect', [1.6, .7])
def test_aspect_section_corpus_derives_separate_cardinal_profiles_from_same_rows(aspect):
    payload = frozen('aspect-section')['design']
    payload['cross_section']['aspect_ratio'] = aspect
    parsed = parse(json.dumps(payload))
    design = parsed.design
    assert design.root.shared_profile is False
    rows = np.asarray(payload['lookup_profile'])*payload['scale']
    z = np.linspace(0, rows[-1, 0], 10001)
    base = np.interp(z, *rows.T)
    h, v = authored(design, z)
    np.testing.assert_allclose(h, rows[0, 1]+(base-rows[0, 1])*aspect, atol=1e-9, rtol=0)
    np.testing.assert_allclose(v, base, atol=1e-9, rtol=0)
    assert 'aspect' not in parsed.migrations[-1].note.lower()
    result = convert_design(legacy_lookup_source(payload), 'aspect', importing=True)
    assert not any(feature.name == 'Cross-section aspect' for feature in result.report.features)
    assert result.report.H.max_deviation_mm < .01
    assert result.report.V.max_deviation_mm < .01
    reopened = parse(serialize(parsed)).design
    assert reopened.root.shared_profile is False
    for left, right in zip(authored(reopened, z), (h, v)):
        np.testing.assert_allclose(left, right, atol=1e-9, rtol=0)


def test_unmarked_wg_expressions_keep_their_dialect_during_geometry_conversion():
    payload = {'formula': 'LOOKUP', 'lookup_profile': [[0, 12.7], [120, 72.7]], 'scale': 'log(100)',
               'source': {'velocity': {'value': 1, 'raw': '2/2'}}}
    result = convert_design(legacy_lookup_source(payload), '', importing=True)
    assert result.design.root.scale.constant_value() == 2
    assert result.design.root.scale.raw == 'log(100)'
    assert result.design.root.scale.dialect == 'wg-v1'
    assert result.design.root.source.velocity.raw == '2/2'
    assert result.report.wall.max_deviation_mm < .01


def test_shape_only_morph_keeps_exact_implicit_mouth_dimensions():
    payload = {'formula': 'LOOKUP', 'lookup_profile': [[0, 12.7], [30, 28], [90, 65], [160, 110.3]],
               'morph': {'target_shape': 3, 'target_exponent': 4}}
    result = convert_design(legacy_lookup_source(payload), '', importing=True)
    assert result.design.root.morph.target_width.constant_value() == pytest.approx(220.6)
    assert result.design.root.morph.target_height.constant_value() == pytest.approx(220.6)
    assert any(feature.name == 'Implicit mouth dimensions' for feature in result.report.features)
