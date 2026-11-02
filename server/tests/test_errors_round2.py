"""Public remedies survive validation, legacy wrappers and worker transport."""
from types import SimpleNamespace

import pytest

from server.preview.errors import user_error_message


def test_file_import_uses_its_format_remedy():
    from server.mesh.file_import import parse_file_mesh
    with pytest.raises(ValueError) as error:
        parse_file_mesh('$MeshFormat\n4.1 0 8\n$EndMeshFormat\n')
    words = user_error_message(error.value)
    assert 'Gmsh 2.2' in words and 'Export' in words


def test_baffle_refusal_is_public_through_legacy_translation():
    from server.preview.errors import mesher_error_message
    from server.jobs.runtime.core import _failure_message
    detail = 'infinite-baffle coupled aperture mesh requires a planar mouth ring; mouth z spans 20 mm'
    message = mesher_error_message({'mode': 'infinite-baffle'}, detail)
    words = _failure_message(RuntimeError(message))
    assert 'mouth' in words and ('free-standing' in words or 'Free-standing' in words)
    assert 'recorded' not in words


@pytest.mark.parametrize('kind', ['ground', 'driver', 'axis', 'imported', 'assembly', 'bempp', 'metal', 'engine'])
def test_established_public_domain_refusals_keep_their_remedy(kind):
    from server.solver.ground_plane import GroundPlaneClearance
    from server.solver.driver_lem import DriverMassLimitError
    from server.solver.imported import SourceAxisError
    from server.solver.beat_imported import ImportedBeatRefusal
    from server.mesh.builder import AssemblyRefusal
    from server.solver.bempp import BemppUnavailable
    from server.solver.metal import MetalUnavailable
    from server.jobs.runtime.engine_policy import EngineUnavailableError
    from server.jobs.runtime.core import _failure_message
    types = dict(ground=GroundPlaneClearance, driver=DriverMassLimitError,
                 axis=SourceAxisError, imported=ImportedBeatRefusal, assembly=AssemblyRefusal,
                 bempp=BemppUnavailable, metal=MetalUnavailable, engine=EngineUnavailableError)
    reason = types[kind]('The selection cannot be used. Choose the supported setting.')
    assert 'Choose the supported setting.' in _failure_message(reason)
    assert 'Choose the supported setting.' in user_error_message(reason)


def test_native_validation_retains_the_refusal_fields():
    from pydantic import ValidationError
    from hornlab_mesher.errors import UserRefusal
    from server.design.schema import DesignConfig
    with pytest.raises(ValidationError) as error:
        DesignConfig.model_validate({'formula': 'SOURCE-DISK', 'source_body':
                                     {'radius_mm': 18, 'outer_radius_mm': 18, 'depth_mm': 8}})
    cause = error.value.errors()[0]['ctx']['error']
    assert isinstance(cause, UserRefusal)
    assert '0.1' in user_error_message(error.value)


def test_invalid_driver_size_is_user_action():
    from server.mesh.imported import validate_imported_sizes
    from server.cadlink.joblane.failure_codes import preparation_failure_code
    with pytest.raises(ValueError) as error:
        validate_imported_sizes([{'id': 'HF'}], {'rigid_size_mm': 10,
                                                      'transition_mm': 5, 'source_size_mm': {'HF': 0}})
    assert preparation_failure_code(error.value) is None
    assert 'positive' in user_error_message(error.value)


def test_project_refusal_passes_the_shared_user_text_guard():
    from server.cadlink.intake.ingest import _admit_return, IngestRefusal
    from server.cadlink.wglink_protocol import user_text_violations
    with pytest.raises(IngestRefusal) as error:
        _admit_return(SimpleNamespace(get_design=lambda _: None),
                      {'instances': [{'design_id': 'design-a'}]}, None, None)
    assert not user_text_violations(str(error.value))
    assert 'CAD-linked designs' in str(error.value)


def test_native_stretch_does_not_prescribe_an_ath_edit():
    from hornlab_mesher.throat_stretch import validate_stretch_composition
    with pytest.raises(ValueError) as error:
        validate_stretch_composition({'s1': 1, 's2': 1, 'slotLength': 10}, 'OSSE')
    words = user_error_message(error.value)
    assert 'ATH text' not in words
    assert 'Straight slot length' in words


def test_stretch_preserves_the_program_fault_diagnostic_contract():
    from hornlab_mesher.throat_stretch import stretch_config_errors
    from hornlab_mesher.mesher import MesherError
    @stretch_config_errors
    def fail(_config):
        raise MesherError('mesh build failed: gmsh produced no triangle elements')
    from hornlab_mesher.errors import ConfigError, UserRefusal
    with pytest.raises(ConfigError) as caught:
        fail({'formula': 'OSSE', 's1': .5, 's2': .2})
    assert not isinstance(caught.value, UserRefusal)
    assert isinstance(caught.value.__cause__, MesherError)
    assert 'recorded' in user_error_message(caught.value)


