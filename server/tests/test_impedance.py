import unittest

import numpy as np

from solver.impedance import calculate_throat_impedance


class _DummyGrid:
    def __init__(self, elements, volumes):
        self.elements = np.asarray(elements, dtype=np.int32)
        self.volumes = np.asarray(volumes, dtype=float)


class _DummyPressure:
    def __init__(self, center_values, coefficients=None):
        self._center_values = np.asarray(center_values, dtype=np.complex128)
        coeffs = [] if coefficients is None else coefficients
        self.coefficients = np.asarray(coeffs, dtype=np.complex128)

    def evaluate_on_element_centers(self):
        return self._center_values


class ImpedanceTest(unittest.TestCase):
    def test_uses_element_center_evaluation_when_available(self):
        grid = _DummyGrid(
            elements=[[0, 3], [1, 4], [2, 5]],
            volumes=[0.2, 0.3],
        )
        # Coefficients are intentionally too short for vertex index 5, matching
        # the real-world failure mode. Element-center evaluation should still work.
        pressure = _DummyPressure(
            center_values=[[2.0 + 1.0j, 4.0 + 2.0j]],
            coefficients=[1.0 + 0.0j, 1.0 + 0.0j],
        )
        throat_elements = np.array([1], dtype=np.int32)

        impedance = calculate_throat_impedance(grid, pressure, throat_elements)

        self.assertAlmostEqual(impedance.real, 12.0)
        self.assertAlmostEqual(impedance.imag, -6.0)

    def test_falls_back_to_vertex_coefficients_when_needed(self):
        grid = _DummyGrid(
            elements=[[0], [1], [2]],
            volumes=[0.5],
        )
        coeffs = np.array([1.0 + 1.0j, 3.0 + 1.0j, 5.0 + 1.0j], dtype=np.complex128)
        throat_elements = np.array([0], dtype=np.int32)

        impedance = calculate_throat_impedance(grid, coeffs, throat_elements)

        self.assertAlmostEqual(impedance.real, 15.0)
        self.assertAlmostEqual(impedance.imag, -5.0)

    def test_fallback_raises_clear_error_on_out_of_range_coefficients(self):
        grid = _DummyGrid(
            elements=[[0], [2], [3]],
            volumes=[0.5],
        )
        coeffs = np.array([1.0 + 0.0j, 1.0 + 0.0j, 1.0 + 0.0j], dtype=np.complex128)
        throat_elements = np.array([0], dtype=np.int32)

        with self.assertRaises(ValueError) as ctx:
            calculate_throat_impedance(grid, coeffs, throat_elements)
        self.assertIn("smaller than required vertex index", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
