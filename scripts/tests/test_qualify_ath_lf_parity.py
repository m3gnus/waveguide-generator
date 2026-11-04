"""Small ABEC fixtures and synthetic complex fields; never run a solver."""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import qualify_ath_lf_parity as parity
from server.mesh.file_import import FileMesh
from server.solver.combine import serialize_channel_bases

PROJECT = '''[Solving]
Scriptname_Solving=solving.txt
[Observation]
C0=observation.txt
[MeshFiles]
C0=case.msh,M1
'''
SOLVING = '''Control_Solver
  f1=800; f2=12000; Sym=x
MeshFile_Properties
  MeshFileAlias="M1"; Scale=1mm
Elements "throat-elements"
  Subdomain=1; MeshFileAlias="M1"
  101 Mesh Include throat-physical
Driving "S1001" // horn_driver
  RefElements="throat-elements"; DrvGroup=1001;
Elements "lf-elements"
  Subdomain=2; MeshFileAlias="M1"
  999 Mesh Include bass-physical
Driving "S1002" // woofer_B
  RefElements="lf-elements"; DrvGroup=1002; Direction=z
Elements "join"
  Subdomain=1,2; MeshFileAlias="M1"
  201 Mesh Include I1-2
'''
OBSERVATION = '''Driving_Values
  DrvType=Acceleration; Value=2.0
  401 DrvGroup=1001 Weight=1 Delay=0ms
  402 DrvGroup=1002 Weight=-0.25 Delay=0.001s
BE_Spectrum
  PlotType=Polar; GraphHeader="PM_h"
  PolarRange=0,90,3
  BasePlane=zx
  Distance=200cm
  501 Inclination=0 DrvGroups=1001,1002 ID=5001
BE_Spectrum
  PlotType=Polar; GraphHeader="PM_v"
  PolarRange=0,90,3
  BasePlane=zx
  Distance=2m
  502 Inclination=90 DrvGroups=1001 ID=5002
'''


def test_project_resolves_files_and_aliases():
    assert parity.parse_project(PROJECT) == {"solving": "solving.txt", "observations": ["observation.txt"], "meshes": {"M1": "case.msh"}}


@pytest.mark.parametrize("path", ["../case.msh", "/tmp/case.msh", "Z:\\case.msh"])
def test_project_refuses_external_files(path):
    with pytest.raises(parity.EvidenceRefusal, match="local filenames"):
        parity.parse_project(PROJECT.replace("case.msh", path))


def test_solving_maps_element_names_not_abec_element_numbers_to_mesh_names():
    result = parity.parse_solving(SOLVING.replace("\n", "\r\n"))
    assert result["units"] == {"M1": "mm"} and result["domain_planes"] == ["x0"]
    assert result["elements"]["lf-elements"] == {"physical_name": "bass-physical", "alias": "M1", "subdomains": [2]}
    assert result["drives"][1] == {"name": "S1002", "group": 1002, "element": "lf-elements", "radiator": "B", "motion": "axial"}
    assert result["elements"]["join"]["subdomains"] == [1, 2]


@pytest.mark.parametrize(("old", "new"), [("Direction=z", "Direction=x"), ("Sym=x", "Sym=z"),
    ("Scale=1mm", "Scale=2mm"), ("// woofer_B", "// unknown"), ("DrvGroup=1002", "DrvGroup=1001")])
def test_unsupported_solving_cannot_be_guessed(old, new):
    with pytest.raises(parity.EvidenceRefusal):
        parity.parse_solving(SOLVING.replace(old, new))


def test_observation_preserves_signed_weights_delay_units_and_exact_arc_count():
    obs = parity.parse_observation(OBSERVATION)
    assert obs["weights"] == {1001: {"weight": 2, "delay_ms": 0}, 1002: {"weight": -.5, "delay_ms": 1}}
    assert [arc["plane"] for arc in obs["arcs"]] == ["horizontal", "vertical"]
    assert obs["arcs"][0]["angle_range"] == [0, 90, 3]
    assert obs["arcs"][0]["distance_m"] == 2
    assert obs["arcs"][0]["driving_groups"] == [1001, 1002]
    polar = parity.polar_options(obs["arcs"])
    assert polar["observation_origin"] == "throat" and polar["spherical_sampling"] is True
    assert polar["angle_range"] == [0, 90, 19]


@pytest.mark.parametrize(("old", "new"), [("Acceleration", "Velocity"), ("BasePlane=zx", "BasePlane=xy"),
    ("Inclination=90", "Inclination=45"), ("Distance=2m", "Distance=2"), ("Weight=-0.25", "Weight=nan")])
def test_unsupported_observations_fail_with_remedy(old, new):
    with pytest.raises(parity.EvidenceRefusal):
        parity.parse_observation(OBSERVATION.replace(old, new))


def test_harness_observation_does_not_depend_on_ath_arcs():
    expected = parity.polar_options([])
    assert expected["distance"] == 2 and expected["angle_range"] == [0, 90, 19]
    arcs = parity.parse_observation(OBSERVATION)["arcs"]
    arcs[1]["distance_m"] = 3
    assert parity.polar_options(arcs) == expected


def fields():
    f = [100, 1000, 5000, 10000, 16000]
    angles = [0, 45, 90]
    pressure = np.ones((5, 2, 3), dtype=complex)
    return f, angles, pressure


def test_metrics_identity_band_boundaries_and_wrapped_phase():
    f, angles, a = fields()
    b = a * np.exp(1j * np.deg2rad(-358))
    result = parity.metric_differences(f, angles, a, b, np.zeros(5), np.zeros(5))
    assert result["pass"]
    assert result["bands"]["below_1k"]["on_axis_spl_db"]["raw"]["count"] == 1
    assert result["bands"]["10k_20k"]["on_axis_spl_db"]["tolerance"] == 1
    assert result["bands"]["5k_10k"]["phase_deg"]["eligible"]["max_abs"] == pytest.approx(2)


def test_gain_polar_phase_and_di_errors_have_raw_max_rms_and_can_fail():
    f, angles, a = fields()
    b = a * 10 ** (.6 / 20) * np.exp(1j * np.deg2rad(6))
    b[:, 1, 2] *= 10 ** (.2 / 20)
    result = parity.metric_differences(f, angles, a, b, np.zeros(5), np.ones(5) * .4)
    band = result["bands"]["below_1k"]
    assert not result["pass"]
    assert band["on_axis_spl_db"]["raw"]["max_abs"] == pytest.approx(.6)
    assert band["vertical_spl_db"]["raw"]["max_abs"] == pytest.approx(.8)
    assert band["vertical_spl_db"]["raw"]["rms"] == pytest.approx(np.sqrt((.6**2 * 2 + .8**2) / 3))
    assert band["phase_deg"]["eligible"]["max_abs"] == pytest.approx(6)
    assert not band["di_db"]["pass"]
    assert result["bands"]["10k_20k"]["on_axis_spl_db"]["pass"]


