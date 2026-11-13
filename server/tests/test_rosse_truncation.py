"""A newly supported ATH throat cut must survive every WG transport."""
import asyncio
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from hornlab_mesher.config_builder import build_geometry_params
from hornlab_mesher.profile_sampling import build_point_grid_arrays
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig
from server.design.textcfg import TextConfigError, parse, serialize
from server.design_io.api import open_design, serialize_design
from server.mesh.builder import _solver_mesher_config
from server.preview.translate import design_to_mesher_config

FIXTURES = Path(__file__).parent / 'fixtures/rosse_trunc'


@pytest.mark.parametrize('name', ['trunc-10-linear', 'trunc-41-stretch-linear', 'trunc-two-roots'])
def test_import_save_reopen_and_solve_preparation_keep_the_cut(name, tmp_path):
    text = (FIXTURES / f'{name}.cfg').read_text()
    design = parse(text).design
    assert design.root.ath_rosse_trunc_mm > 0
    assert 'trunc' not in design.root.extra_keys
    expected = np.array([[[float(v) for v in row.split(';')] for row in block.splitlines()]
                         for block in (FIXTURES / f'{name}.csv').read_text().strip().split('\n\n')])
    store = CadLinkStore(tmp_path / 'cadlink.db')
    opened = asyncio.run(open_design(text, store=store))
    saved = asyncio.run(serialize_design(opened['design']))
    reopened = asyncio.run(open_design(saved['text'], store=store))
    variants = [design, DesignConfig.model_validate_json(design.model_dump_json()),
                parse(serialize(design)).design, DesignConfig.model_validate(reopened['design'])]
    first = None
    for variant in variants:
        for cfg in (design_to_mesher_config(variant), _solver_mesher_config(variant)):
            assert cfg['athRosseTruncMm'] == design.root.ath_rosse_trunc_mm
            actual = build_point_grid_arrays(build_geometry_params(cfg)[0])['inner_grid']
            assert actual.shape == expected.shape
            np.testing.assert_allclose(actual, expected, rtol=0, atol=.001)
            if first is None:
                first = actual.tobytes()
            assert actual.tobytes() == first


@pytest.mark.parametrize('stamp', [None, 'ath-2026-08c-v1', 'ath-2026-09a-v2'])
def test_old_designs_cannot_acquire_the_new_interpretation(stamp):
    with pytest.raises(ValidationError, match='Re-import a copy'):
        DesignConfig.model_validate({'formula': 'R-OSSE', 'text_import_version': stamp,
                                     'ath_rosse_trunc_mm': 10})


@pytest.mark.parametrize('replacement', ['tmax = 0.8', 'trunc = -1', 'trunc = 9999', 'trunc = 10+p'])
def test_unqualified_cuts_explain_the_remedy(replacement):
    text = (FIXTURES / 'trunc-10-linear.cfg').read_text()
    old = 'tmax = 1' if replacement.startswith('tmax') else 'trunc = 10'
    with pytest.raises(TextConfigError, match='[Rr]emove.*[Rr]e-import'):
        parse(text.replace(old, replacement))


def test_absent_cut_keeps_saved_json_and_legacy_passthrough_unchanged():
    text = (FIXTURES / 'trunc-10-linear.cfg').read_text().replace(' trunc = 10\n', '')
    design = parse(text).design
    assert 'ath_rosse_trunc_mm' not in design.model_dump(mode='json')
    assert design == parse(serialize(design)).design


def test_saved_v3_ignored_block_cut_keeps_its_existing_geometry():
    text = (FIXTURES / 'trunc-41-stretch-linear.cfg').read_text()
    saved = '; Waveguide Generator geometry-interpretation: ath-2026-09a-v3\n' + text
    design = parse(saved).design
    assert design.root.ath_rosse_trunc_mm is None
    baseline = parse(saved.replace(' trunc = 41\n', '')).design
    a = build_point_grid_arrays(build_geometry_params(design_to_mesher_config(design))[0])['inner_grid']
    b = build_point_grid_arrays(build_geometry_params(design_to_mesher_config(baseline))[0])['inner_grid']
    assert a.tobytes() == b.tobytes()
