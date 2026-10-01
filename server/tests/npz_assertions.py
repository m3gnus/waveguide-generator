"""Compare NPZ payloads independently of their ZIP packaging."""

import io

import numpy as np


def assert_npz_contents_equal(actual: bytes, expected: bytes) -> None:
    with (
        np.load(io.BytesIO(actual), allow_pickle=False) as actual_arrays,
        np.load(io.BytesIO(expected), allow_pickle=False) as expected_arrays,
    ):
        assert sorted(actual_arrays.files) == sorted(expected_arrays.files)
        # Metadata is stored as arrays too, so every member participates.
        for name in expected_arrays.files:
            actual_array, expected_array = actual_arrays[name], expected_arrays[name]
            assert actual_array.dtype == expected_array.dtype, name
            assert actual_array.shape == expected_array.shape, name
            np.testing.assert_array_equal(actual_array, expected_array, err_msg=name)
            assert actual_array.tobytes(order="C") == expected_array.tobytes(order="C"), name