def test_deep_nulls_are_excluded_but_raw_errors_remain_and_silence_is_not_pass():
    f, angles, a = fields()
    a[0] *= 1e-3
    b = a.copy()
    b[0] *= 10
    result = parity.metric_differences(f, angles, a, b, np.zeros(5), np.zeros(5))
    band = result["bands"]["below_1k"]["on_axis_spl_db"]
    assert band["raw"]["max_abs"] == pytest.approx(20)
    assert band["eligible"]["count"] == 0 and band["pass"] is None
    assert band["deep_null"]["count"] == 1
    silent = np.zeros_like(a)
    result = parity.metric_differences(f, angles, silent, silent, np.zeros(5), np.zeros(5))
    assert result["bands"]["below_1k"]["phase_deg"]["eligible"]["count"] == 0
    assert result["pass"] is None


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_invalid_metrics_never_become_evidence(bad):
    f, angles, a = fields()
    a[0, 0, 0] = bad
    with pytest.raises(ValueError, match="finite"):
        parity.metric_differences(f, angles, a, a, np.zeros(5), np.zeros(5))


def test_surface_distance_uses_triangle_interiors_and_bounds_whole_facets():
    a = FileMesh(np.array([[0., 0, 0], [2, 0, 0], [0, 2, 0]]), np.array([[0, 1, 2]]), np.array([1]), {})
    b = copy.deepcopy(a)
    b.points = np.vstack([b.points, [.5, .5, 0]])
    b.triangles = np.array([[0, 1, 3], [1, 2, 3], [2, 0, 3]])
    assert parity._triangle_distances(np.array([.5, .5, 0]), a.points[a.triangles])[0] == pytest.approx(0)
    equal = parity.surface_hausdorff(a, b)
    assert equal["lower_bound_mm"] == pytest.approx(0, abs=1e-12)
    b.points[:, 2] = 3
    moved = parity.surface_hausdorff(a, b)
    assert moved["lower_bound_mm"] == pytest.approx(3)
    assert moved["upper_bound_mm"] >= 3


def _bases(velocity=False):
    f = np.array([100., 1000.])
    angles = np.array([0., 45., 90.])
    theta, phi = np.meshgrid(np.linspace(0, 180, 5), np.arange(8) * 45, indexing="ij")
    scale = -2j * np.pi * f if velocity else np.ones(2)
    data = {}
    for name in ("HF", "B"):
        data[name] = SimpleNamespace(frequencies_hz=f, observation_angles_deg=angles, observation_planes=["horizontal", "vertical"],
            pressure_complex=np.ones((2, 2, 3), complex) * scale[:, None, None],
            sphere_pressure_complex=np.ones((2, theta.size), complex) * scale[:, None],
            sphere_theta_deg=theta.ravel(), sphere_phi_deg=phi.ravel())
    return serialize_channel_bases(data, metadata_by_id={name: {
        "source_normalization": "native_rms_velocity" if velocity else "unit_normal_acceleration"} for name in data})


def test_grouping_reuses_combine_signed_weights_and_acceleration_conversion():
    plan = {"variant": "A", "radiator_ids": {"HF": "HF", "B": "B"}, "polar": {"distance": 2, "angle_range": [0, 90, 3]},
            "drives": [{"radiator": "HF", "weight": 1, "delay_ms": 0}, {"radiator": "B", "weight": -.5, "delay_ms": 0}]}
    a = parity.grouped_bases(_bases(), plan)
    b = parity.grouped_bases(_bases(velocity=True), {**plan, "variant": "B"})
    assert np.asarray(a["combined"]["pressure_real"]) == pytest.approx(np.full((2, 2, 3), .5))
    comparisons = parity.compare_groups(a, b)
    assert all(entry["absolute"]["pass"] for entry in comparisons.values())
    plan["drives"] = plan["drives"][:1]
    no_lf = parity.grouped_bases(_bases(), plan)
    assert no_lf["LF"]["status"] == "not-applicable"


@pytest.mark.parametrize("variant", ["A", "B"])
@pytest.mark.parametrize("velocity", [False, True])
def test_normalisation_comes_from_each_retained_channel_not_geometry(variant, velocity):
    plan = {"variant": variant, "radiator_ids": {"HF": "HF", "B": "B"},
            "polar": {"distance": 2, "angle_range": [0, 90, 3]},
            "drives": [{"radiator": "HF", "weight": 1, "delay_ms": 0}, {"radiator": "B", "weight": 1, "delay_ms": 0}]}
    grouped = parity.grouped_bases(_bases(velocity=velocity), plan)
    assert np.asarray(grouped["combined"]["pressure_real"]) == pytest.approx(np.full((2, 2, 3), 2.))
    assert np.asarray(grouped["combined"]["pressure_imag"]) == pytest.approx(np.zeros((2, 2, 3)))
    assert {m['conversion'] for m in grouped['combined']['channel_normalisation'].values()} == {
        'acceleration_scale' if velocity else 'identity'}


@pytest.mark.parametrize('normalisation', [None, 'native_rms_voltage', 'unknown'])
def test_missing_or_nonmechanical_basis_is_refused_instead_of_guessed(normalisation):
    from server.solver.combine import deserialize_channel_bases

    bases = deserialize_channel_bases(_bases())['results_by_id']
    payload = serialize_channel_bases(bases, metadata_by_id={k: {'source_normalization': normalisation} for k in bases})
    with pytest.raises(parity.EvidenceRefusal, match='source_normalization.*Retain'):
        parity.grouped_bases(payload, {'variant': 'A'})


def test_normalisation_can_differ_between_channels_in_one_solve():
    from server.solver.combine import deserialize_channel_bases
    from server.solver.imported_channels import scale_channel_result

    bases = deserialize_channel_bases(_bases())['results_by_id']
    scale_channel_result(bases['B'], -2j * np.pi * np.asarray(bases['B'].frequencies_hz))
    payload = serialize_channel_bases(bases, metadata_by_id={
        'HF': {'source_normalization': 'unit_normal_acceleration'}, 'B': {'source_normalization': 'native_rms_velocity'}})
    plan = {'variant': 'A', 'radiator_ids': {'HF': 'HF', 'B': 'B'}, 'polar': {'distance': 2, 'angle_range': [0, 90, 3]},
            'drives': [{'radiator': 'HF', 'weight': 1, 'delay_ms': 0}, {'radiator': 'B', 'weight': -.5, 'delay_ms': 0}]}
    groups = parity.grouped_bases(payload, plan)
    assert np.asarray(groups['combined']['pressure_real']) == pytest.approx(np.full((2, 2, 3), .5))


