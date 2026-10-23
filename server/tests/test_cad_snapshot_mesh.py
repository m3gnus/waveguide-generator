"""Snapshot sizing transport, legacy wire identity and packaged-pair skew."""
from __future__ import annotations

import ast
from copy import deepcopy
import gzip
import hashlib
import json
import re
import shutil
from types import SimpleNamespace

import pytest

from server.cadlink.fusion.fusion_delivery import capabilities
from server.cadlink.intake import ingest as ingest_module
from server.cadlink.intake.ingest import IngestRefusal, ingest_bundle
from server.cadlink.joblane.default_setup import default_mesh, solve_defaults
from server.cadlink.snapshot_mesh import snapshot_mesh
from server.cadlink.store import CadLinkStore
from server.cadlink.wglink_protocol import MESH_DEFAULTS
from server.cadlink.wgreturn import read_wgreturn, validate_manifest
from server.jobs.imported_setup import setup_content, setup_digest, validate_setup
from server.jobs.models import ImportedMeshSizes
from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.tests.test_wglink_packaged_pair import CORPUS, PIN, _from_package, _package_source, packaged  # noqa: F401
from server.tests.test_wglink_shared_protocol import SHARED, SOURCE, addin  # noqa: F401

FIXTURES = SHARED / 'mesh-sizing-v1'
BUNDLES = sorted(FIXTURES.glob('*.wgreturn'))


def test_defaults_match_the_shared_solve_defaults():
    cad = solve_defaults()['cad']
    mesh = cad['mesh']
    assert MESH_DEFAULTS == {
        'wall_size_mm': mesh['rigid_size_mm'],
        'grading_distance_mm': mesh['transition_mm'],
        'symmetry': cad['preparation_symmetry_mode'],
        'source_size_mm': {role: policy['size_mm'] for role, policy in mesh['source_roles'].items()
                           if role != 'PORT_EXIT'},
    }
    assert mesh['source_roles']['PORT_EXIT']['size_mm'] == MESH_DEFAULTS['source_size_mm']['PASSIVE_CARDIOID']
    assert type(capabilities()['meshSizing']) is int
    assert capabilities()['meshSizing'] == 1


@pytest.mark.parametrize('bundle', BUNDLES, ids=lambda p: p.stem)
def test_snapshot_translation_is_detached_and_legacy_is_absent(bundle):
    manifest = read_wgreturn(bundle).manifest
    before = deepcopy(manifest)
    result = snapshot_mesh(manifest)
    assert result.sizes == {
        'rigid_size_mm': manifest['mesh']['wall_size_mm'],
        'transition_mm': manifest['mesh']['grading_distance_mm'],
        'source_size_mm': manifest['mesh']['source_size_mm'],
        'surface_groups': manifest['mesh']['surface_groups'],
    }
    assert result.symmetry_mode == 'auto'
    result.sizes['surface_groups'][0]['name'] = 'Changed'
    result.sizes['source_size_mm'].clear()
    assert manifest == before
    manifest['mesh']['symmetry'] = 'off'
    assert snapshot_mesh(manifest).symmetry_mode == 'full'
    manifest.pop('mesh')
    manifest['required_features'].remove('mesh-sizing-v1')
    assert snapshot_mesh(manifest) is None


def test_absent_groups_preserve_legacy_wire_and_setup_digest():
    from server.tests.test_cad_preparation import _setup
    legacy = _setup()
    mesh = legacy['geometry']['mesh']
    assert ImportedMeshSizes.model_validate(mesh).model_dump(mode='json') == mesh
    before = validate_setup(legacy)
    with_null = deepcopy(legacy)
    with_null['geometry']['mesh']['surface_groups'] = None
    after = validate_setup(with_null)
    assert setup_content(before) == setup_content(after)
    assert setup_digest(before) == setup_digest(after)
    assert ImportedMeshSizes.model_validate({**mesh, 'surface_groups': []}).model_dump()['surface_groups'] == []


@pytest.fixture(scope='module')
def frozen_protocol():
    raw = gzip.decompress((FIXTURES / 'pre-w1-protocol.py.gz').read_bytes())
    provenance = json.loads((FIXTURES / 'PROVENANCE.json').read_text())
    assert hashlib.sha256(raw).hexdigest() == provenance['frozen_protocol_sha256']
    namespace = {}
    exec(compile(raw, 'pre-w1-protocol.py', 'exec'), namespace)
    return SimpleNamespace(**namespace)


