"""The design-assembly qualification harness must be able to fail.

``scripts/qualify_assembly.py`` compiles real designs with the design mesher
and composes each engine's request as the model Solve does. Its geometry
phase runs here; each verdict is also pinned with an input that must fail it,
so a harness that cannot see a moved point, an uncoupled driver, a silent
fallback or a missing channel is caught here rather than on a qualification
host.

The real solve run is opt-in (``WG2_QUALIFY_ASSEMBLY=1``): it needs BEAT and
Metal runtimes and takes minutes.
"""

from __future__ import annotations

import copy
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import qualify_assembly as qual
from server.contracts.assembly import HF_RADIATOR_ID
from server.design.schema import DesignConfig


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return qual.geometry_phase(tmp_path_factory.mktemp("qualify-assembly"))


def test_every_case_is_a_valid_design_with_the_cases_it_names():
    designs = qual.cases()
    assert list(designs) == ["horn-only", "horn-woofer", "two-woofers", "controls"]
    for dump in designs.values():
        DesignConfig.model_validate(dump)
    members = designs["two-woofers"]["assembly"]["signal_groups"][1]["members"]
    assert members[0] != members[1] and members[1]["invert"] is True and members[1]["delay_ms"] > 0
    shapes = [woofer["shape"]["kind"] for woofer in designs["controls"]["assembly"]["woofers"]]
    assert shapes == ["piston", "ath_cone"] and set(designs["controls"]["assembly"]["drivers"]) == {qual.W1, qual.W2}


def test_the_geometry_phase_passes_on_the_real_design_mesher(report):
    assert qual.geometry_verdicts(report) == []
    assert report["cases"]["horn-only"]["horn_only_identity"] == {"points": True, "connectivity": True, "source_mask": True}
    assert report["cases"]["horn-only"]["inventory"]["excitations"] == [HF_RADIATOR_ID]
    sources = report["cases"]["controls"]["inventory"]["sources"]
    assert {key.split("/")[-1] for key in sources} == {HF_RADIATOR_ID, "piston", "cap", "cone"}


def test_a_coupled_engine_sends_the_horn_beside_both_drivers_in_one_request(report):
    for engine in qual.COUPLED_ENGINES:
        summary = report["cases"]["controls"]["requests"][engine]
        plan = summary["coupled_plan"]
        assert summary["notice"] is None and plan["preflight"] is None
        assert plan["ideal_source_count"] == 1
        assert plan["ports"][0].endswith(HF_RADIATOR_ID)
        assert plan["driver_ports"] == [f"voltage:{qual.W1}", f"voltage:{qual.W2}"]
        assert plan["channel_ports"] == {HF_RADIATOR_ID: [0], qual.W1: [1], qual.W2: [2]}
    for engine in ("metal", "bempp"):
        summary = report["cases"]["controls"]["requests"][engine]
        assert summary["transducer_channels"] == [] and "prescribed velocity" in summary["notice"]


def test_member_processing_reaches_the_request(report):
    request = report["cases"]["two-woofers"]["requests"]["metal"]["request"]
    processing = {channel: item.model_dump() for group in request.geometry.driver_groups
                  for channel, item in (group.member_processing or {}).items()}
    assert processing[qual.W2] == {"weight": 0.8, "delay_ms": 0.25, "invert": True}
    assert processing[qual.W1] == {"weight": 1.0, "delay_ms": 0.0, "invert": False}


def _mesh(points, triangles, tags):
    return SimpleNamespace(points=np.asarray(points, float), triangles=np.asarray(triangles), tags=np.asarray(tags))


def test_the_identity_check_sees_a_moved_point_a_rewired_face_and_a_moved_source():
    base = _mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], [[0, 1, 2], [1, 3, 2]], [2, 1])
    assert qual.compare_meshes(base, base, compiled_throat_tag=2) == {"points": True, "connectivity": True, "source_mask": True}
    moved = _mesh(base.points + [[0, 0, 1e-12], [0, 0, 0], [0, 0, 0], [0, 0, 0]], base.triangles, base.tags)
    assert qual.compare_meshes(moved, base, compiled_throat_tag=2)["points"] is False
    rewired = _mesh(base.points, [[0, 2, 1], [1, 3, 2]], base.tags)
    assert qual.compare_meshes(rewired, base, compiled_throat_tag=2)["connectivity"] is False
    retagged = _mesh(base.points, base.triangles, [7, 7])
    assert qual.compare_meshes(retagged, base, compiled_throat_tag=7)["source_mask"] is False
    assert qual.compare_meshes(retagged, base, compiled_throat_tag=2)["source_mask"] is False