def test_mesh_limit_gates_spl_phase_di_and_ignores_out_of_band_peaks_for_nulls():
    f, angles, a = fields()
    b = a.copy()
    a[2:] *= 1e5
    b[2:] *= 1e8 * 1j
    di = [0, 0, 10, 10, 10]
    result = parity.metric_differences(f, angles, a, b, np.zeros(5), di, mesh_valid_hz=1000)
    assert result['pass'] is True
    assert result['frequency_status'] == ['valid', 'valid', 'mesh-limited', 'mesh-limited', 'mesh-limited']
    assert result['bands']['below_1k']['on_axis_spl_db']['eligible']['count'] == 1
    for quantity in result['bands']['5k_10k'].values():
        assert quantity['pass'] is None and quantity['eligible']['count'] == 0
        assert quantity['mesh_limited']['count'] > 0
    assert parity.metric_differences(f, angles, a, b, np.zeros(5), di, mesh_valid_hz=50)['pass'] is None


def test_mesh_adequacy_uses_largest_edge_and_correct_units():
    mesh = FileMesh(np.array([[0., 0, 0], [30, 0, 0], [0, 40, 0]]), np.array([[0, 1, 2]]), np.array([1]), {})
    result = parity.mesh_adequacy(mesh, .001)
    assert result['max_edge_mm'] == pytest.approx(50)
    assert result['max_valid_frequency_hz'] == pytest.approx(result['sound_speed_m_s'] / (.05 * 6))
    mesh.points *= .001
    assert parity.mesh_adequacy(mesh, 1.) == result


def _save_chunk(folder, side, indices):
    from server.mesh.file_import import mesh_text
    from server.solver.combine import deserialize_channel_bases

    folder.mkdir(parents=True, exist_ok=True)
    bases = deserialize_channel_bases(_bases())['results_by_id']
    for result in bases.values():
        result.frequencies_hz = result.frequencies_hz[indices]
        result.pressure_complex = result.pressure_complex[indices]
        result.sphere_pressure_complex = result.sphere_pressure_complex[indices]
    payload = serialize_channel_bases(bases, metadata_by_id={k: {'source_normalization': 'unit_normal_acceleration'} for k in bases})
    plan = {'variant': side, 'radiator_ids': {'HF': 'HF', 'B': 'B'}, 'polar': {'distance': 2, 'angle_range': [0, 90, 3]},
            'drives': [{'radiator': 'HF', 'weight': 1, 'delay_ms': 0}, {'radiator': 'B', 'weight': .5, 'delay_ms': 0}]}
    (folder / f'{side}.channel-bases.bin').write_bytes(payload)
    (folder / f'{side}.plan.json').write_text(json.dumps(plan))
    (folder / f'{side}.request.json').write_text(json.dumps({'geometry': {'drive_channels': [{'id': k} for k in bases]},
        'options': {'frequencies_hz': bases['HF'].frequencies_hz.tolist(), 'symmetry': 'half_yz',
                    'polar_config': parity._make_setup({'symmetry': {'domain_planes': ['x0']}}, [], [],
                        {**parity.polar_options(), 'angle_range': [0, 90, 3]})['options']['polar_config']}}))
    (folder / f'{side}.record.json').write_text(json.dumps({
        'normalisation': {'assembly_frame_is_solver_frame': True}, 'symmetry': {'domain_planes': ['x0']}}))
    mesh = half_tetrahedron()
    mesh.points *= .001
    (folder / f'{side}.msh').write_text(mesh_text(mesh))
    (folder / 'case.json').write_text(json.dumps({'case': 'test', 'status': 'side-complete'}))


@pytest.mark.parametrize('split', [False, True])
def test_recompute_uses_saved_bases_and_joins_side_frequency_jobs_without_preparing(tmp_path, monkeypatch, split):
    source, output = tmp_path / 'saved', tmp_path / 'compared'
    if split:
        for side in ['A', 'B']:
            for index in [0, 1]:
                _save_chunk(source / f'{side}-{index}' / 'test', side, [index])
    else:
        for side in ['A', 'B']:
            _save_chunk(source / 'test', side, [0, 1])
    before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    monkeypatch.setattr(parity, 'prepare_case', lambda *a: pytest.fail('recompute prepared geometry'))
    monkeypatch.setattr(parity.assembly, '_solve', lambda *a, **kw: pytest.fail('recompute started solver'))
    monkeypatch.setattr(parity, 'charts', lambda *a, **kw: [])
    assert parity.main(['--recompute', str(source), '--out', str(output)]) == 0
    summary = json.loads((output / 'summary.json').read_text())
    entry = summary['cases']['test']
    assert entry['status'] == 'pass' and entry['recomputed_from_retained_bases']
    assert entry['solves']['B']['measurement']['HF']['frequencies_hz'] == [100, 1000]
    assert entry['metrics']['combined']['absolute']['bands']['1k_5k']['phase_deg']['eligible']['max_abs'] == 0
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize('problem', ['missing-side', 'overlap', 'changed-mesh', 'changed-request'])
def test_recompute_refuses_incomplete_or_inconsistent_retained_evidence(tmp_path, monkeypatch, problem):
    source, output = tmp_path / 'saved', tmp_path / 'compared'
    _save_chunk(source / 'one' / 'test', 'A', [0, 1])
    if problem != 'missing-side':
        _save_chunk(source / 'one' / 'test', 'B', [0, 1])
    if problem in {'overlap', 'changed-mesh'}:
        extra = source / 'extra' / 'test'
        _save_chunk(extra, 'A', [0])
        if problem == 'changed-mesh':
            mesh = extra / 'A.msh'
            mesh.write_text(mesh.read_text() + '\n')
    if problem == 'changed-request':
        request = source / 'one' / 'test' / 'A.request.json'
        data = json.loads(request.read_text())
        data['options']['frequencies_hz'] = [200, 2000]
        request.write_text(json.dumps(data))
    monkeypatch.setattr(parity, 'charts', lambda *a, **kw: pytest.fail('invalid evidence reached charts'))
    assert parity.main(['--recompute', str(source), '--out', str(output)]) == 1
    assert not (output / 'summary.json').exists()


def test_charts_use_classic_theme_and_write_pngs(tmp_path, monkeypatch):
    import hornlab_plots as plots

    axes = []
    render = plots.render_single_heatmap

    def capture(ax, *args, **kwargs):
        axes.append(ax)
        return render(ax, *args, **kwargs)

    monkeypatch.setattr(plots, 'render_single_heatmap', capture)
    plan = {"variant": "A", "radiator_ids": {"HF": "HF"}, "polar": {"distance": 2, "angle_range": [0, 90, 3]},
            "drives": [{"radiator": "HF", "weight": 1, "delay_ms": 0}]}
    groups = parity.grouped_bases(_bases(), plan)
    files = parity.charts(tmp_path, groups, groups, parity.compare_groups(groups, groups), mesh_valid_hz=800)
    assert len(files) == 19  # HF + combined detail plots, plus one case summary.
    assert all((tmp_path / filename).read_bytes().startswith(b"\x89PNG") for filename in files)
    assert len(axes) == 12
    assert all(ax.patches and all(patch.get_zorder() == 4 and patch.get_alpha() == .65 for patch in ax.patches) for ax in axes)
    assert all(patch.get_facecolor()[:3] == (.5, .5, .5) for ax in axes for patch in ax.patches)


