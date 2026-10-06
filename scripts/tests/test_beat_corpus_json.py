"""Native corpus evidence regression coverage; no engines are launched."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any

import numpy as np
import pytest

from scripts.beat_conformance import corpus, json_io, run_corpus as runner


@dataclass
class CapturedConfig:
    progress_callback: Any
    julia_executable: Path
    solve_precision: str = "double"


@dataclass
class SolveResultLike:
    frequencies_hz: np.ndarray
    pressure_complex: np.ndarray
    config: Any
    timings: dict
    solver_log: list


class Progress:
    def __init__(self):
        self.lock = threading.Lock()  # Bound methods must not be deep-copied.

    def __call__(self, *args):
        pytest.fail("serialization invoked callback")

    def progress(self, *args):
        self(*args)


def sample(dtype=np.complex128):
    return SolveResultLike(
        np.array([500., 750., 1000.]),
        np.array([[[1+2j, 3-4j]], [[complex(np.nan, 1), complex(2, np.inf)]], [[0j, -1j]]], dtype=dtype),
        CapturedConfig(Progress().progress, Path("/external/julia")),
        {"solve": np.float32(1.5), "unavailable": np.float64(np.inf)},
        [{"count": np.int64(3), "complete": np.bool_(True), "step": Path("trace.npz"),
          "hook": Progress(), "samples": [np.float32(np.nan), np.float64(-np.inf), 1+2j, b"NPZ"]}],
    )


def assert_sample(decoded, expected):
    for part in ("real", "imag"):
        values = getattr(expected.pressure_complex, part)
        np.testing.assert_array_equal(getattr(decoded["pressure_complex"], part),
                                      values)
    assert decoded["pressure_complex"].dtype == expected.pressure_complex.dtype
    np.testing.assert_array_equal(decoded["frequencies_hz"], expected.frequencies_hz)
    assert "progress_callback" not in decoded["config"]
    assert decoded["config"]["__omitted_fields__"] == {"progress_callback": json_io.CALLABLE_REASON}
    assert decoded["config"]["julia_executable"] == "/external/julia"
    assert decoded["timings"] == {"solve": 1.5, "unavailable": np.inf}
    log = decoded["solver_log"][0]
    assert log["count"] == 3 and log["complete"] is True
    assert log["__omitted_fields__"]["hook"] == json_io.CALLABLE_REASON
    assert np.isnan(log["samples"][0])
    assert log["samples"][1:] == [-np.inf, 1+2j, b"NPZ"]


@pytest.mark.parametrize("dtype", [np.complex64, np.complex128])
def test_corpus_json_dataclass_callbacks_and_numpy_roundtrip(tmp_path, dtype):
    expected = sample(dtype)
    path = tmp_path / "raw.json"
    runner.write_json(path, expected)
    assert_sample(runner.read_json(path), expected)
    assert "NaN" not in path.read_text() and "Infinity" not in path.read_text()


@pytest.mark.parametrize("official", [False, True])
@pytest.mark.parametrize("imported", [False, True])
def test_corpus_engine_child_serializes_native_and_response(tmp_path, monkeypatch, official, imported):
    case = corpus.CASES["imported-two-sources" if imported else "osse-quarter"]
    record = {"sources": [{"id": "a"}, {"id": "b"}]} if imported else None
    frozen = corpus.FrozenCase(case, case.request(record=record).model_dump(mode="json"),
                              (corpus.ROOT / "server/solver/warmup_mesh.msh").read_bytes(), record, {})
    corpus.save_frozen(frozen, tmp_path)
    job, output = tmp_path / "job.json", tmp_path / ("official.json" if official else "hbb.json")
    runner.write_json(job, {"case": case.name, "frequencies_hz": [500., 750., 1000.]})
    expected = sample()
    def route(text, request, *args, **kwargs):
        assert kwargs["_official"] is official
        channels = [c.id for c in request.geometry.drive_channels] if imported else ["source"]
        for channel in channels:
            kwargs["_native_result_callback"](channel, expected)
        # Packaging is allowed to mutate a native result after the callback.
        expected.pressure_complex[0, 0, 0] = 99j
        return {"metadata": {"stats": sample(), "callback": Progress()}, "artifact": b"NPZ"}
    production = runner.production_run
    monkeypatch.setattr(runner, "production_run", lambda *a, **k: production(*a, **k, routes=(route, route)))
    name = "beat-engine" if official else "hornlab-beat-bem"
    identity = {"distributions": {name: {"revision": "a" * 40, "direct_url": {"vcs_info": {"commit_id": "a" * 40}}}}, "hbb_pin": "a" * 40, "wg_commit": {"returncode": 0, "stdout": "wg-test"},
                "wg_worktree": {"returncode": 0, "stdout": ""}}
    monkeypatch.setattr(runner, "capture_identity", lambda _: identity)
    monkeypatch.setattr(runner.signal, "signal", lambda *args: None)
    assert runner.engine_child(job, output, official=official, backend="cpu", precision="float64", julia="fake") == 0
    result = runner.read_json(output)
    assert "error" not in result and result["official"] is official
    for values in result["native"].values():
        assert_sample(values, sample())
    assert_sample(result["response"]["metadata"]["stats"], sample())
    assert result["response"]["metadata"]["__omitted_fields__"]["callback"] == json_io.CALLABLE_REASON
    assert result["response"]["artifact"] == b"NPZ"


def test_corpus_json_real_hbb_solve_result(tmp_path):
    result_module = pytest.importorskip("hornlab_beat_bem.result")
    from hornlab_beat_bem.config import SolveConfig
    native = sample()
    result = result_module.SolveResult(
        frequencies_hz=native.frequencies_hz, pressure_complex=native.pressure_complex,
        spl_db=np.zeros(native.pressure_complex.shape), impedance=np.array([1+2j, 2j, 3j]),
        observation_angles_deg=np.array([0., 30.]), observation_planes=["horizontal"],
        config=SolveConfig(progress_callback=Progress().progress, julia_executable="/external/julia"),
        mesh_info=result_module.MeshInfo(np.int64(4), 4, {np.int64(2): np.float64(0.01)}),
    )
    path = tmp_path / "hbb.json"
    runner.write_json(path, result)
    decoded = runner.read_json(path)
    assert decoded["config"]["__omitted_fields__"]["progress_callback"] == json_io.CALLABLE_REASON
    assert decoded["mesh_info"]["physical_tag_areas_m2"] == {"2": 0.01}
    assert decoded["pressure_complex"][0, 0, 0] == result.pressure_complex[0, 0, 0]


@pytest.mark.parametrize("value", [object(), {lambda: None: 1}])
def test_corpus_json_unknown_objects_fail_without_stringifying(tmp_path, value):
    path = tmp_path / "raw.json"
    with pytest.raises(TypeError, match=r"at \$.response"):
        runner.write_json(path, {"response": value})
    assert not path.exists()


def test_corpus_json_sequence_callbacks_keep_positions_and_array_shape(tmp_path):
    path = tmp_path / "raw.json"
    runner.write_json(path, {"log": [1, Progress(), 2], "matrix": np.array([[Progress(), 3]], dtype=object),
                             "scalar": np.array(1+2j), "empty": np.empty((0, 2), dtype=np.complex64)})
    decoded = runner.read_json(path)
    assert decoded["log"] == [1, {"__unavailable__": json_io.CALLABLE_REASON}, 2]
    assert decoded["matrix"].shape == (1, 2)
    assert decoded["matrix"][0, 0] == {"__unavailable__": json_io.CALLABLE_REASON}
    assert decoded["scalar"].shape == () and decoded["scalar"].item() == 1+2j
    assert decoded["empty"].shape == (0, 2)


def test_corpus_frozen_record_and_stats_use_shared_serializer(tmp_path):
    case = corpus.CASES["osse-quarter"]
    frozen = corpus.FrozenCase(case, case.request().model_dump(mode="json"), b"mesh", None,
                              {"count": np.int64(4), "scale": np.float32(0.5), "result": sample()})
    corpus.save_frozen(frozen, tmp_path)
    decoded = runner.read_json(tmp_path / "frozen.json")
    assert_sample(decoded["mesh_stats"]["result"], sample())
    assert runner.load_frozen(tmp_path, case).sha256 == frozen.sha256


def test_corpus_preflight_refuses_before_meshing_or_child(tmp_path, monkeypatch):
    from server.solver.beat_runtime import readiness
    monkeypatch.setattr(readiness, "backend_readiness", lambda *a, **k:
                        readiness.BackendReadiness(False, "stale", "depot differs"))
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: pytest.fail("child started"))
    case = corpus.CASES["osse-quarter"]
    monkeypatch.setattr(runner, "wg_identity", lambda: {})
    with pytest.raises(ValueError, match="Official BEAT cpu readiness is stale.*No corpus solve started"):
        runner.run_case(case, tmp_path / "run", backend="cpu", precision="float64", julia="fake", hbb_depot="/hbb/depot",
                        freezer=lambda *a, **k: pytest.fail("meshing started"))
    frozen = corpus.FrozenCase(case, {}, b"mesh", None, {})
    with pytest.raises(ValueError, match="depot differs"):
        runner.isolated_pair(frozen, (500.,), tmp_path / "coarse", backend="cpu", precision="float64", julia="fake", hbb_depot="/hbb/depot")
    assert not (tmp_path / "coarse").exists()


@pytest.mark.parametrize("official", [False, True])
def test_corpus_child_records_serialization_failure(tmp_path, monkeypatch, official):
    case = corpus.CASES["osse-quarter"]
    frozen = corpus.FrozenCase(case, case.request().model_dump(mode="json"), b"mesh", None, {})
    corpus.save_frozen(frozen, tmp_path)
    job, output = tmp_path / "job.json", tmp_path / "error.json"
    runner.write_json(job, {"case": case.name, "frequencies_hz": [500., 750., 1000.]})
    name = "beat-engine" if official else "hornlab-beat-bem"
    monkeypatch.setattr(runner, "capture_identity", lambda _: {
        "distributions": {name: {"revision": "a" * 40, "direct_url": {"vcs_info": {"commit_id": "a" * 40}}}}, "hbb_pin": "a" * 40, "wg_commit": {"returncode": 0, "stdout": "wg-test"},
                "wg_worktree": {"returncode": 0, "stdout": ""}})
    monkeypatch.setattr(runner.signal, "signal", lambda *args: None)
    monkeypatch.setattr(runner, "production_run", lambda *a, **k: {"response": {"opaque": object()}})
    assert runner.engine_child(job, output, official=official, backend="cpu", precision="float64", julia="fake") == 1
    error = runner.read_json(output)
    assert "Unsupported evidence type object at $.response.opaque" in error["error"]
    assert error["refusal"] is None


def test_corpus_cli_verdict_uses_strict_serializer(monkeypatch, tmp_path, capsys):
    import sys

    verdict = {"passed": False, "native": sample()}
    monkeypatch.setattr(runner, "run_case", lambda *a, **k: verdict)
    monkeypatch.setattr(sys, "argv", ["run_corpus", "--case", "osse-quarter", "--julia", "fake",
                                     "--output-dir", str(tmp_path), "--hbb-depot", "/hbb/depot"])
    assert runner.main() == 1
    import json
    decoded = json.loads(capsys.readouterr().out, object_hook=json_io._decode)
    assert_sample(decoded["native"], sample())


def test_corpus_json_official_sweep_result(tmp_path):
    from server.solver.beat_adapter.results import SweepResult

    native = sample()
    result = SweepResult(
        frequencies_hz=native.frequencies_hz, pressure_complex=native.pressure_complex,
        spl_db=np.zeros(native.pressure_complex.shape), impedance=np.array([1+2j, 2j, 3j]),
        observation_angles_deg=np.array([0., 30.]), observation_planes=["horizontal"],
        sphere_pressure_complex=native.pressure_complex[:, 0], sphere_theta_deg=np.array([0., 180.]),
        sphere_phi_deg=np.zeros(2), surface_pressure_complex=native.pressure_complex[:, 0],
        surface_neumann_complex=np.array([[1j], [2j], [3j]]), cancelled=False,
        requested_frequency_count=np.int64(3), solver_log=native.solver_log,
    )
    path = tmp_path / "official.json"
    runner.write_json(path, result)
    decoded = runner.read_json(path)
    assert decoded["requested_frequency_count"] == 3 and decoded["cancelled"] is False
    assert decoded["solver_log"][0]["__omitted_fields__"]["hook"] == json_io.CALLABLE_REASON
    for field in ("pressure_complex", "sphere_pressure_complex", "surface_pressure_complex", "surface_neumann_complex"):
        for part in ("real", "imag"):
            values = getattr(getattr(result, field), part)
            np.testing.assert_array_equal(getattr(decoded[field], part), values)


@pytest.mark.parametrize("key", ["__array__", "__complex__", "__bytes__", "__float__", "__mapping__"])
def test_corpus_json_reserved_mapping_keys_roundtrip_without_tag_collision(key, tmp_path):
    value = {key: "ordinary data", "nested": {key: ["ordinary", 3]}, "shape": [1], "data": [2]}
    runner.write_json(tmp_path / "reserved.json", value)
    assert runner.read_json(tmp_path / "reserved.json") == value


def test_corpus_json_preserves_all_nonfinite_signs_in_scalars_arrays_and_complex(tmp_path):
    values = np.array([np.inf, -np.inf, np.nan])
    value = {"scalars": list(values), "array": values,
             "complex": complex(-np.inf, np.inf)}
    runner.write_json(tmp_path / "nonfinite.json", value)
    decoded = runner.read_json(tmp_path / "nonfinite.json")
    np.testing.assert_array_equal(decoded["scalars"], values)
    np.testing.assert_array_equal(decoded["array"], values)
    assert decoded["complex"].real == -np.inf and decoded["complex"].imag == np.inf


@pytest.mark.parametrize("direct", [{"dir_info": {"editable": True}, "vcs_info": {"commit_id": "a" * 40}},
                                   {"dir_info": {}}, {}])
def test_corpus_official_child_refuses_unpinned_distribution_before_production(tmp_path, monkeypatch, direct):
    case = corpus.CASES["osse-quarter"]
    frozen = corpus.FrozenCase(case, case.request().model_dump(mode="json"), b"mesh", None, {})
    corpus.save_frozen(frozen, tmp_path)
    job, output = tmp_path / "job.json", tmp_path / "official.json"
    runner.write_json(job, {"case": case.name, "frequencies_hz": [500., 750., 1000.]})
    monkeypatch.setattr(runner, "capture_identity", lambda _: {
        "wg_commit": {"returncode": 0, "stdout": "wg-test"}, "wg_worktree": {"returncode": 0, "stdout": ""},
        "distributions": {"beat-engine": {"revision": "a" * 40, "direct_url": direct}}})
    monkeypatch.setattr(runner.signal, "signal", lambda *args: None)
    monkeypatch.setattr(runner, "production_run", lambda *a, **k: pytest.fail("production started"))
    assert runner.engine_child(job, output, official=True, backend="cpu", precision="float32", julia="forbidden") == 1
    assert "non-editable with an exact VCS commit" in runner.read_json(output)["error"]