def test_numeric_control_uses_the_app_label():
    from hornlab_mesher.errors import numeric_refusal
    assert numeric_refusal('throat_res_mm', 'bad resolution').field == 'Throat mesh resolution'


def test_native_refusal_keeps_the_original_diagnostic():
    from server.design.native import _number
    with pytest.raises(ValueError) as error:
        _number('bad', 'mesh.wall_thickness', 0)
    assert str(error.value) == 'mesh.wall_thickness requires a plain finite number for native formulas'


def test_bempp_process_memory_status_keeps_its_category(monkeypatch):
    from server.solver import bempp_process
    from server.jobs.runtime.core import _solver_failure_code
    monkeypatch.setattr(bempp_process.child_process, 'worker_exitcode', lambda *a, **k: 0xC0000017)
    assert _solver_failure_code(bempp_process._worker_death_error(None, None)) == 'solver_memory_exhausted'


def test_known_mesh_crash_remedy_survives():
    from server.mesh.child import CRASH_MESSAGE, MesherCrashError
    assert user_error_message(MesherCrashError(CRASH_MESSAGE)) == CRASH_MESSAGE


def test_assembly_program_fault_keeps_500(monkeypatch, tmp_path):
    import asyncio
    import httpx2 as httpx
    from test_models_api import app_for
    from server.cadlink.store import CadLinkStore
    from server.jobs import models_api
    async def broken(*a, **k):
        raise RuntimeError('broken assembly invariant')
    monkeypatch.setattr(models_api, 'assembly_front_context', broken)
    async def post():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                return await client.post('/api/models/assembly/context', json={'design': {'formula': 'OSSE', 'L': 120, 'a': 45}})
        finally:
            await runtime.shutdown()
            store.close()
    response = asyncio.run(post())
    assert response.status_code == 500


def test_surround_remedy_covers_its_actual_bound():
    from hornlab_mesher.source_contour import dome
    with pytest.raises(ValueError) as error:
        dome(radius_mm=12, height_mm=4, surround_width_mm=2, surround_depth_mm=2)
    words = user_error_message(error.value)
    assert ('half' in words or '/2' in words) and 'zero' in words
    assert dome(radius_mm=12, height_mm=4, surround_width_mm=2, surround_depth_mm=1)


def test_driver_size_diagnostic_retains_the_missing_and_extra_ids():
    from server.mesh.imported import validate_imported_sizes
    with pytest.raises(ValueError) as error:
        validate_imported_sizes([{'id': 'HF'}], {'rigid_size_mm': 10, 'transition_mm': 5,
                                                      'source_size_mm': {'LF': 1}})
    diagnostic = getattr(error.value, 'diagnostic', str(error.value))
    assert 'HF' in diagnostic and 'LF' in diagnostic


@pytest.mark.parametrize('kind', ['memory', 'public', 'deterministic'])
def test_worker_exception_keeps_its_presentation_category(kind):
    from server.solver.bempp_process import _exception_payload, _worker_exception
    from server.solver.ground_plane import GroundPlaneClearance
    from server.jobs.runtime.core import _failure_message
    error = {'memory': MemoryError('allocation failed'), 'public': GroundPlaneClearance('The horn crosses the reflecting surface. Increase Height above surface.'), 'deterministic': ValueError('bad native invariant')}[kind]
    words = _failure_message(_worker_exception(_exception_payload(error)))
    if kind == 'memory':
        assert 'ran out of memory' in words
    elif kind == 'public':
        assert 'Increase Height above surface.' in words
    else:
        assert 'recorded' in words and 'try again' not in words.lower()


def test_control_catalog_comes_from_the_mesher_package():
    from importlib.resources import files
    import json
    from hornlab_mesher.controls import CONTROL_CATALOG, ALIASES, control_label
    assert CONTROL_CATALOG == json.loads(files('hornlab_mesher').joinpath('control_labels.json').read_text(encoding="utf-8"))
    for key in ALIASES:
        assert control_label(key)
    with pytest.raises(KeyError):
        control_label('Profile value')


def test_assembly_owner_keeps_the_placement_prefix_and_refusal(monkeypatch):
    from server.design.schema import DesignConfig
    from server.mesh import builder
    row = {'radiator_id': 'woofer', 'enabled': True, 'radius_mm': 50,
           'placement': {'anchor': 'free', 'x_mm': 10, 'y_mm': -100,
                         'spacing_to': {'radiator_id': 'missing', 'distance_mm': 100}}}
    monkeypatch.setattr(builder, 'assembly_woofer_rows', lambda _: [row])
    design = DesignConfig.model_validate({'formula': 'OSSE', 'L': 120, 'a': 45, 'assembly': {'signal_groups': [{'id': 'a1b2c3d4-0000-4000-8000-000000000001', 'label': 'HF', 'members': [{'radiator_id': 'horn-throat'}]}]}})
    with pytest.raises(builder.AssemblyRefusal) as error:
        builder.assembly_mesher_config(design)
    assert str(error.value).startswith('Woofer placement:')
    assert 'Choose a current woofer in Spacing from' in str(error.value)


