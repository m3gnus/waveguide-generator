"""Status: real-mesher export evidence shared with the read-only profile inspector."""
import json
from pathlib import Path

import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.exports.core import build_profiles, _inner_grid

FIXTURES = json.loads((Path(__file__).parents[2] / 'shared/profile-inspection/mesher-profiles.json').read_text())


@pytest.mark.parametrize('fixture', FIXTURES, ids=[case['name'] for case in FIXTURES])
def test_inspector_fixture_matches_build_profiles_in_scaled_mm(fixture):
    design = DesignConfig.model_validate(fixture['design'])
    assert build_profiles(design, 'profiles') == fixture['csv']
    grid = _inner_grid(design, profile_sampling=True)
    for plane, index in [('H', 0), ('V', len(grid) // 4)]:
        expected = fixture['expected'][plane]
        np.testing.assert_allclose([point['z'] for point in expected], grid[index, :, 2], atol=1e-8)
        np.testing.assert_allclose([point['r'] for point in expected], np.hypot(grid[index, :, 0], grid[index, :, 1]), atol=1e-8)
    if fixture['name'] == 'morphed-scaled-extended':
        # FREEFORM defines the whole meridian: the existing translator omits
        # generic extensions. Inspect the export as it is, without inventing one.
        assert grid[0, -1, 2] == pytest.approx(120 * 1.7)
        assert np.hypot(*grid[0, 0, :2]) == pytest.approx(12.7 * 1.7)


def test_freeform_load_save_keeps_station_positions_and_file_bytes():
    from server.design.textcfg import parse, serialize

    payload = {**FIXTURES[0]['design'], 'cross_sections': [
        {'t': 0, 'shape': 'ellipse'},
        {'t': .35123456789123456, 'shape': 'ellipse'},
        {'t': .7123456789123456, 'shape': 'ellipse'},
        {'t': 1, 'shape': 'ellipse'},
    ]}
    saved = serialize(DesignConfig.model_validate(payload))
    opened = parse(saved)
    assert serialize(opened).encode() == saved.encode()
    # The same typed-wire save path used by the app, not only the pristine-text path.
    assert serialize(DesignConfig.model_validate(opened.semantic_data())).encode() == saved.encode()
    saved_again = parse(serialize(DesignConfig.model_validate(opened.semantic_data())))
    assert [station.t.constant_value() for station in saved_again.design.root.cross_sections] == [station.t.constant_value() for station in opened.design.root.cross_sections]
