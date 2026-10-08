"""Portable comparison and replay of the pre-switch HBB production contract."""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import zipfile

import numpy as np


def snapshot(value):
    if isinstance(value, bytes):
        # NPZ's ZIP creator OS is incidental packaging, not response data.
        # Reproduce NumPy's stream format with the original Unix creator field.
        if zipfile.is_zipfile(io.BytesIO(value)):
            buffer = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(value)) as source, zipfile.ZipFile(buffer, "w") as target:
                for info in source.infolist():
                    payload = source.read(info)
                    info.create_system = 3
                    with target.open(info, "w", force_zip64=True) as stream:
                        stream.write(payload)
            value = buffer.getvalue()
        return {"sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, dict):
        return {key: snapshot(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [snapshot(item) for item in value]
    return value


def assert_float_snapshot(actual, expected, path="response"):
    """Only float roundoff is tolerated: relative 1e-12, zero absolute slack."""
    assert type(actual) is type(expected), path
    if isinstance(expected, float):
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=0), path
    elif isinstance(expected, dict):
        assert actual.keys() == expected.keys(), path
        for key in expected:
            assert_float_snapshot(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert len(actual) == len(expected), path
        for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
            assert_float_snapshot(left, right, f"{path}[{index}]")
    else:
        assert actual == expected, path


def assert_production_snapshot(result, expected, key, fixture_dir):
    actual = snapshot(result)
    if key == "imported_adaptive":
        # A raw hash cannot tolerate last-ulp numerical differences inside NPZ.
        # Bind the reference bytes to the JSON hash, then compare every array.
        reference = (fixture_dir / "hbb_production_imported_adaptive.npz").read_bytes()
        assert snapshot(reference) == expected["_channel_bases_npz"]
        with np.load(io.BytesIO(result["_channel_bases_npz"]), allow_pickle=False) as left, np.load(
            io.BytesIO(reference), allow_pickle=False
        ) as right:
            assert left.files == right.files
            for name in right.files:
                a, b = left[name], right[name]
                assert a.dtype == b.dtype and a.shape == b.shape, name
                if a.dtype.kind in "fc":
                    np.testing.assert_allclose(a, b, rtol=1e-12, atol=0, err_msg=name)
                elif name.startswith("metadata::"):
                    assert_float_snapshot(json.loads(str(a)), json.loads(str(b)), name)
                else:
                    np.testing.assert_array_equal(a, b, err_msg=name)
        actual["_channel_bases_npz"] = expected["_channel_bases_npz"]
    if key.endswith("_adaptive"):
        assert_float_snapshot(actual, expected)
    else:
        assert actual == expected


def freeze(output: Path):
    """Run ONLY in a pre-switch 0a1b901d worktree with the tie-break patch."""
    import importlib
    import tempfile
    from types import SimpleNamespace

    import pytest
    from server.platform import temp_session
    from server.solver import beat, beat_imported
    from server.solver.context import SolverContext
    from server.tests import test_imported_beat as cad

    frozen = {}
    with tempfile.TemporaryDirectory() as work, pytest.MonkeyPatch.context() as patch:
        patch.setenv("WG2_BEAT_PROVIDER", "hbb")
        patch.setattr(beat.time, "time", lambda: 1700000000.)
        patch.setattr(temp_session, "_active_root", work)
        original = importlib.import_module

        def imports(name, *args, **kwargs):
            if name == "hornlab_beat_bem._constants":
                return SimpleNamespace(SPEED_OF_SOUND=343.)
            return original(name, *args, **kwargs)

        patch.setattr(importlib, "import_module", imports)
        statuses = {"cpu": dict(available=True, backend="cpu", surface_traces=False, reason="HBB")}
        mesh = Path(beat.__file__).parent / "warmup_mesh.msh"
        for adaptive in (False, True):
            for imported in (False, True):
                package = cad._RecordingBeat()
                patch.setattr(beat, "_load_api", lambda: package)
                patch.setattr(beat_imported, "_load_api", lambda: package)
                patch.setattr(beat, "beat_backend_statuses", lambda: statuses)
                patch.setattr(beat_imported, "beat_backend_statuses", lambda: statuses)
                request = cad._request()
                request.options.adaptive_frequency_sampling = adaptive
                if adaptive:
                    request.options.frequency_range = [100., 1000.]
                    request.options.frequencies_hz = None
                    request.options.num_frequencies = 24
                result = (beat_imported.solve_imported_beat_from_msh_text(
                    cad.MESH, request, cad._record(), backend="cpu") if imported else
                    beat.solve_beat_from_msh_text(mesh.read_text(), SolverContext(
                        design=None, frequency_range=(500., 2000.),
                        num_frequencies=24 if adaptive else 3,
                        adaptive_frequency_sampling=adaptive), backend="cpu"))
                key = ("imported" if imported else "parametric") + ("_adaptive" if adaptive else "")
                frozen[key] = snapshot(result)
                if key == "imported_adaptive":
                    (output / "hbb_production_imported_adaptive.npz").write_bytes(result["_channel_bases_npz"])
    target = output / "hbb_production.json"
    original = json.loads(target.read_text())
    for key in ("parametric", "imported"):
        assert frozen[key] == original[key], f"non-adaptive case changed: {key}"
    original.update({key: value for key, value in frozen.items() if key.endswith("_adaptive")})
    target.write_text(json.dumps(original, separators=(",", ":")))


if __name__ == "__main__":
    import sys

    freeze(Path(sys.argv[1]))
