"""Portable JSON serialization for official conformance artifacts."""

from dataclasses import dataclass
import threading
from typing import Any
from pathlib import Path, PurePosixPath, PureWindowsPath
import numpy as np
import pytest

from scripts.beat_conformance import corpus, json_io

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
    assert decoded["config"]["julia_executable"] == expected.config.julia_executable.as_posix()
    assert decoded["timings"] == {"solve": 1.5, "unavailable": np.inf}
    log = decoded["solver_log"][0]
    assert log["step"] == expected.solver_log[0]["step"].as_posix()
    assert log["count"] == 3 and log["complete"] is True
    assert log["__omitted_fields__"]["hook"] == json_io.CALLABLE_REASON
    assert np.isnan(log["samples"][0])
    assert log["samples"][1:] == [-np.inf, 1+2j, b"NPZ"]


@pytest.mark.parametrize("dtype", [np.complex64, np.complex128])
def test_corpus_json_dataclass_callbacks_and_numpy_roundtrip(tmp_path, dtype):
    expected = sample(dtype)
    path = tmp_path / "raw.json"
    json_io.write_json(path, expected)
    assert_sample(json_io.read_json(path), expected)
    assert "NaN" not in path.read_text() and "Infinity" not in path.read_text()


@pytest.mark.parametrize("path", [
    PureWindowsPath(r"D:\runtime\julia.exe"),
    PureWindowsPath(r"D:runtime\julia.exe"),
    PureWindowsPath(r"\external\julia"),
    PureWindowsPath(r"runtime\julia.exe"),
    PureWindowsPath(r"\\server\share\runtime\julia.exe"),
    PureWindowsPath(r"\\?\D:\runtime\julia.exe"),
    PureWindowsPath(r"\\?\UNC\server\share\julia.exe"),
])
def test_corpus_json_windows_paths_roundtrip_without_changing_identity(path, tmp_path):
    output = tmp_path / "paths.json"
    json_io.write_json(output, {"executable": path, "by_path": {path: "evidence"}})
    decoded = json_io.read_json(output)
    assert decoded["executable"] == path.as_posix()
    assert decoded["by_path"] == {path.as_posix(): "evidence"}
    assert PureWindowsPath(decoded["executable"]) == path
    assert PureWindowsPath(decoded["executable"]).parts == path.parts
    assert "\\" not in decoded["executable"]


def test_corpus_json_path_conversion_preserves_posix_backslashes_and_strings(tmp_path):
    path = PurePosixPath(r"/external/literal\filename")
    value = {"path": path, "string": r"D:\runtime\julia.exe"}
    json_io.write_json(tmp_path / "paths.json", value)
    assert json_io.read_json(tmp_path / "paths.json") == {"path": path.as_posix(), "string": value["string"]}


def test_corpus_json_normalized_path_key_collision_is_refused():
    path = PureWindowsPath(r"D:\runtime\julia.exe")
    with pytest.raises(ValueError, match="Colliding JSON mapping key"):
        json_io.dumps({path: 1, path.as_posix(): 2})


@pytest.mark.parametrize("value", [object(), {lambda: None: 1}])
def test_corpus_json_unknown_objects_fail_without_stringifying(tmp_path, value):
    path = tmp_path / "raw.json"
    with pytest.raises(TypeError, match=r"at \$.response"):
        json_io.write_json(path, {"response": value})
    assert not path.exists()


def test_corpus_json_sequence_callbacks_keep_positions_and_array_shape(tmp_path):
    path = tmp_path / "raw.json"
    json_io.write_json(path, {"log": [1, Progress(), 2], "matrix": np.array([[Progress(), 3]], dtype=object),
                             "scalar": np.array(1+2j), "empty": np.empty((0, 2), dtype=np.complex64)})
    decoded = json_io.read_json(path)
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
    decoded = json_io.read_json(tmp_path / "frozen.json")
    assert_sample(decoded["mesh_stats"]["result"], sample())
    assert corpus.load_frozen(tmp_path, case).sha256 == frozen.sha256




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
    json_io.write_json(path, result)
    decoded = json_io.read_json(path)
    assert decoded["requested_frequency_count"] == 3 and decoded["cancelled"] is False
    assert decoded["solver_log"][0]["__omitted_fields__"]["hook"] == json_io.CALLABLE_REASON
    for field in ("pressure_complex", "sphere_pressure_complex", "surface_pressure_complex", "surface_neumann_complex"):
        for part in ("real", "imag"):
            values = getattr(getattr(result, field), part)
            np.testing.assert_array_equal(getattr(decoded[field], part), values)


@pytest.mark.parametrize("key", ["__array__", "__complex__", "__bytes__", "__float__", "__mapping__"])
def test_corpus_json_reserved_mapping_keys_roundtrip_without_tag_collision(key, tmp_path):
    value = {key: "ordinary data", "nested": {key: ["ordinary", 3]}, "shape": [1], "data": [2]}
    json_io.write_json(tmp_path / "reserved.json", value)
    assert json_io.read_json(tmp_path / "reserved.json") == value


def test_corpus_json_preserves_all_nonfinite_signs_in_scalars_arrays_and_complex(tmp_path):
    values = np.array([np.inf, -np.inf, np.nan])
    value = {"scalars": list(values), "array": values,
             "complex": complex(-np.inf, np.inf)}
    json_io.write_json(tmp_path / "nonfinite.json", value)
    decoded = json_io.read_json(tmp_path / "nonfinite.json")
    np.testing.assert_array_equal(decoded["scalars"], values)
    np.testing.assert_array_equal(decoded["array"], values)
    assert decoded["complex"].real == -np.inf and decoded["complex"].imag == np.inf