def _mesh_gate(folder, advertised):
    """Run the exact packaged add-in gate without importing Fusion's host API."""
    tree = ast.parse((folder / 'WGLink.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_required_manifest_features')
    with _from_package(folder, 'wglink_inbox.py') as inbox, _from_package(folder, 'wglink_protocol.py') as protocol:
        namespace = {
            '_manifest_feature_enabled': lambda name: inbox.wg_manifest_feature(advertised, name),
            'wglink_inbox': inbox,
            'wglink_core': SimpleNamespace(WgLinkError=ValueError),
            'wglink_send': SimpleNamespace(wglink_protocol=protocol),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'WGLink.py', 'exec'), namespace)
        return namespace['_required_manifest_features']()


@pytest.fixture(scope='module')
def snapshot_packaged(addin, tmp_path_factory):  # noqa: F811
    root = tmp_path_factory.mktemp('wglink-snapshot-package')
    version = json.loads((addin / 'WGLink.manifest').read_text())['version']
    return _package_source(addin.parents[1], root, {**PIN, 'commit': SOURCE['commit'], 'addinVersion': version})


@pytest.mark.parametrize('wg', ['wg-0.3.5', 'wg-0.3.6-old', 'new'])
@pytest.mark.parametrize('writer', ['0.3.1', 'new'])
def test_packaged_compatibility_matrix(wg, writer, packaged, snapshot_packaged, frozen_protocol, tmp_path):  # noqa: F811
    """Frozen readers/capabilities and exact packaged writer objects, no pin move."""
    advertised = capabilities() if wg == 'new' else json.loads((FIXTURES / f'{wg}-capabilities.json').read_text())
    ipc = tmp_path / 'ipc'
    ipc.mkdir()
    (ipc / 'wg-capabilities.json').write_text(json.dumps(advertised))
    if writer == 'new':
        if wg != 'new':
            with pytest.raises(ValueError, match='Update and restart Waveguide Generator'):
                _mesh_gate(snapshot_packaged, ipc)
            forced = json.loads((BUNDLES[0] / 'wgreturn.json').read_text())
            with pytest.raises(frozen_protocol.ProtocolValidationError):
                frozen_protocol.validate_structure(forced, frozen_protocol.WG_INGRESS)
            del forced['mesh']
            with pytest.raises(frozen_protocol.ProtocolValidationError, match=r'unknown required feature\(s\): mesh-sizing-v1'):
                frozen_protocol.validate_structure(forced, frozen_protocol.WG_INGRESS)
            return
        assert _mesh_gate(snapshot_packaged, ipc)['automatic_domain'] is True
        manifest = read_wgreturn(BUNDLES[0]).manifest
        assert snapshot_mesh(manifest).sizes['rigid_size_mm'] == 25
    else:
        # The released writer still emits legacy returns without injecting a mesh.
        fields = deepcopy(json.loads((CORPUS / 'automatic-full.wgreturn/wgreturn.json').read_text()))
        fields['return_record'] = fields.pop('return')
        fields.pop('acoustics')
        for body in fields['scope']['included']:
            body.update(component='Body1', external_reference='none')
        with _from_package(packaged, 'wglink_return.py') as old_writer:
            manifest = old_writer.build_return_manifest(**fields)
        before = deepcopy(manifest)
        frozen_protocol.validate_structure(manifest, frozen_protocol.WG_INGRESS)
        validate_manifest(manifest)
        assert manifest == before and 'mesh' not in manifest
        assert snapshot_mesh(manifest) is None
        # M0 policy is unchanged: unedited role suggestions use current WG defaults.
        if wg in ('wg-0.3.6-old', 'new'):
            assert default_mesh(manifest)['rigid_size_mm'] == 25
    validate_manifest(manifest)


