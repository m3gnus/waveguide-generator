from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pytest

from server.protocol.frame import FrameError, decode, encode

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "shared" / "frame-fixtures"
MANIFEST = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
GOOD = [item for item in MANIFEST["fixtures"] if item["outcome"] == "ok"]
MALFORMED = [item for item in MANIFEST["fixtures"] if item["outcome"] != "ok"]


@pytest.mark.parametrize("fixture", GOOD, ids=lambda item: Path(item["file"]).stem)
def test_good_fixture_round_trip(fixture: dict[str, object]) -> None:
    data = (FIXTURE_ROOT / str(fixture["file"])).read_bytes()
    header, arrays = decode(data)
    assert header["kind"] == fixture["kind"]
    assert {name: list(array.shape) for name, array in arrays.items()} == fixture["sectionShapes"]
    backing = np.frombuffer(data, dtype=np.uint8)
    assert all(np.shares_memory(array, backing) for array in arrays.values())
    for check in fixture["spotChecks"]:
        actual = arrays[check["section"]].reshape(-1)[check["index"]]
        assert float(actual) == pytest.approx(check["value"])
    if header["kind"] == "preview":
        for surface in header["surfaces"]:
            indices = arrays[surface["indices"]]
            positions = arrays[surface["positions"]]
            assert indices.size == 0 or int(indices.max()) < positions.shape[0]
    if fixture["byteExactReencode"]:
        header_fields = {key: value for key, value in header.items() if key != "sections"}
        assert encode(header_fields, arrays) == data


@pytest.mark.parametrize("fixture", MALFORMED, ids=lambda item: Path(item["file"]).stem)
def test_malformed_fixture_rule(fixture: dict[str, object]) -> None:
    data = (FIXTURE_ROOT / str(fixture["file"])).read_bytes()
    with pytest.raises(FrameError) as captured:
        decode(data)
    assert captured.value.rule == fixture["outcome"]


@pytest.mark.parametrize("fixture", GOOD, ids=lambda item: f"truncate-{Path(item['file']).stem}")
def test_random_truncations_are_clean_errors(fixture: dict[str, object]) -> None:
    data = (FIXTURE_ROOT / str(fixture["file"])).read_bytes()
    rng = random.Random(str(fixture["file"]))
    for _ in range(64):
        cut = rng.randrange(len(data))
        with pytest.raises(FrameError):
            decode(data[:cut])


@pytest.mark.parametrize("fixture", GOOD, ids=lambda item: f"flip-{Path(item['file']).stem}")
def test_random_bit_flips_never_crash(fixture: dict[str, object]) -> None:
    original = (FIXTURE_ROOT / str(fixture["file"])).read_bytes()
    rng = random.Random("flip-" + str(fixture["file"]))
    for _ in range(128):
        changed = bytearray(original)
        offset = rng.randrange(len(changed))
        changed[offset] ^= 1 << rng.randrange(8)
        try:
            decode(changed)
        except FrameError:
            pass


def test_encoder_rejects_nonfinite_position_and_normal_arrays() -> None:
    with pytest.raises(FrameError, match="positions-nonfinite"):
        encode({"kind": "curve"}, {"positions": np.array([[np.nan, 0, 0]], dtype=np.float32)})
    with pytest.raises(FrameError, match="normals-nonfinite"):
        encode({"kind": "curve"}, {"normals": np.array([[0, np.inf, 0]], dtype=np.float32)})