def test_new_fixture_imports_freeze_identities_for_independent_frequency_jobs():
    from server.design.schema import DesignConfig
    from server.design.textcfg import parse

    text = 'OSSE = {\nL = 120\n}\nMesh.Enclosure = {\nLFSource.B = {\nRadius = 50\nDrivingWeight = 1\n}\n}\n'
    left, right = [parse(text).design.model_dump(mode='json') for _ in range(2)]
    assert left['assembly']['woofers'][0]['id'] != right['assembly']['woofers'][0]['id']
    before = copy.deepcopy(left['assembly']['woofers'][0])
    parity.fixture_identities(left, 'test')
    parity.fixture_identities(right, 'test')
    assert left == right
    assert {k: v for k, v in left['assembly']['woofers'][0].items() if k != 'id'} == {k: v for k, v in before.items() if k != 'id'}
    assert DesignConfig.model_validate(left).root.assembly is not None


def test_dry_run_and_invalid_reference_never_start_the_solve_driver(tmp_path, monkeypatch):
    from server.platform.temp_session import temporary_directory_root

    def prepare(name, *args):
        assert str(tmp_path) in temporary_directory_root()
        return {"case": name, "status": "refused", "issues": ["invalid reference"]}, {}

    monkeypatch.setattr(parity, "prepare_case", prepare)
    monkeypatch.setattr(parity.assembly, "_solve", lambda *a, **kw: pytest.fail("unexpected solve"))
    for args in (["--dry-run"], []):
        assert parity.main(["--out", str(tmp_path), "--phase1", str(tmp_path), "--cases", "test", *args]) == 1
    assert json.loads((tmp_path / "summary.json").read_text())["status"] == "incomplete"


def test_result_hook_reuses_the_job_runner_and_receives_the_original_request(tmp_path, monkeypatch):
    from scripts import qualify_assembly as qual
    from server.jobs import runtime as module
    from scripts.tests.test_qualify_assembly import good_results

    class Runtime:
        def __init__(self, store, **kwargs):
            self.store = store

        async def start(self):
            pass

        async def shutdown(self):
            pass

        async def plan_imported(self, request):
            return {"engine": "beat-cpu"}

        async def submit(self, request):
            return "job"

        async def get_job(self, job_id):
            return {"status": "complete"}

        async def get_results(self, job_id):
            return good_results()

    request = SimpleNamespace(geometry=SimpleNamespace(drive_channels=[SimpleNamespace(id="horn-throat")], combine=None),
        options=SimpleNamespace(engine="beat-cpu", frequencies_hz=list(qual.FREQUENCIES_HZ),
            polar_config=SimpleNamespace(enabled_axes=["horizontal", "vertical"], angle_range=[-180, 180, 3])))

    async def hook(runtime, job_id, results, received):
        assert received is request and job_id == "job" and "channels" in results
        return {"captured": True}

    monkeypatch.setattr(module, "JobRuntime", Runtime)
    entries = {"requests": {"beat-cpu": {"request": request, "notice": None, "transducer_channels": []}}}
    solved = asyncio.run(qual._solve(tmp_path, {"cases": {"test": entries}}, ["beat-cpu"], 2, on_result=hook))
    assert solved["test/beat-cpu"]["measurement"] == {"captured": True}


def test_reference_import_maps_arbitrary_physical_ids_and_omits_only_virtual_interface(tmp_path):
    folder = tmp_path / "corpus" / "example"
    folder.mkdir(parents=True)
    (folder / "example.cfg").write_text("// unchanged input")
    (folder / "Project.abec").write_text(PROJECT)
    (folder / "solving.txt").write_text(SOLVING)
    (folder / "observation.txt").write_text(OBSERVATION)
    (folder / "bem_mesh.geo").write_text("// retained ATH geometry")
    from server.mesh.file_import import mesh_text

    mesh = FileMesh(np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]),
        np.array([[0, 1, 2], [0, 2, 3], [1, 3, 2]]), np.array([73, 82, 17]),
        {73: "throat-physical", 82: "bass-physical", 17: "I1-2"})
    (folder / "case.msh").write_text(mesh_text(mesh))
    measured = tmp_path / "ath-runs" / "example" / "measured.json"
    measured.parent.mkdir(parents=True)
    measured.write_text(json.dumps({"mesh_extents_mm": [[0, 0, 0], [1, 1, 1]], "woofers": []}))
    reference = parity.read_reference(tmp_path, "example")
    assert [drive["tag"] for drive in reference["drives"]] == [73, 82]
    assert reference["boundary"].tags.tolist() == [73, 82]
    assert reference["interface_triangles_omitted"] == 1
    assert all(len(digest) == 64 for digest in reference["provenance"].values())


def test_same_full_observation_grid_negotiates_assembly_velocity_normalisation():
    polar = parity.polar_options(parity.parse_observation(OBSERVATION)["arcs"])
    setup_a = parity._make_setup({"producer": "mesh-file"}, [], [100, 1000], polar)
    setup_b = parity._make_setup({"producer": "design-assembly"}, [], [100, 1000], polar)
    assert "required_features" not in setup_a["geometry"]
    assert setup_b["geometry"]["required_features"] == [parity.assembly.ASSEMBLY_RECORD_FEATURE]
    assert setup_a["options"] == setup_b["options"]
    assert setup_b["options"]["polar_config"]["angle_range"] == [-180, 180, 73]


