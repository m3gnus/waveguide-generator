"""Saved-design and ATH compatibility at the optional solve-accuracy boundary."""
import pytest

from server.design.schema import DesignConfig
from server.design.textcfg import parse, serialize
from server.preview.translate import design_to_mesher_config


def test_absent_or_off_is_not_written_into_old_documents():
    for mesh in ({}, {'surface_deviation_mm': None}):
        design = DesignConfig.model_validate({'formula': 'OSSE', 'mesh': mesh})
        assert 'surface_deviation_mm' not in design.model_dump(mode='json')['mesh']
        assert 'surface_deviation_mm' not in design_to_mesher_config(design)['mesh']
        assert 'surface_deviation_mm' not in serialize(design)


def test_deviation_uses_finished_mm_and_the_existing_wg_only_cfg_block():
    design = DesignConfig.model_validate({'formula': 'OSSE', 'scale': 1.5,
                                         'mesh': {'surface_deviation_mm': 0.3}})
    assert design_to_mesher_config(design)['mesh']['surface_deviation_mm'] == 0.3
    text = serialize(design)
    assert 'WG.Geometry' in text
    assert 'Mesh.SurfaceDeviation' not in text
    assert parse(text).design.root.mesh.surface_deviation_mm == 0.3


@pytest.mark.parametrize('value', [True, -1, 0, '0.3', float('inf')])
def test_invalid_deviation_is_rejected_before_translation(value):
    with pytest.raises(ValueError):
        DesignConfig.model_validate({'formula': 'OSSE', 'mesh': {'surface_deviation_mm': value}})


def test_fresh_ath_import_does_not_opt_into_the_new_rule():
    design = parse('Length = 130\nThroat.Diameter = 25.4\nCoverage.Angle = 45\nOS.k = 7\n').design
    assert design.root.mesh.surface_deviation_mm is None
    assert 'surface_deviation_mm' not in design_to_mesher_config(design)['mesh']


def test_estimate_route_passes_the_optional_setting_to_the_existing_builder(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.engines.registry import EngineRegistry
    from server.jobs.mesh_preview import mount_solver_mesh
    from server.mesh import builder

    async def estimate(design, cancel_cb=None):
        assert design.root.mesh.surface_deviation_mm == 0.3
        return {'triangle_count': 1234, 'surface_deviation_mm': 0.3}

    monkeypatch.setattr(builder, 'estimate_solver_mesh', estimate)
    app = FastAPI()
    mount_solver_mesh(app, EngineRegistry(detector=lambda: []))
    with TestClient(app) as client:
        response = client.post('/api/mesh-estimate', json={
            'design': {'formula': 'OSSE', 'mesh': {'surface_deviation_mm': 0.3}},
        })
    assert response.status_code == 200
    assert response.json() == {'triangle_count': 1234, 'surface_deviation_mm': 0.3}


def test_complex_guide_opt_in_estimate_fits_the_existing_memory_stop(tmp_path):
    import json
    from pathlib import Path

    from hornlab_mesher.config_builder import estimate_from_config

    from server.mesh import builder
    from server.platform.memory import PhysicalMemory

    config = json.loads((Path(__file__).parent / 'fixtures/solve-accuracy-complex-guide.json').read_text())
    limit = builder.DenseSolverMemoryLimit(
        bytes=32 * 1024**3, source='physical-memory',
        physical=PhysicalMemory(bytes=64 * 1024**3, source='sysconf', known=True),
    )

    def estimate_only(current, *args, **kwargs):
        return estimate_from_config(current)

    metadata = builder._build_with_memory_stop(
        config, tmp_path / 'estimate.msh', limit=limit, build_from_config=estimate_only,
    )
    assert metadata['meshTriangleEstimate'] < builder._dense_solver_triangle_stop(1, limit=limit)
    # The known generated mesh is about 12,600 facets, rather than 61,862.
    assert metadata['meshTriangleEstimate'] < 2 * 12616
