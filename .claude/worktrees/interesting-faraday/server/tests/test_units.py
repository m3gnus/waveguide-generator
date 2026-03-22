import unittest

from solver.units import mm_to_m, m_to_mm


class UnitsTest(unittest.TestCase):
    def test_mm_to_m(self):
        self.assertEqual(mm_to_m(1000.0), 1.0)

    def test_m_to_mm(self):
        self.assertEqual(m_to_mm(2.5), 2500.0)


if __name__ == "__main__":
    unittest.main()