def _open_quarter_fixture(bundle, tmp_path):
    """Open F2's capped cut faces, retaining all source/group STEP identities.

    The upstream protocol fixture is a closed box despite declaring a quarter.
    Keep the vendor bytes and its refusal test; this derived bundle exercises a
    physically valid quarter without bypassing WG's domain checks.
    """
    import gmsh
    from hornlab_mesher.step_import import advanced_face_order_for_surfaces

    step = bundle / 'assembly.step'

    def caps():
        gmsh.clear()
        gmsh.option.setNumber('General.Terminal', 0)
        gmsh.model.occ.importShapes(str(step))
        gmsh.model.occ.synchronize()
        surfaces = [tag for _dim, tag in gmsh.model.getEntities(2)]
        order = advanced_face_order_for_surfaces(step, surfaces)
        result = []
        for tag, face in zip(surfaces, order, strict=True):
            center = gmsh.model.occ.getCenterOfMass(2, tag)
            if abs(center[0]) < 1e-8 or abs(center[1]) < 1e-8:
                result.append(face)
        gmsh.clear()
        return result

    removed = set(_run_in_gmsh_session(caps))
    assert len(removed) == 2
    derived = tmp_path / bundle.name
    shutil.copytree(bundle, derived)
    text = step.read_text()

    def shell(match):
        faces = [f for f in match[2].split(',') if int(f.strip().removeprefix('#')) not in removed]
        return f"#{match[1]} = OPEN_SHELL('',({','.join(faces)}));"

    text, count = re.subn(r"#(\d+)\s*=\s*CLOSED_SHELL\('',\(([^)]*)\)\);", shell, text)
    assert count == 1
    text, count = re.subn(r"MANIFOLD_SOLID_BREP\('([^']*)',#(\d+)\)", r"SHELL_BASED_SURFACE_MODEL('\1',(#\2))", text)
    assert count == 1
    for face in removed:
        text, count = re.subn(rf'#{face}\s*=\s*ADVANCED_FACE[^;]*;', '', text)
        assert count == 1
    data = text.encode()
    (derived / 'assembly.step').write_bytes(data)
    manifest = json.loads((derived / 'wgreturn.json').read_text())
    manifest['files']['assembly.step'].update(sha256='sha256:' + hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    manifest['scope']['included'][0]['body_kind'] = 'surface'
    (derived / 'wgreturn.json').write_text(json.dumps(manifest))
    return derived


@pytest.mark.parametrize('bundle', BUNDLES, ids=lambda p: p.stem)
def test_shared_fixtures_ingest_at_snapshot_sizes(bundle, tmp_path, monkeypatch):
    built_meshes = []
    build = ingest_module.build_imported_mesh_isolated

    def capture(*args, **kwargs):
        result = build(*args, **kwargs)
        built_meshes.append((args[2], kwargs['options'], result))
        return result

    monkeypatch.setattr(ingest_module, 'build_imported_mesh_isolated', capture)
    data = tmp_path / 'data'
    data.mkdir()
    with_store = CadLinkStore.for_data_dir(data)
    try:
        if bundle.name.startswith('f2-'):
            with pytest.raises(IngestRefusal, match='cut plane is capped'):
                _run_in_gmsh_session(
                    ingest_bundle, bundle, snapshot_mesh(read_wgreturn(bundle).manifest).sizes,
                    [], with_store, data, defer_viewport=True,
                )
            bundle = _open_quarter_fixture(bundle, tmp_path)
        manifest = read_wgreturn(bundle).manifest
        wrong = {'rigid_size_mm': 4, 'transition_mm': 4,
                 'source_size_mm': {s['id']: 4 for s in manifest['sources']}}
        record = _run_in_gmsh_session(
            ingest_bundle, bundle, wrong, [], with_store, data,
            prep_options={'symmetry_mode': 'full'}, defer_viewport=True,
        )
        assert record['mesh_sizes'] == snapshot_mesh(manifest).sizes
        assert record['mesh_snapshot'] == manifest['mesh']
        sizes, options, built = built_meshes[-1]
        assert sizes == record['mesh_sizes']
        assert options['surface_groups'] == manifest['mesh']['surface_groups']
        group = built['surface_group_resolution']['back']
        assert group['size_mm'] == 40
        fraction = .25 if bundle.name.startswith('f1-') else 1
        assert group['retained_area_mm2'] == pytest.approx(manifest['mesh']['surface_groups'][0]['observed']['total_area_mm2'] * fraction)
        count = record['mesh']['stats']['triangle_count']
        assert 500 < count < (4000 if bundle.name.startswith('f1-') else 10000)
        assert record['symmetry']['domain_planes'] == ['x0', 'y0']
        assert record['source_tags'] == {s['id']: 101 + i for i, s in enumerate(manifest['sources'])}
    finally:
        with_store.close()