@pytest.mark.parametrize(("change", "expected"), [
    (lambda r: r["cases"]["horn-only"]["horn_only_identity"].update(points=False), "points differ"),
    (lambda r: r["cases"]["controls"]["requests"]["beat-cpu"].update(transducer_channels=[]), "are not all coupled"),
    (lambda r: r["cases"]["controls"]["requests"]["metal"].update(notice=None), "is not explained"),
    (lambda r: r["cases"]["controls"]["requests"]["metal"].update(transducer_channels=[qual.W1]), "was sent exterior drivers"),
    (lambda r: r["cases"]["controls"]["requests"]["beat-cpu"]["coupled_plan"].update(preflight="open solid"), "preflight refused"),
    (lambda r: r["cases"]["controls"]["requests"]["beat-cpu"]["coupled_plan"].update(ideal_source_count=0), "not a prescribed ideal source"),
    (lambda r: r["cases"]["horn-woofer"]["inventory"].update(excitations=[HF_RADIATOR_ID]), "are not the radiators"),
    (lambda r: r["cases"]["two-woofers"]["dump"]["assembly"]["signal_groups"][1]["members"][1].update(delay_ms=0.5), "member processing"),
])
def test_each_geometry_verdict_can_fail(report, change, expected):
    broken = {"cases": {name: {**entry, "inventory": copy.deepcopy(entry["inventory"]), "dump": copy.deepcopy(entry["dump"]),
                               **({"horn_only_identity": dict(entry["horn_only_identity"])} if "horn_only_identity" in entry else {}),
                               "requests": {engine: {**summary, **({"coupled_plan": dict(summary["coupled_plan"])} if "coupled_plan" in summary else {})}
                                            for engine, summary in entry["requests"].items()}}
                        for name, entry in report["cases"].items()}}
    change(broken)
    failures = qual.geometry_verdicts(broken)
    assert any(expected in failure for failure in failures), failures


def good_results():
    f = list(qual.FREQUENCIES_HZ)
    channel = {"frequencies": f, "spl_on_axis": {"frequencies": f, "spl": [80.0] * len(f), "phase_degrees": [0.0] * len(f)},
               "directivity": {plane: [[[-180.0, -30.0], [0.0, 0.0], [180.0, -30.0]]] * len(f) for plane in ("horizontal", "vertical")},
               "directivity_phase": {plane: [[[-180.0, 10.0], [0.0, 0.0], [180.0, -10.0]]] * len(f) for plane in ("horizontal", "vertical")},
               "impedance": {"frequencies": f, "real": [6.0] * len(f), "imaginary": [1.0] * len(f)},
               "metadata": {"impedance_units": "ohms", "impedance_quantity": "electrical_input_impedance"}}
    return {"channels": {name: copy.deepcopy(channel) for name in ("horn-throat", qual.W1, "combined")}}


def findings(results):
    return qual.result_findings(results, channel_ids=["horn-throat", qual.W1], driver_ids=[qual.W1], combined_id="combined", angle_count=3)


def test_the_result_check_accepts_complete_physical_outputs():
    assert findings(good_results()) == []


@pytest.mark.parametrize("change", [
    lambda c: c.update(spl_on_axis=None),
    lambda c: c["spl_on_axis"].update(spl=[]),
    lambda c: c["spl_on_axis"].update(spl=[None] * 4),
    lambda c: c["spl_on_axis"].update(spl=[80.0, float("nan"), 80.0, 80.0]),
    lambda c: c["spl_on_axis"].update(phase_degrees=[None] * 4),
    lambda c: c["spl_on_axis"].update(frequencies=[200.0, 500.0, 1000.0, 3000.0]),
    lambda c: c.update(frequencies=[200.0]),
    lambda c: c.update(directivity={}),
    lambda c: c["directivity"].update(horizontal=[[[0.0, 0.0]]]),
    lambda c: c["directivity_phase"].update(vertical=[[[0.0, None]]] * 4),
    lambda c: c.update(impedance=None),
    lambda c: c["impedance"].update(real=[]),
    lambda c: c["impedance"].update(imaginary=[None] * 4),
    lambda c: c["impedance"].update(real=[6.0, float("inf"), 6.0, 6.0]),
    lambda c: c["impedance"].update(frequencies=[200.0, 500.0, 1000.0, 3000.0]),
    lambda c: c["metadata"].update(impedance_quantity="specific_acoustic_impedance"),
    lambda c: c["metadata"].update(failure_count=1),
    lambda c: c["metadata"].update(failures=[{"stage": "postprocess", "code": "missing_output"}]),
    lambda c: c["metadata"].update(partial_success=True),
])
def test_empty_null_nonfinite_wrong_shape_missing_frequency_and_postprocessing_outputs_fail(change):
    results = good_results()
    change(results["channels"][qual.W1])
    assert findings(results)