def test_triangle_budget_refuses_before_import_or_solver_submission(tmp_path, monkeypatch):
    from server.design.schema import DesignConfig
    from server.jobs import models_api
    from server.mesh.file_import import mesh_text

    config = parity.assembly.cases()["horn-woofer"]
    config["assembly"]["woofers"][0]["ath"] = {"key": "B"}
    design = DesignConfig.model_validate(config)
    mesh = half_tetrahedron()
    mesh_file = tmp_path / "solve.msh"
    mesh_file.write_text(mesh_text(mesh))
    cfg = tmp_path / "test.cfg"
    cfg.write_text("dummy")
    ref = {"cfg": cfg, "provenance": {}, "drives": [], "arcs": [], "raw_triangles": 2,
           "boundary": mesh, "interface_triangles_omitted": 0, "boundary_integrity": {},
           "solving": {"domain_planes": ["x0"], "units": {"M1": "mm"}}, "measurement": {"woofers": []}}
    monkeypatch.setattr(parity, "read_reference", lambda *a: ref)
    monkeypatch.setattr("server.design.textcfg.parse", lambda *a: SimpleNamespace(design=design, migration_names=[]))
    monkeypatch.setattr(parity, "build_x_half", lambda *a: {
        "msh_text": mesh_text(mesh), "metadata": {"source_tags": {}}, "placement": {"woofers": []}})
    monkeypatch.setattr(parity, "geometry_parity", lambda *a: {})
    monkeypatch.setattr(models_api, "register_mesh", lambda *a, **kw: pytest.fail("budget must refuse before file import"))
    entry, plans = parity.prepare_case("test", tmp_path, tmp_path, None, [100], 1, 1)
    assert entry["status"] == "refused" and "Triangle budget" in entry["issues"][0]
    assert plans == {}