def test_assembly_known_mesher_crash_keeps_422_and_its_fix(tmp_path, monkeypatch):
    import asyncio
    import httpx2 as httpx
    from test_models_api import app_for
    from server.cadlink.store import CadLinkStore
    from server.jobs import models_api
    from server.mesh.child import MesherCrashError, CRASH_MESSAGE
    async def refuse(*args, **kwargs):
        raise MesherCrashError(CRASH_MESSAGE)
    monkeypatch.setattr(models_api, 'assembly_front_context', refuse)
    async def run():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/api/models/assembly/context', json={'design': {'formula': 'OSSE', 'L': 120, 'a': 45}})
            assert response.status_code == 422
            assert response.json()['detail'] == CRASH_MESSAGE
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(run())


def test_serialized_memory_error_keeps_its_category_without_optional_metadata():
    from server.solver.bempp_process import _worker_exception
    from server.jobs.runtime.core import _solver_failure_code
    error = _worker_exception({'type': 'MemoryError', 'message': 'allocation failed', 'traceback': 'worker trace'})
    assert _solver_failure_code(error) == 'solver_memory_exhausted'


@pytest.mark.parametrize('family', ['guide', 'freeform'])
def test_schema_outline_refusals_retain_the_user_fix(family):
    from server.design.schema import DesignConfig
    if family == 'guide':
        payload = {'formula': 'OSSE', 'L': 120, 'a': 45, 'cross_section': {'aspect_ratio': 2}, 'guiding_curve': {'curve_type': 1, 'width': 100}}
        expected = 'Guiding curve mode'
    else:
        payload = {'formula': 'FREEFORM', 'length': 60, 'profile_h': {'points': [[0, 12.7], [1, 40]]}, 'profile_v': {'points': [[0, 12.7], [1, 40]]}, 'cross_sections': [{'t': 0, 'shape': 'ellipse'}, {'t': 1, 'shape': 'ellipse'}], 'cross_section': {'exponent': 4}}
        expected = 'Cross-section stations'
    with pytest.raises(ValueError) as error:
        DesignConfig.model_validate(payload)
    words = user_error_message(error.value)
    assert expected in words and 'recorded' not in words
    assert 'Set ' in words or 'Import ' in words


def test_real_sharp_corner_keeps_its_fix_and_has_a_working_bare_shell_alternative():
    from copy import deepcopy
    from hornlab_mesher.config_builder import resolve_geometry
    profile = {'points': [{'z': 0, 'r': 100, 'join': 'corner'}, {'z': 1, 'r': 110, 'join': 'corner'}, {'z': 2, 'r': 100, 'join': 'corner'}]}
    config = {'formula': 'FREEFORM', 'mode': 'freestanding', 'profile': {'profileH': profile, 'profileV': deepcopy(profile)}, 'mesh': {'wall_thickness_mm': 2, 'throat_res_mm': 5, 'mouth_res_mm': 10}}
    with pytest.raises(ValueError) as error:
        resolve_geometry(config)
    assert 'unsafe offset mitre' in str(error.value)
    words = user_error_message(error.value)
    assert 'choose Bare shell in Mounting' in words and 'recorded' not in words
    # The other public route wraps this legacy diagnostic before displaying it.
    from server.preview.errors import mesher_error_message
    assert user_error_message(RuntimeError(mesher_error_message(config, str(error.value)))) == words
    config['mode'] = 'bare'
    assert resolve_geometry(config).geometry is not None


@pytest.mark.parametrize('case', ['arc', 'contours', 'sampling', 'length'])
def test_native_remedies_cover_the_rejected_feature(case):
    from copy import deepcopy
    from server.design.schema import DesignConfig
    from test_native_formulas import design_payload
    payload = deepcopy(design_payload('OSSE-ARC' if case == 'arc' else 'OSSE-NATIVE'))
    if case == 'arc':
        payload['terminating_arc'] = None
        expected = 'terminating arc'
    elif case == 'contours':
        payload['source'] = {'contours': 'legacy source'}
        expected = 'Clear Source contours'
    elif case == 'sampling':
        payload['mesh']['z_map_points'] = '0,0;1,1'
        expected = 'clear Z-map points'
    else:
        payload['length_mode'] = 'total'
        expected = 'Length mode'
    with pytest.raises(ValueError) as error:
        DesignConfig.model_validate(payload)
    words = user_error_message(error.value)
    assert expected in words
    assert 'needs Family' not in words
    if case == 'arc':
        payload = deepcopy(design_payload('OSSE-ARC'))
    elif case == 'contours':
        payload['source']['contours'] = ''
    elif case == 'sampling':
        payload['mesh']['z_map_points'] = ''
    else:
        payload['length_mode'] = 'profile'
    assert DesignConfig.model_validate(payload).root is not None
