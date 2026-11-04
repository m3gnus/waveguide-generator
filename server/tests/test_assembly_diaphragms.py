"""Sketched documents compile into the shared assembly record and excitations."""
import asyncio
import copy
from pathlib import Path

import pytest

from hornlab_mesher.source_contour import cone, dome
from server.cadlink.intake.design_assembly import compile_spec_digest, compose_record, compile_design_assembly
from server.design.schema import DesignConfig
from server.mesh.builder import AssemblyRefusal, _build_assembly_sync
from server.source_editor import SourceDocument, canonical_document, native
from test_design_assembly_record import HORN, W1, UID, assembly, woofer


def document(contour, motion='axial'):
    from hornlab_mesher.source_contour import ContourDrive
    return canonical_document(contour, ContourDrive('motor', tuple(
        (s.id, -.25 if s.id == 'surround' else 1.) for s in contour.segments if s.role == 'moving'), motion))


def sketched_design():
    w = woofer()
    w['shape'] = {'kind': 'sketched', 'diaphragm': document(cone(
        75, 25, cap_radius_mm=20, cap_height_mm=6, surround_width_mm=5, surround_depth_mm=-1))}
    return DesignConfig.model_validate({**copy.deepcopy(HORN), 'assembly': assembly(w)})


def test_schema_roundtrip_and_canonical_source_document():
    config = sketched_design()
    from server.design.textcfg import parse, serialize
    saved = serialize(config)
    assert parse(saved).design.model_dump(mode='json')['assembly'] == config.root.assembly.model_dump(mode='json')
    assert serialize(parse(saved)) == saved
    dump = config.model_dump(mode='json')
    assert DesignConfig.model_validate(dump).model_dump(mode='json') == dump
    source = config.root.assembly.woofers[0].shape.diaphragm
    contour, drive = native(source)
    assert source.model_dump(mode='json') == SourceDocument.model_validate(canonical_document(contour, drive)).model_dump(mode='json')
    assert 'status' not in dump['assembly']['woofers'][0]['shape']
    assert dump['assembly']['hf_source'] == {'radiator_id': 'horn-throat', 'kind': 'classic'}


def test_record_determinism_signed_weights_and_committed_geometry(tmp_path):
    config = sketched_design()
    built = _build_assembly_sync(config.model_dump(mode='json'))
    kwargs = dict(ingest_id='wgi_01JABCDEFGHJKMNPQRSTVWXYZ0', created_at='2026-11-04T00:00:00Z',
                  design_uid=UID, design_name='Horn', design_revision=1, spec_digest=compile_spec_digest(config),
                  archive_stem='', destination=tmp_path)
    first, text = compose_record(config, built, **kwargs)
    second, text2 = compose_record(config, built, **kwargs)
    assert first == second and text == text2
    assert first['radiators'][1]['compiled_from_committed_valid_geometry'] is True
    assert 'projected_area_m2' not in first['radiators'][1]
    assert first['radiators'][1]['aperture_area_m2'] > 0
    assert first['excitations'][1]['patch_weights'][W1+'/surround'] == -.25
    assert first['excitations'][1]['motion'] == 'axial'
    # Authoring does not redefine compiled geometry; a valid re-open reuses it.
    config.root.assembly.woofers[0].shape.authoring = {'version': 'wg-source-authoring-v2', 'pending': False}
    assert compile_spec_digest(config) == first['manifest_sha256']


@pytest.mark.parametrize('status,pending', [('unfinished',False), ('invalid',False), ('valid',True)])
def test_unfinished_sketch_refused_before_any_record_lookup(status, pending, tmp_path):
    config = sketched_design()
    shape = config.root.assembly.woofers[0].shape
    shape.status = status
    shape.authoring = {'version': 'wg-source-authoring-v2', 'pending': pending}
    with pytest.raises(AssemblyRefusal, match='Finish or revert the Woofer aa sketch before solving'):
        asyncio.run(compile_design_assembly(None, tmp_path, config.model_dump(mode='json'),
                    design_uid=UID, design_name='Horn', design_revision=2))


def test_sketched_hf_only_record(tmp_path):
    body = {**copy.deepcopy(HORN), 'assembly': assembly()}
    body['assembly']['hf_source'] = {'kind': 'sketched', 'diaphragm': document(dome(12.7, 3), 'normal')}
    config = DesignConfig.model_validate(body)
    built = _build_assembly_sync(config.model_dump(mode='json'))
    record, _ = compose_record(config, built, ingest_id='wgi_01JABCDEFGHJKMNPQRSTVWXYZ0',
                              created_at='2026-11-04T00:00:00Z', design_uid=UID, design_name='HF',
                              design_revision=1, spec_digest=compile_spec_digest(config),
                              archive_stem='', destination=Path(tmp_path))
    assert record['excitations'] == [{'id': 'horn-throat', 'source_ids': ['horn-throat/dome'],
                                     'motion': 'normal', 'patch_weights': {'horn-throat/dome': 1.0}}]
    assert record['radiators'][0]['compiled_from_committed_valid_geometry']



def test_disabled_unfinished_sketch_still_refuses_solve(tmp_path):
    config = sketched_design()
    config.root.assembly.woofers[0].enabled = False
    config.root.assembly.woofers[0].shape.status = 'unfinished'
    with pytest.raises(AssemblyRefusal, match='Finish or revert'):
        asyncio.run(compile_design_assembly(None, tmp_path, config.model_dump(mode='json'),
                    design_uid=UID, design_name='Horn', design_revision=2))


def test_request_cannot_change_compiled_signed_weights():
    from types import SimpleNamespace
    from server.contracts.source_contour import validate_contour_request
    from server.jobs.models import DriveChannel
    excitation = {'id': W1, 'source_ids': [W1+'/cone'], 'motion': 'normal',
                  'patch_weights': {W1+'/cone': -.25}}
    geometry = SimpleNamespace(required_features=['wg-design-assembly-v1'], skipped_source_ids=[],
                               drive_channels=[DriveChannel(id=W1, source_ids=excitation['source_ids'],
                                             patch_weights=excitation['patch_weights'])])
    record = {'required_features': geometry.required_features, 'excitations': [excitation]}
    validate_contour_request(geometry, record)
    geometry.drive_channels[0].patch_weights = {W1+'/cone': 1.}
    with pytest.raises(ValueError, match='contradicts'):
        validate_contour_request(geometry, record)



def test_old_mesher_cannot_silently_build_a_classic_source(monkeypatch):
    import builtins
    from server.mesh.builder import assembly_mesher_config
    original = builtins.__import__
    def old_mesher(name, *args, **kwargs):
        if name == 'hornlab_mesher.design_diaphragm':
            raise ModuleNotFoundError(name=name)
        return original(name, *args, **kwargs)
    config = sketched_design()
    monkeypatch.setattr(builtins, '__import__', old_mesher)
    with pytest.raises(AssemblyRefusal, match='installed mesher.*Update Waveguide Generator'):
        assembly_mesher_config(config)
