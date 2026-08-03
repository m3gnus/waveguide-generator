from __future__ import annotations

import json
import random
import struct
from pathlib import Path

import numpy as np
import pytest

from server.protocol.frame import FrameError, decode, encode

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "shared" / "frame-fixtures"
MANIFEST = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
GOOD = [item for item in MANIFEST["fixtures"] if item["outcome"] == "ok"]
MALFORMED = [item for item in MANIFEST["fixtures"] if item["outcome"] != "ok"]


def _frame_with_header_bytes(header_bytes: bytes, payload: bytes = b"") -> bytes:
    prefix = b"WGF1" + struct.pack("<I", len(header_bytes)) + header_bytes
    return prefix + bytes((-len(prefix)) % 8) + payload


def _rewrite_header(data: bytes, mutate) -> bytes:
    old_length = struct.unpack_from("<I", data, 4)[0]
    old_payload_base = (8 + old_length + 7) & ~7
    header = json.loads(data[8 : 8 + old_length])
    mutate(header)
    return _frame_with_header_bytes(
        json.dumps(header, separators=(",", ":")).encode(), data[old_payload_base:]
    )


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


@pytest.mark.parametrize(
    ("fixture_name", "mutate", "rule"),
    [
        ("good/curve.bin", lambda header: header["sections"][0].update(dtype=[]), "section-dtype"),
        ("good/minimal-preview.bin", lambda header: header["surfaces"][0].update(shading=[]), "surface-shading"),
        (
            "good/minimal-preview.bin",
            lambda header: header["surfaces"][0].update(normalMethod=[]),
            "surface-normal-method",
        ),
    ],
)
def test_unhashable_discriminators_are_classified_frame_errors(
    fixture_name: str, mutate, rule: str
) -> None:
    data = (FIXTURE_ROOT / fixture_name).read_bytes()
    with pytest.raises(FrameError) as captured:
        decode(_rewrite_header(data, mutate))
    assert captured.value.rule == rule


def test_deep_header_json_recursion_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    import server.protocol.frame as frame_module

    def recursive_loads(*_args, **_kwargs):
        raise RecursionError("maximum JSON depth exceeded")

    monkeypatch.setattr(frame_module.json, "loads", recursive_loads)
    raw = b'{"v":1,"sections":[]}'
    with pytest.raises(FrameError) as captured:
        decode(_frame_with_header_bytes(raw))
    assert captured.value.rule == "header-json"


def test_huge_fidelity_integer_is_invalid_instead_of_overflowing() -> None:
    data = (FIXTURE_ROOT / "good/minimal-preview.bin").read_bytes()

    def mutate(header: dict[str, object]) -> None:
        header["fidelity"]["maxChordErrorMmRequested"] = 10**1000  # type: ignore[index]

    with pytest.raises(FrameError) as captured:
        decode(_rewrite_header(data, mutate))
    assert captured.value.rule == "fidelity-invalid"


@pytest.mark.parametrize("shape", [[0] * 65, [0, (1 << 28) + 1]])
def test_zero_product_shapes_still_validate_rank_and_each_dimension(shape: list[int]) -> None:
    data = (FIXTURE_ROOT / "good/curve.bin").read_bytes()

    def mutate(header: dict[str, object]) -> None:
        header["sections"][0]["shape"] = shape  # type: ignore[index]

    with pytest.raises(FrameError) as captured:
        decode(_rewrite_header(data, mutate))
    assert captured.value.rule in {"section-shape", "section-elements"}


def test_integral_json_floats_use_the_shared_safe_integer_contract() -> None:
    data = (FIXTURE_ROOT / "good/curve.bin").read_bytes()

    def mutate(header: dict[str, object]) -> None:
        header["v"] = 1.0
        for section in header["sections"]:  # type: ignore[index]
            section["shape"] = [float(value) for value in section["shape"]]
            section["byteOffset"] = float(section["byteOffset"])
            section["byteLength"] = float(section["byteLength"])

    header, arrays = decode(_rewrite_header(data, mutate))
    assert header["v"] == 1
    assert isinstance(header["v"], int)
    for section in header["sections"]:
        assert arrays[section["name"]].shape == tuple(int(value) for value in section["shape"])
