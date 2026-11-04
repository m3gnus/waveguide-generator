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
    assert polar["angle_range"] == [0, 90, 3]


@pytest.mark.parametrize(("old", "new"), [("Acceleration", "Velocity"), ("BasePlane=zx", "BasePlane=xy"),
    ("Inclination=90", "Inclination=45"), ("Distance=2m", "Distance=2"), ("Weight=-0.25", "Weight=nan")])
def test_unsupported_observations_fail_with_remedy(old, new):
    with pytest.raises(parity.EvidenceRefusal):
        parity.parse_observation(OBSERVATION.replace(old, new))


def test_no_arcs_and_different_grids_are_not_a_default_or_a_pass():
    with pytest.raises(parity.EvidenceRefusal, match="no polar arcs"):
        parity.polar_options([])
    arcs = parity.parse_observation(OBSERVATION)["arcs"]
    arcs[1]["distance_m"] = 3
    with pytest.raises(parity.EvidenceRefusal, match="different grids"):
        parity.polar_options(arcs)


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
    return serialize_channel_bases(data)


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


def test_charts_use_classic_theme_and_write_pngs(tmp_path):
    plan = {"variant": "A", "radiator_ids": {"HF": "HF"}, "polar": {"distance": 2, "angle_range": [0, 90, 3]},
            "drives": [{"radiator": "HF", "weight": 1, "delay_ms": 0}]}
    groups = parity.grouped_bases(_bases(), plan)
    files = parity.charts(tmp_path, groups, groups, parity.compare_groups(groups, groups))
    assert len(files) == 18  # HF + combined: overlay, six heatmaps, phase + SPL differences.
    assert all((tmp_path / filename).read_bytes().startswith(b"\x89PNG") for filename in files)


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
    assert setup_b["options"]["polar_config"]["angle_range"] == [-180, 180, 9]


def test_triangle_budget_refuses_before_import_or_solver_submission(tmp_path, monkeypatch):
    from server.design.schema import DesignConfig
    from server.jobs import models_api
    from server.mesh.file_import import mesh_text

    design = DesignConfig.model_validate(parity.assembly.cases()["horn-woofer"])
    mesh = FileMesh(np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0]]), np.array([[0, 1, 2], [0, 2, 1]]), np.array([2, 1]), {})
    mesh_file = tmp_path / "solve.msh"
    mesh_file.write_text(mesh_text(mesh))
    cfg = tmp_path / "test.cfg"
    cfg.write_text("dummy")
    ref = {"cfg": cfg, "provenance": {}, "drives": [], "arcs": [], "raw_triangles": 2,
           "boundary": mesh, "interface_triangles_omitted": 0, "boundary_integrity": {}}
    monkeypatch.setattr(parity, "read_reference", lambda *a: ref)
    monkeypatch.setattr("server.design.textcfg.parse", lambda *a: SimpleNamespace(design=design, migration_names=[]))
    monkeypatch.setattr(parity.assembly, "compile_case", lambda *a, **kw: {"mesh_store_path": str(mesh_file), "excitations": []})
    monkeypatch.setattr(parity, "geometry_parity", lambda *a: {})
    monkeypatch.setattr(models_api, "register_mesh", lambda *a, **kw: pytest.fail("budget must refuse before file import"))
    entry, plans = parity.prepare_case("test", tmp_path, tmp_path, None, [100], 1, 1)
    assert entry["status"] == "refused" and "Triangle budget" in entry["issues"][0]
    assert plans == {}


def test_ready_dry_run_never_starts_solver(tmp_path, monkeypatch):
    monkeypatch.setattr(parity, "prepare_case", lambda *a, **kw: ({"case": "test", "status": "ready", "issues": []}, {}))
    monkeypatch.setattr(parity.assembly, "_solve", lambda *a, **kw: pytest.fail("dry run started a solve"))
    assert parity.main(["--out", str(tmp_path), "--phase1", str(tmp_path), "--cases", "test", "--dry-run"]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["status"] == "geometry-only" and len(summary["frequencies_hz"]) == 23
    assert summary["frequencies_hz"][-1] == 16000


@pytest.mark.parametrize("case", ["../escape", "bad/name", ""])
def test_invalid_case_names_cannot_write_outside_output(tmp_path, case):
    with pytest.raises(SystemExit) as exc:
        parity.main(["--out", str(tmp_path), "--phase1", str(tmp_path), "--cases", case])
    assert exc.value.code == 2
