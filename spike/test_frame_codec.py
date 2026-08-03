"""Plain-assert tests for frame_codec.py."""

from __future__ import annotations

import json
import struct

import numpy as np

from frame_codec import MAGIC, decode, encode, grid_to_mesh


def test_round_trip() -> None:
    source = {
        "positions": np.array([[1.25, -2.5, 3.75], [4.0, 5.0, 6.0]], dtype=np.float32),
        "indices": np.array([0, 1, 0], dtype=np.uint32),
    }
    frame = encode(42, 7.125, source)
    assert frame[:4] == MAGIC
    header_length = struct.unpack_from("<I", frame, 4)[0]
    wire_header = json.loads(frame[8 : 8 + header_length])
    assert wire_header["v"] == 0
    assert wire_header["seq"] == 42
    assert wire_header["evalMs"] == 7.125
    assert wire_header["sections"][0]["offset"] == 0
    assert wire_header["sections"][1]["offset"] == source["positions"].nbytes

    header, decoded = decode(frame)
    assert header == wire_header
    assert decoded["positions"].dtype == np.dtype("<f4")
    assert decoded["indices"].dtype == np.dtype("<u4")
    np.testing.assert_array_equal(decoded["positions"], source["positions"])
    np.testing.assert_array_equal(decoded["indices"], source["indices"])


def test_known_three_by_three_grid() -> None:
    points = np.array(
        [
            [[0, 0, 0], [0, 0, 1], [0, 0, 2]],
            [[1, 0, 0], [1, 0, 1], [1, 0, 2]],
            [[2, 0, 0], [2, 0, 1], [2, 0, 2]],
        ],
        dtype=np.float64,
    )
    viewport = {
        "grid": {
            "grid_n_phi": 3,
            "grid_n_length": 2,
            "full_circle": False,
            "inner_points": points.reshape(-1).tolist(),
            "outer_points": (points + np.array([0, 1, 0])).reshape(-1).tolist(),
        }
    }
    mesh = grid_to_mesh(viewport)
    expected = np.array(
        [
            0, 3, 1, 3, 4, 1,
            1, 4, 2, 4, 5, 2,
            3, 6, 4, 6, 7, 4,
            4, 7, 5, 7, 8, 5,
        ],
        dtype=np.uint32,
    )
    assert mesh["positions"].shape == (9, 3)
    assert mesh["positions"].dtype == np.float32
    np.testing.assert_array_equal(mesh["indices"], expected)
    np.testing.assert_array_equal(mesh["outerIndices"], expected)
    np.testing.assert_array_equal(
        mesh["outerPositions"], (points + np.array([0, 1, 0])).reshape(9, 3)
    )


if __name__ == "__main__":
    test_round_trip()
    test_known_three_by_three_grid()
    print("frame_codec: all assertions passed")
