"""Golden portability must not conceal a changed contract or numerical result."""
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest

from server.tests.beat_adapter.hbb_snapshot import (
    assert_float_snapshot, assert_production_snapshot, snapshot,
)


def test_npz_creator_os_does_not_change_contract_hash(monkeypatch):
    original = zipfile.ZipInfo.__init__

    def creator_info(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.create_system = creator_os

    monkeypatch.setattr(zipfile.ZipInfo, "__init__", creator_info)
    creator_os = 3
    buffer = io.BytesIO()
    np.savez_compressed(buffer, frequencies=np.array([100., 500., 1000.]))
    unix = buffer.getvalue()
    creator_os = 0
    buffer = io.BytesIO()
    np.savez_compressed(buffer, frequencies=np.array([100., 500., 1000.]))
    windows = buffer.getvalue()
    assert windows != unix
    assert snapshot(windows) == snapshot(unix)


@pytest.mark.parametrize("actual,expected", [
    (1.00000001, 1.), (1, 1.), (True, 1), ("changed", "original"),
    ([1., 2.], [1.]), ({"extra": 1}, {}), (1e-20, 0.),
])
def test_float_tolerance_refuses_other_changes(actual, expected):
    with pytest.raises(AssertionError):
        assert_float_snapshot(actual, expected)


def test_float_tolerance_accepts_only_relative_roundoff():
    assert_float_snapshot({"frequency": np.nextafter(1883.0136762977816, np.inf).item()},
                          {"frequency": 1883.0136762977816})


def test_adaptive_artifact_checks_arrays_behind_the_hash():
    fixtures = Path(__file__).parent / "fixtures"
    reference = (fixtures / "hbb_production_imported_adaptive.npz").read_bytes()
    expected = json.loads((fixtures / "hbb_production.json").read_text())["imported_adaptive"]
    expected = {"_channel_bases_npz": expected["_channel_bases_npz"]}
    with np.load(io.BytesIO(reference)) as archive:
        arrays = {name: archive[name] for name in archive.files}
    arrays["frequencies_hz"] = np.nextafter(arrays["frequencies_hz"], np.inf)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    assert_production_snapshot({"_channel_bases_npz": buffer.getvalue()}, expected,
                               "imported_adaptive", fixtures)
    arrays["pressure::left"][0, 0, 0] *= 1.01
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    with pytest.raises(AssertionError):
        assert_production_snapshot({"_channel_bases_npz": buffer.getvalue()}, expected,
                                   "imported_adaptive", fixtures)
