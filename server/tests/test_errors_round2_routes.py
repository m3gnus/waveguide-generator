"""Exercise public refusals through HTTP, persistence and the WGLink projector."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
import httpx2 as httpx
import pytest

from server.preview.errors import user_error_message


def capture(route, words):
    assert words and 'recorded' not in words and 'try again' not in words.lower()
    if path := os.environ.get('REFUSAL_SWEEP_LOG'):
        with Path(path).open('a') as stream:
            stream.write(json.dumps({'route': route, 'words': words}) + '\n')


def reason(kind):
    from hornlab_mesher.source_body import StandaloneSourceGeometry
    from hornlab_mesher.source_contour import dome
    from hornlab_mesher.throat_stretch import validate_stretch_composition
    from server.mesh.file_import import parse_file_mesh
    from server.mesh.imported import validate_imported_sizes
    calls = {
        'native': lambda: StandaloneSourceGeometry(radius_mm=18, outer_radius_mm=18, depth_mm=8),
        'surround': lambda: dome(12, 4, surround_width_mm=2, surround_depth_mm=2),
        'stretch': lambda: validate_stretch_composition({'s1': 1, 's2': 1, 'slotLength': 10}, 'OSSE'),
        'file': lambda: parse_file_mesh('$MeshFormat\n4.1 0 8\n$EndMeshFormat\n'),
        'driver': lambda: validate_imported_sizes([{'id': 'HF'}], {'rigid_size_mm': 10, 'transition_mm': 5, 'source_size_mm': {'HF': 0}}),
    }
    try:
        calls[kind]()
    except ValueError as error:
        return error
    raise AssertionError('input did not fail')


@pytest.mark.parametrize('kind', ['native', 'surround', 'stretch', 'file', 'driver'])
def test_preview_http_and_job_record_preserve_the_real_remedy(kind, monkeypatch, tmp_path):
    from server.engines.registry import EngineRegistry
    from server.jobs.mesh_preview import mount_solver_mesh
    from server.mesh import builder
    from server.jobs.runtime import JobRuntime
    from server.jobs.runtime.core import _failure_message
    from server.jobs.store import JobStore
    from test_jobs_runtime import _running_job
    error = reason(kind)
    async def refuse(*args, **kwargs):
        raise error
    monkeypatch.setattr(builder, 'estimate_solver_mesh', refuse)
    app = FastAPI()
    mount_solver_mesh(app, EngineRegistry(detector=lambda: []))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/mesh-estimate', json={'design': {'formula': 'OSSE', 'L': 120, 'a': 45}})
        assert response.status_code == 422
        detail = response.json()['detail']
        words = f"{detail['user_message']} {detail['next_step']}" if isinstance(detail, dict) else detail
        capture('HTTP mesh preview: ' + kind, words)
        assert words == user_error_message(error)
        store = JobStore(tmp_path / 'jobs.db')
        store.initialize()
        store.create_job(_running_job('refused'))
        runtime = JobRuntime(store)
        runtime._started = True
        try:
            await runtime._fail_job('refused', _failure_message(error))
            row = store.get_job_row('refused')
            assert row['status'] == 'error'
            assert row['error_message'] == words
            capture('job record: ' + kind, row['error_message'])
        finally:
            await runtime.shutdown()
    asyncio.run(run())


def test_real_file_import_inputs_keep_their_remedies(tmp_path):
    from test_models_api import app_for
    from test_file_mesh_roles_symmetry import box_mesh
    from server.cadlink.store import CadLinkStore
    from server.mesh.file_import import mesh_text
    async def run():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                cases = [('/api/models/files/inspect', {'filename': 'bad.msh', 'msh_text': '$MeshFormat\n4.1 0 8\n$EndMeshFormat\n'}),
                         ('/api/models/files', {'filename': 'half.msh', 'msh_text': mesh_text(box_mesh(planes=('x0',))), 'units': 'm', 'forward': '+z', 'up': '+y', 'roles': {101: 'HF', 102: 'MF'}, 'domain_planes': []})]
                for path, body in cases:
                    response = await client.post(path, json=body)
                    assert response.status_code == 422
                    capture(path, response.json()['detail'])
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(run())


def test_project_gate_crosses_the_real_wglink_projector():
    from server.cadlink.intake.ingest import _admit_return, IngestRefusal
    from server.cadlink.joblane.request_status import project_request_status
    from server.cadlink.wglink_protocol import user_text_violations
    with pytest.raises(IngestRefusal) as caught:
        _admit_return(SimpleNamespace(get_design=lambda _: None), {'instances': [{'design_id': 'design-a'}]}, None, None)
    words = user_error_message(caught.value)
    job = {'id': 'refused', 'status': 'error', 'task_metadata': {'cad': {'refusal': {'code': 'model_needs_change', 'message': words}}}}
    projected, incident = project_request_status({'commandId': 'request', 'kind': 'prepare_and_solve'}, None, job, {'message': words})
    assert incident is None and not projected['failure']['recorded']
    assert projected['failure']['message'] == words
    assert not user_text_violations(words)
    capture('WGLink projection', projected['failure']['message'])


def test_solve_plan_keeps_a_domain_refusal(tmp_path, monkeypatch):
    from server.jobs.api import create_jobs_router
    from test_models_api import app_for
    from server.cadlink.store import CadLinkStore
    async def run():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        async def refuse(*args, **kwargs):
            raise reason('stretch')
        monkeypatch.setattr(runtime, 'validate_submission', refuse)
        app.include_router(create_jobs_router(runtime))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/api/solve/plan', json={'design': {'formula': 'OSSE', 'L': 120, 'a': 45}})
            assert response.status_code == 422
            capture('solve plan', response.json()['detail'])
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(run())


@pytest.mark.parametrize('path', ['/api/models/assembly/context', '/api/models/assembly/compile'])
def test_assembly_placement_keeps_the_named_fix(path, tmp_path, monkeypatch):
    from test_models_api import app_for
    from server.cadlink.store import CadLinkStore
    from server.jobs import models_api
    from server.mesh.builder import AssemblyRefusal
    from hornlab_mesher.enclosure_woofers import resolve_woofer_placements
    row = {'radiator_id': 'woofer', 'enabled': True, 'radius_mm': 50,
           'placement': {'anchor': 'free', 'x_mm': 10, 'y_mm': -100,
                         'spacing_to': {'radiator_id': 'missing', 'distance_mm': 100}}}
    with pytest.raises(ValueError) as error:
        resolve_woofer_placements([row], mouth_bottom_mm=-80, mouth_top_mm=80)
    async def refuse(*args, **kwargs):
        raise AssemblyRefusal('Woofer placement: ' + user_error_message(error.value))
    monkeypatch.setattr(models_api, 'assembly_front_context', refuse)
    monkeypatch.setattr(models_api, 'compile_design_assembly', refuse)
    async def run():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                body = {'design': {'formula': 'OSSE', 'L': 120, 'a': 45}}
                if path.endswith('/compile'):
                    body.update(design_uid='wgu_00000000000000000000000000', design_revision=1)
                response = await client.post(path, json=body)
            assert response.status_code == 422
            words = response.json()['detail']
            assert words.startswith('Woofer placement:') and 'Spacing from' in words
            capture(path, words)
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(run())


def test_native_source_validation_preserves_a_real_http_remedy():
    from server.engines.registry import EngineRegistry
    from server.jobs.mesh_preview import mount_solver_mesh
    app = FastAPI()
    mount_solver_mesh(app, EngineRegistry(detector=lambda: []))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/mesh-estimate', json={'design': {'formula': 'SOURCE-DISK', 'source_body': {'radius_mm': 18, 'outer_radius_mm': 18, 'depth_mm': 8}}})
        assert response.status_code == 422
        refusal = response.json()['detail'][0]['ctx']['error']
        assert refusal['field'] == 'Source body radius'
        capture('HTTP native validation', refusal['user_message'] + ' ' + refusal['next_step'])
    asyncio.run(run())


@pytest.mark.parametrize('path', ['/api/solve/plan', '/api/solve'])
def test_native_validation_remedy_also_reaches_solve_envelopes(path, tmp_path):
    from server.jobs.api import create_jobs_router
    from test_models_api import app_for
    from server.cadlink.store import CadLinkStore
    async def run():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        app.include_router(create_jobs_router(runtime))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post(path, json={'design': {'formula': 'SOURCE-DISK', 'source_body': {'radius_mm': 18, 'outer_radius_mm': 18, 'depth_mm': 8}}})
            assert response.status_code == 422
            words = response.json()['detail']
            assert '0.1 mm above Source disk radius' in words
            capture(path + ': native validation', words)
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(run())


def test_sharp_corner_input_reaches_the_http_preview_with_its_fix():
    from server.engines.registry import EngineRegistry
    from server.jobs.mesh_preview import mount_solver_mesh
    app = FastAPI()
    mount_solver_mesh(app, EngineRegistry(detector=lambda: []))
    profile = {'points': [{'t': 0, 'r': 100, 'join': 'corner'}, {'t': .5, 'r': 110, 'join': 'corner'}, {'t': 1, 'r': 100, 'join': 'corner'}]}
    design = {'formula': 'FREEFORM', 'length': 2, 'profile_h': profile, 'profile_v': profile,
              'cross_sections': [{'t': 0, 'shape': 'ellipse'}, {'t': 1, 'shape': 'ellipse'}],
              'mesh': {'wall_thickness': 2, 'throat_resolution': 5, 'mouth_resolution': 10}}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/mesh-estimate', json={'design': design})
        assert response.status_code == 422
        words = response.json()['detail']
        assert 'sharp profile corner' in words and 'Bare shell in Mounting' in words
        capture('HTTP preview: real 2 mm sharp corner', words)
    asyncio.run(run())