def test_ready_dry_run_never_starts_solver(tmp_path, monkeypatch):
    def prepare(*args):
        assert args[-1] is False  # Dry runs never request Wine regeneration.
        return {"case": "test", "status": "ready", "issues": []}, {}

    monkeypatch.setattr(parity, "prepare_case", prepare)
    monkeypatch.setattr(parity.assembly, "_solve", lambda *a, **kw: pytest.fail("dry run started a solve"))
    assert parity.main(["--out", str(tmp_path), "--phase1", str(tmp_path), "--cases", "test", "--dry-run"]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["status"] == "geometry-only" and summary["frequencies_hz"] == [200, 1000, 3000]


@pytest.mark.parametrize("case", ["../escape", "bad/name", ""])
def test_invalid_case_names_cannot_write_outside_output(tmp_path, case):
    with pytest.raises(SystemExit) as exc:
        parity.main(["--out", str(tmp_path), "--phase1", str(tmp_path), "--cases", case])
    assert exc.value.code == 2


def test_coarse_cfg_changes_sampling_only_and_does_not_duplicate_lf_resolution():
    cfg = '''OSSE = { L = 120 }
Mesh.Quadrants = 1234
Mesh.ThroatResolution = 6
Mesh.MouthResolution = 15
Mesh.Enclosure = {
FrontResolution = 20,20,20,20
BackResolution = 30,30,30,30
LFSource.A = {
Radius = 50
DrivingWeight = -0.5
Resolution = 10
}
LFSource.B = {
Radius = 65
Spacing = 35
}
}
'''
    modified = parity.coarse_cfg(cfg, 40)
    assert cfg.count('Resolution = 40') == 0
    assert modified.count('\nResolution = 40') == 2
    assert 'Mesh.Quadrants = 14' in modified
    for key in ('Mesh.ThroatResolution', 'Mesh.MouthResolution', 'Mesh.RearResolution',
                'Mesh.InterfaceResolution', 'FrontResolution', 'BackResolution'):
        assert f'{key} = 40' in modified
    for geometry in ('L = 120', 'Radius = 50', 'Radius = 65', 'Spacing = 35', 'DrivingWeight = -0.5'):
        assert geometry in modified


def test_existing_weld_closes_duplicate_seam_without_adding_faces_or_filling_a_hole():
    from server.mesh.file_import import file_mesh_integrity

    points = np.array([[0., 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10], [0, 0, 0]])
    faces = np.array([[0, 2, 1], [4, 1, 3], [1, 2, 3], [2, 4, 3]])
    original = FileMesh(points, faces, np.ones(4, int), {})
    fixed, report = parity.weld_reference(original)
    assert report['max_corner_displacement_mm'] == 0
    assert len(fixed.triangles) == 4 and len(fixed.points) == 4
    assert file_mesh_integrity(fixed, [])['open_edge_count'] == 0
    assert original.triangles.tolist() == faces.tolist()
    missing = FileMesh(points, faces[:-1], np.ones(3, int), {})
    still_open, _ = parity.weld_reference(missing)
    assert file_mesh_integrity(still_open, [])['open_edge_count'] == 3


@pytest.mark.parametrize('weight', [0., -.499992, 1.])
def test_harness_weights_override_omitted_product_default_and_zero_is_rigid(weight):
    from server.design.schema import DesignConfig

    dump = parity.assembly.cases()['horn-woofer']
    woofer = dump['assembly']['woofers'][0]
    woofer['ath'] = {'key': 'B'}
    original = copy.deepcopy(dump)
    effective = parity.match_ath_weights(dump, [
        {'radiator': 'HF', 'weight': 1., 'delay_ms': 0.},
        *([{'radiator': 'B', 'weight': weight, 'delay_ms': 0.}] if weight else []),
    ])
    validated = DesignConfig.model_validate(dump).root.assembly
    assert effective == {'HF': 1., 'B': weight}
    assert woofer['drive'] == ('rigid' if weight == 0 else 'prescribed')
    member = next(m for g in validated.signal_groups for m in g.members if m.radiator_id == woofer['id'])
    assert member.weight == abs(weight) and member.invert == (weight < 0)
    assert original['assembly']['woofers'][0].get('drive', 'prescribed') == 'prescribed'


def test_hausdorff_percentiles_and_defect_mask_report_where_the_max_is():
    a = FileMesh(np.array([[-1., 0, 0], [-2, 0, 0], [-1, 1, 0]]), np.array([[0, 1, 2]]), np.array([7]), {7: 'LF cone'})
    b = copy.deepcopy(a)
    b.points[:, 2] = 3
    result = parity.surface_hausdorff(a, b, [{'aperture_center_mm': [0, 0, 0], 'radius_mm': 5.}])
    assert result['sampled_p95_mm'] == pytest.approx(3)
    assert result['sampled_p99_mm'] == pytest.approx(3)
    assert result['sampled_rms_mm'] == pytest.approx(3)
    assert result['max_surface'] == 'LF cone' and len(result['max_location_mm']) == 3
    assert result['a_to_b']['excluded_samples'] == 4
    assert result['max_excluding_defect_region_mm'] == pytest.approx(3)


def test_calibration_mode_runs_exactly_one_b_base_b_frequency(tmp_path, monkeypatch):
    request = SimpleNamespace(geometry=SimpleNamespace(drive_channels=[SimpleNamespace(id='HF')]))
    plan = {'requests': {'beat-cpu': {'request': request}}}

    def prepare(name, phase1, data_dir, store, freqs, *args):
        assert name == 'b-base' and freqs == [1000.]
        return {'case': name, 'status': 'refused', 'issues': ['A unavailable'], 'wg_triangles': 5000}, {'B': plan}

    async def solve(data_dir, report, engines, timeout):
        assert report['cases'] == {'b-base/B': plan} and timeout <= 115
        return {'b-base/B/beat-cpu': {'status': 'complete', 'findings': [], 'wall_seconds': 10.}}

    monkeypatch.setattr(parity, 'prepare_case', prepare)
    monkeypatch.setattr(parity.assembly, '_solve', solve)
    assert parity.main(['--calibrate-only', '--out', str(tmp_path), '--phase1', str(tmp_path)]) == 0
    calibration = json.loads((tmp_path / 'calibration.json').read_text())
    assert json.loads((tmp_path / 'summary.json').read_text())['status'] == 'calibration-complete'
    assert calibration['channels'] == 1 and calibration['seconds_per_frequency_per_channel'] == 10
    entries = {'test': {'estimated_beat_cpu_time': {'A': parity._cpu_estimate(5000, 12, 2),
                                                 'B': parity._cpu_estimate(5000, 12, 2)}}}
    parity.calibrated_estimates(entries, calibration)
    assert entries['test']['estimated_pair_minutes'] == 8
    assert entries['test']['broker_budget']['under_15_minutes']
    assert 'N^1.5' in entries['test']['broker_budget']['assumption']


def test_b_lip_is_explicit_only_and_ath_arcs_are_unused():
    assert 'b-lip' not in parity.DEFAULT_CASES
    no_arcs = parity.parse_observation(OBSERVATION.replace('BasePlane=zx', 'BasePlane=xy'), read_arcs=False)
    assert no_arcs['arcs'] == [] and no_arcs['weights'][1002]['weight'] == -.5


def test_unavailable_calibration_cannot_be_reported_as_a_pass(tmp_path, monkeypatch):
    plan = {'requests': {'beat-cpu': {'request': SimpleNamespace()}}}
    monkeypatch.setattr(parity, 'prepare_case', lambda *a: (
        {'case': 'b-base', 'status': 'ready', 'issues': [], 'wg_triangles': 5000}, {'B': plan}))

    async def solve(*args):
        return {'b-base/B/beat-cpu': {'status': 'not-applicable', 'reason': 'BEAT CPU needs preparing.', 'findings': []}}

    monkeypatch.setattr(parity.assembly, '_solve', solve)
    assert parity.main(['--calibrate-only', '--out', str(tmp_path), '--phase1', str(tmp_path)]) == 1
    result = json.loads((tmp_path / 'summary.json').read_text())
    assert result['status'] == 'incomplete' and result['calibration'] is None
    assert not (tmp_path / 'calibration.json').exists()
    assert 'CPU calibration unavailable' in result['cases']['b-base']['issues'][0]


def half_tetrahedron():
    return FileMesh(np.array([[0., 0, 0], [0, 10, 0], [0, 0, 10], [10, 0, 0]]),
                    np.array([[0, 3, 1], [0, 2, 3], [1, 3, 2]]), np.array([7, 8, 9]),
                    {7: 'HF', 8: 'cone', 9: 'dust cap'})


def test_intact_positive_half_discards_broken_negative_half_and_closes_by_reflection():
    half = half_tetrahedron()
    full = parity._full_mesh(half, ['x0'])
    # Simulate ATH's absent negative-X LF cone/dust-cap join.
    full.triangles = full.triangles[:-1]
    full.tags = full.tags[:-1]
    original = copy.deepcopy(full)
    kept, report = parity.x_half_reference(full, .001)
    assert len(kept.triangles) == 3 and set(kept.tags) == {7, 8, 9}
    assert kept.points[:, 0].min() == 0 and kept.points[:, 0].max() == 10
    assert report['reflected_integrity']['open_edge_count'] == 0
    assert report['half_integrity']['off_plane_open_edge_count'] == 0
    assert report['half_integrity']['open_edge_count'] == 3
    assert np.array_equal(full.points, original.points) and np.array_equal(full.triangles, original.triangles)
    assert report['repair']['max_corner_displacement_mm'] == 0


def test_half_closure_refuses_a_missing_positive_diaphragm_facet():
    half = half_tetrahedron()
    half.triangles, half.tags = half.triangles[:-1], half.tags[:-1]
    with pytest.raises(parity.EvidenceRefusal, match='does not close'):
        parity.x_half_reference(half, .001)


def test_half_extraction_refuses_straddling_facets_instead_of_remeshing():
    half = half_tetrahedron()
    half.points[0, 0] = -1
    with pytest.raises(parity.EvidenceRefusal, match='crosses x=0'):
        parity.positive_x_half(half)
    half.points[:, 0] = -np.abs(half.points[:, 0])
    with pytest.raises(parity.EvidenceRefusal, match='empty'):
        parity.positive_x_half(half)


def test_half_extraction_omits_plane_caps_and_compacts_unused_negative_vertices():
    half = half_tetrahedron()
    full = parity._full_mesh(half, ['x0'])
    full.triangles = np.vstack([full.triangles, [0, 1, 2]])
    full.tags = np.append(full.tags, 11)
    selected = parity.positive_x_half(full)
    assert np.array_equal(selected.points, half.points)
    assert np.array_equal(selected.triangles, half.triangles)
    assert np.array_equal(selected.tags, half.tags)


def test_half_reference_normalises_repair_tolerance_to_mm_for_si_wg_mesh():
    half = half_tetrahedron()
    half.points *= .001
    selected, report = parity.x_half_reference(half, 1.)
    assert np.allclose(selected.points, half.points, atol=1e-14, rtol=0)
    assert report['reflected_integrity']['open_edge_count'] == 0


def test_both_requests_use_shared_persisted_x_half_symmetry():
    polar = parity.polar_options()
    for producer in ['mesh-file', 'design-assembly']:
        setup = parity._make_setup({'producer': producer, 'symmetry': {'domain_planes': ['x0']}}, [], [1000], polar)
        assert setup['options']['symmetry'] == 'half_yz'


def test_coordinator_commands_print_split_broker_jobs_then_recompute_without_preparing(tmp_path, monkeypatch, capsys):
    import shlex

    monkeypatch.setattr(parity, 'prepare_case', lambda *a: pytest.fail('printing commands prepared geometry'))
    assert parity.main(['--print-commands', '--out', str(tmp_path), '--phase1', str(tmp_path)]) == 0
    commands = [shlex.split(line) for line in capsys.readouterr().out.splitlines() if not line.startswith('#')]
    assert len(commands) == len(parity.DEFAULT_CASES) * 7
    assert 'a-explicit' in parity.DEFAULT_CASES and 'mtm-q14' in parity.DEFAULT_CASES
    for index, case in enumerate(parity.DEFAULT_CASES):
        jobs = commands[index * 7:index * 7 + 6]
        assert all(c[1] == 'submit' and c[c.index('--case') + 1] == case for c in jobs)
        assert [c[c.index('--side') + 1] for c in jobs] == ['A'] * 3 + ['B'] * 3
        assert [c[c.index('--freqs') + 1] for c in jobs] == ['200.0', '1000.0', '3000.0'] * 2
        assert '--recompute' in commands[index * 7 + 6]


@pytest.mark.parametrize('side', ['A', 'B'])
def test_selected_side_solves_only_that_side_and_does_not_claim_parity(tmp_path, monkeypatch, side):
    plans = {v: {'requests': {'beat-cpu': {'request': SimpleNamespace()}}, 'variant': v} for v in ['A', 'B']}

    def prepare(name, phase1, data_dir, store, freqs, maximum, revision, resolution, out, selected, regenerate):
        assert name == 'b-pair' and selected == side
        return {'case': name, 'status': 'ready', 'issues': [], 'estimated_beat_cpu_time': {
            side: parity._cpu_estimate(4000, len(freqs), 2)}}, {side: plans[side]}

    async def solve(data_dir, report, engines, timeout, **kwargs):
        assert report['cases'] == {f'b-pair/{side}': plans[side]}
        return {f'b-pair/{side}/beat-cpu': {'status': 'complete', 'findings': [], 'measurement': {}}}

    calibration = tmp_path / 'calibration.json'
    calibration.write_text(json.dumps({'symmetry': 'half_yz', 'resolution_mm': parity.DEFAULT_RESOLUTION_MM, 'triangles': 4000,
                                       'seconds_per_frequency_per_channel': 1.}))
    monkeypatch.setattr(parity, 'prepare_case', prepare)
    monkeypatch.setattr(parity.assembly, '_solve', solve)
    assert parity.main(['--case', 'b-pair', '--side', side, '--calibration', str(calibration),
                        '--out', str(tmp_path), '--phase1', str(tmp_path)]) == 0
    result = json.loads((tmp_path / 'summary.json').read_text())
    assert result['status'] == 'side-complete' and 'metrics' not in result['cases']['b-pair']


def test_native_half_build_reuses_assembly_geometry_and_guards_without_changing_the_design(tmp_path, monkeypatch):
    from server.mesh import builder

    dump = parity.assembly.cases()['horn-woofer']
    original = copy.deepcopy(dump)
    config = {'mesh': {'quadrants': 1234}, 'enclosure': {'woofers': [{'radius_mm': 50, 'resolution_mm': 40}]}}
    placement = {'woofers': [{'x_mm': 0}]}
    monkeypatch.setattr(builder, 'assembly_mesher_config', lambda d: (copy.deepcopy(config), placement))

    def build(design, cfg, cancel_cb, **kwargs):
        assert cfg == {**config, 'mesh': {'quadrants': 14}}
        assert kwargs == {'assembly': True, 'work_dir': str(tmp_path)}
        return {'guarded': True}

    monkeypatch.setattr(builder, '_build_design_artifact', build)
    assert parity._build_x_half_sync(dump, str(tmp_path)) == {'guarded': True, 'placement': placement}
    assert dump == original
    placement['woofers'][0]['x_mm'] = 1
    with pytest.raises(parity.EvidenceRefusal, match='off x=0'):
        parity._build_x_half_sync(dump, str(tmp_path))


def test_lf_coverage_finds_rigid_ath_cone_and_cap_and_refuses_an_incomplete_half():
    mesh = FileMesh(np.array([[10., 0, 0], [0, 10, 0], [3.5, 0, -4], [0, 0, -2], [0, 3.5, -4]]),
                    np.array([[0, 1, 2], [3, 2, 4]]), np.array([1, 1]), {1: 'rigid'})
    woofers = [{'source': 'B', 'aperture_center_mm': [0, 0, 0], 'radius_mm': 10}]
    result = parity.lf_half_coverage(mesh, woofers)['B']
    assert result['contains_cone'] and result['contains_dust_cap'] and result['triangles'] == 2
    mesh.triangles = mesh.triangles[:1]
    with pytest.raises(parity.EvidenceRefusal, match='cone or dust cap'):
        parity.lf_half_coverage(mesh, woofers)


def test_on_axis_panel_uses_theme_categories_and_distinct_solved_sample_traces():
    import hornlab_plots as plots
    import matplotlib.pyplot as plt

    plan = {"variant": "A", "radiator_ids": {"HF": "HF"}, "polar": {"distance": 2, "angle_range": [0, 90, 3]},
            "drives": [{"radiator": "HF", "weight": 1, "delay_ms": 0}]}
    group = parity.grouped_bases(_bases(), plan)["HF"]
    fig, ax = plt.subplots()
    try:
        parity._on_axis_panel(ax, group, group, "HF", None)
        a, b = [line for line in ax.lines if line.get_label().startswith(("A:", "B:"))]
        assert [line.get_color() for line in (a, b)] == list(plots.get_theme("classic").palette[:2])
        assert [line.get_linestyle() for line in (a, b)] == ["-", "--"]
        assert [line.get_marker() for line in (a, b)] == ["o", "s"]
        assert min(group["frequencies_hz"]) in ax.get_xticks()
        assert list(a.get_xdata()) == group["frequencies_hz"]
        assert list(b.get_xdata()) == group["frequencies_hz"]
        assert [text.get_text() for text in ax.get_legend().get_texts()] == ["A: ATH geometry", "B: WG geometry"]
    finally:
        plt.close(fig)


def test_summary_panels_keep_raw_signed_errors_and_shaded_tolerance(tmp_path, monkeypatch):
    import matplotlib.figure

    plan = {"variant": "A", "radiator_ids": {"HF": "HF"}, "polar": {"distance": 2, "angle_range": [0, 90, 3]},
            "drives": [{"radiator": "HF", "weight": 1, "delay_ms": 0}]}
    left = parity.grouped_bases(_bases(), plan)
    right = copy.deepcopy(left)
    for group in right.values():
        if group["status"] == "complete":
            group["pressure_real"] = (np.asarray(group["pressure_real"]) * 10 ** (-.2 / 20)).tolist()
            group["pressure_imag"] = (np.asarray(group["pressure_imag"]) * 10 ** (-.2 / 20)).tolist()
    comparison = parity.compare_groups(left, right)
    captured = []
    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", lambda fig, *a, **kw: captured.append(fig))
    assert parity.case_summary_figure(tmp_path, left, right, comparison, 800) == "summary.png"
    fig = captured[0]
    assert len(fig.axes) == 4
    assert not fig.axes[1].axison  # Rigid LF remains explicitly not applicable.
    error = fig.axes[3]
    assert np.allclose(error.lines[0].get_ydata(), -.2)
    assert error.collections  # Filled tolerance band, separate from mesh-limited span.
    vertices = error.collections[0].get_paths()[0].vertices
    assert set(vertices[:, 1]) == {-.5, .5}
    assert error.get_legend() is not None


def test_cross_case_table_reuses_eligible_band_maxima_and_omits_unsampled_bands(tmp_path):
    plan = {"variant": "A", "radiator_ids": {"HF": "HF"}, "polar": {"distance": 2, "angle_range": [0, 90, 3]},
            "drives": [{"radiator": "HF", "weight": 1, "delay_ms": 0}]}
    groups = parity.grouped_bases(_bases(), plan)
    cases = {"example": {"metrics": parity.compare_groups(groups, groups, mesh_valid_hz=800)}}
    rows = parity.summary_table_rows(cases)
    assert any(row[:3] == ["example", "HF", "<1k"] and row[3:] == ["0.0000"] * 3 + ["PASS"] for row in rows)
    assert any(row[:3] == ["example", "HF", "1k–5k"] and row[3:] == ["—"] * 3 + ["mesh-limited"] for row in rows)
    assert not any(row[2] == "10k–20k" for row in rows)
    assert (tmp_path / parity.cross_case_summary(tmp_path, cases)).read_bytes().startswith(b"\x89PNG")


def test_dense_commands_use_requested_cpu_python_and_study_budget(tmp_path, capsys):
    import shlex

    executable = tmp_path / "cpu-ready-python"
    assert parity.main(["--print-commands", "--case", "a-explicit", "--freqs", "300", "500", "700", "1500", "2200",
                        "--command-python", str(executable), "--out", str(tmp_path), "--phase1", str(tmp_path)]) == 0
    commands = [shlex.split(line) for line in capsys.readouterr().out.splitlines() if not line.startswith("#")]
    assert len(commands) == 11
    for command in commands[:-1]:
        assert command[command.index("--lane") + 1] == "compute"
        assert command[command.index("--priority") + 1] == "4"
        assert command[command.index("--expected") + 1] == "14"
        assert command[command.index("--requester") + 1] == "Assembly: diaphragms, L/C/R wiring, physics, ATH parity"
        assert command[command.index("--") + 1] == str(executable)
        assert command[command.index("--resolution-mm") + 1] == "11.0"
        assert command[command.index("--timeout") + 1] == "840"
    assert [c[c.index("--freqs") + 1] for c in commands[:-1]] == ["300.0", "500.0", "700.0", "1500.0", "2200.0"] * 2
    assert commands[-1][0] == str(executable)
    assert commands[-1][commands[-1].index("--recompute") + 1] == str(tmp_path / "a-explicit")


@pytest.mark.parametrize("change", ["inactive", "weight", "delay"])
def test_mismatched_excitation_is_incomparable_even_below_acoustic_tolerance(tmp_path, monkeypatch, change):
    plan = {"radiator_ids": {"HF": "HF", "B": "B"}, "polar": {"distance": 2, "angle_range": [0, 90, 3]},
            "drives": [{"radiator": "HF", "weight": 1., "delay_ms": 0.},
                       {"radiator": "B", "weight": .001, "delay_ms": 0.}]}
    other = copy.deepcopy(plan)
    other["drives"][1].update({"weight": 0. if change == "inactive" else .001001} if change != "delay" else {"delay_ms": .001})
    left, right = [parity.grouped_bases(_bases(), p) for p in (plan, other)]
    monkeypatch.setattr(parity, "charts", lambda *a, **kw: pytest.fail("incomparable reached charts"))
    entry = {"mesh_adequacy": {"A": {"max_valid_frequency_hz": 10000}}}
    parity.finish_comparison(entry, tmp_path, left, right)
    assert entry["status"] == "incomparable"
    assert entry["metrics"]["LF"]["status"] == "incomparable"
    assert entry["metrics"]["combined"]["status"] == "incomparable"


@pytest.mark.parametrize("field", ["frame", "distance", "angles", "sphere", "symmetry", "request-symmetry", "missing-record", "chunk-distance"])
def test_recompute_requires_matching_saved_observation_contracts(tmp_path, monkeypatch, field):
    source, output = tmp_path / "saved", tmp_path / "compared"
    folder = source / "test"
    for side in ("A", "B"):
        _save_chunk(folder, side, [0, 1])
    if field == "chunk-distance":
        folder = source / "extra" / "test"
        _save_chunk(folder, "B", [0])
        _save_chunk(folder, "A", [0])
    request_path, record_path = folder / "B.request.json", folder / "B.record.json"
    request, record = json.loads(request_path.read_text()), json.loads(record_path.read_text())
    if field == "frame":
        record["anchor"] = {"throat_frame": {"axis": [0, 0, 1], "u": [1, 0, 0], "v": [0, 1, 0], "origin_m": [0, .01, 0]}}
    elif field in {"distance", "chunk-distance"}:
        request["options"]["polar_config"]["distance"] = 3
    elif field == "angles":
        request["options"]["polar_config"]["angle_range"] = [-180, 180, 145]
    elif field == "sphere":
        request["options"]["polar_config"]["spherical_phi_count"] = 72
    elif field in {"symmetry", "request-symmetry"}:
        request["options"]["symmetry"] = "full"
        if field == "symmetry":
            record["symmetry"]["domain_planes"] = []
    request_path.write_text(json.dumps(request))
    record_path.write_text(json.dumps(record))
    if field == "missing-record":
        record_path.unlink()
    # Different chunks use disjoint frequencies, so only observation validation can reject them.
    if field == "chunk-distance":
        for side in ("A", "B"):
            _save_chunk(source / "test", side, [1])
    monkeypatch.setattr(parity, "charts", lambda *a, **kw: pytest.fail("incomparable reached charts"))
    assert parity.main(["--recompute", str(source), "--out", str(output)]) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["cases"]["test"]["status"] == "incomparable"
    assert summary["status"] != "pass"


def test_matching_inactive_groups_stay_not_driven():
    plan = {"radiator_ids": {"HF": "HF", "B": "B"}, "polar": {"distance": 2, "angle_range": [0, 90, 3]},
            "drives": [{"radiator": "HF", "weight": 1., "delay_ms": 0.},
                       {"radiator": "B", "weight": 0., "delay_ms": 0.}]}
    groups = parity.grouped_bases(_bases(), plan)
    comparison = parity.compare_groups(groups, groups)
    assert comparison["LF"] == {"status": "not-applicable", "reason": "LF not driven in ATH"}
    assert comparison["combined"]["absolute"]["pass"] is True


def test_recompute_refuses_saved_excitation_mismatch(tmp_path, monkeypatch):
    source, output = tmp_path / "saved", tmp_path / "compared"
    for side in ("A", "B"):
        _save_chunk(source, side, [0, 1])
    path = source / "B.plan.json"
    plan = json.loads(path.read_text())
    plan["drives"][1]["delay_ms"] = .00001
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(parity, "charts", lambda *a, **kw: pytest.fail("incomparable reached charts"))
    assert parity.main(["--recompute", str(source), "--out", str(output)]) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == summary["cases"]["test"]["status"] == "incomparable"