def test_frequency_axes_and_metadata_are_never_acoustic_evidence():
    results = {"channels": {"horn-throat": {"frequencies": [200.0], "spl_onaxis": [None]},
                           qual.W1: {"frequencies": [200.0], "impedance": {"real": [None], "imag": [None]}}}}
    assert findings(results)
    missing = good_results()
    del missing["channels"]["combined"]
    assert "channel combined is missing from the results" in findings(missing)
    envelope = good_results()
    envelope["metadata"] = {"failure_count": 1}
    assert "the result envelope reports solve/postprocessing failures" in findings(envelope)


def test_case_planning_skips_unsupported_engines_and_retains_real_solve_failures(tmp_path, monkeypatch):
    from server.jobs import runtime as runtime_module

    calls = []

    class Runtime:
        def __init__(self, store, **kwargs):
            self.store = store

        async def start(self):
            calls.append("start")

        async def shutdown(self):
            calls.append("shutdown")

        async def plan_imported(self, request):
            engine = request.options.engine
            return {"engine": None if engine == "bempp" else engine,
                    "reason": "imported geometry is unsupported", "code": "geometry_unsupported"}

        async def submit(self, request):
            calls.append(request.options.engine)
            if request.options.engine == "metal":
                raise RuntimeError("real submission failure")
            return "job"

        async def get_job(self, job_id):
            return {"status": "complete"}

        async def get_results(self, job_id):
            return good_results()

    engines = ["beat-cpu", "metal", "bempp"]
    entries = {engine: {"notice": None, "transducer_channels": [], "request": SimpleNamespace(
        geometry=SimpleNamespace(drive_channels=[SimpleNamespace(id="horn-throat")], combine=None),
        options=SimpleNamespace(engine=engine, frequencies_hz=list(qual.FREQUENCIES_HZ),
            polar_config=SimpleNamespace(enabled_axes=["horizontal", "vertical"], angle_range=[-180, 180, 3])))} for engine in engines}
    monkeypatch.setattr(runtime_module, "JobRuntime", Runtime)
    solved = asyncio.run(qual._solve(tmp_path, {"cases": {"one": {"requests": entries}}}, engines, 2))
    assert solved["one/beat-cpu"]["status"] == "complete" and solved["one/beat-cpu"]["findings"] == []
    assert solved["one/bempp"]["status"] == "not-applicable"
    assert solved["one/bempp"]["reason"] == "imported geometry is unsupported"
    assert solved["one/metal"]["status"] == "failed"
    assert "real submission failure" in solved["one/metal"]["findings"][0]
    assert calls == ["start", "beat-cpu", "metal", "shutdown"]


@pytest.mark.parametrize("status", ["not-applicable", "unavailable", "failed"])
def test_required_engine_must_complete_every_case_and_program_owns_the_temporary_session(tmp_path, monkeypatch, status):
    from server.platform.temp_session import temporary_directory_root

    roots = []

    def inventory():
        roots.append(Path(temporary_directory_root()))
        assert roots[-1].is_dir()
        return {"beat-cpu": "available", "metal": "available"}

    async def solve(*args):
        return {"one/beat-cpu": {"status": "complete", "findings": []},
                "one/metal": {"status": status, "findings": []}}

    monkeypatch.setattr(qual, "geometry_phase", lambda path: {"cases": {"one": {"inventory": {}, "requests": {}}}})
    monkeypatch.setattr(qual, "geometry_verdicts", lambda report: [])
    monkeypatch.setattr(qual, "available_engines", inventory)
    monkeypatch.setattr(qual, "_solve", solve)
    output = tmp_path / "evidence.json"
    assert qual.main(["--solve", "--require", "beat-cpu", "--require", "metal", "--json", str(output)]) == 1
    evidence = json.loads(output.read_text())
    assert evidence["verdict"] == "fail" and any("one/metal" in failure for failure in evidence["failures"])
    assert evidence["solves"]["one/metal"]["status"] == status
    assert not roots[0].exists()


def test_the_public_evidence_has_no_local_paths(report):
    text = repr(qual.public_report(report, None, []))
    assert "mesh_store_path" not in text and str(Path(report["cases"]["horn-only"]["record"]["mesh_store_path"]).parent) not in text


def test_the_harness_runs_only_as_a_program():
    source = Path(qual.__file__).read_text(encoding="utf-8")
    assert source.rstrip().endswith('if __name__ == "__main__":\n    raise SystemExit(main())')


@pytest.mark.skipif(os.environ.get("WG2_QUALIFY_ASSEMBLY") != "1", reason="needs BEAT and Metal runtimes; opt in with WG2_QUALIFY_ASSEMBLY=1")
def test_the_real_solve_qualification(tmp_path):
    assert qual.main(["--solve", "--require", "beat-cpu", "--require", "metal", "--json", str(tmp_path / "assembly.json")]) == 0
