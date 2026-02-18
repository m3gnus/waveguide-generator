import unittest
import importlib.util


MATPLOTLIB_AVAILABLE = importlib.util.find_spec("matplotlib") is not None


def _pattern(points):
    return [[float(a), float(db)] for a, db in points]


@unittest.skipUnless(MATPLOTLIB_AVAILABLE, "matplotlib is not installed")
class DirectivityPlotTest(unittest.TestCase):
    def _frequencies(self):
        return [100.0, 1000.0]

    def _render(self, directivity):
        from solver.directivity_plot import render_directivity_plot
        return render_directivity_plot(self._frequencies(), directivity)

    def _vertical_only(self):
        return {
            "horizontal": [],
            "vertical": [
                _pattern([(0, 0), (90, -6), (180, -12)]),
                _pattern([(0, 0), (90, -8), (180, -15)]),
            ],
            "diagonal": [],
        }

    def _diagonal_only(self):
        return {
            "horizontal": [],
            "vertical": [],
            "diagonal": [
                _pattern([(0, 0), (90, -7), (180, -14)]),
                _pattern([(0, 0), (90, -9), (180, -18)]),
            ],
        }

    def _mixed(self):
        return {
            "horizontal": [
                _pattern([(0, 0), (90, -5), (180, -11)]),
                _pattern([(0, 0), (90, -7), (180, -14)]),
            ],
            "vertical": [
                _pattern([(0, 0), (90, -6), (180, -13)]),
                _pattern([(0, 0), (90, -8), (180, -16)]),
            ],
            "diagonal": [
                _pattern([(0, 0), (90, -7), (180, -15)]),
                _pattern([(0, 0), (90, -9), (180, -17)]),
            ],
        }

    def test_renders_vertical_only(self):
        image = self._render(self._vertical_only())
        self.assertIsInstance(image, str)
        self.assertGreater(len(image), 100)

    def test_renders_diagonal_only(self):
        image = self._render(self._diagonal_only())
        self.assertIsInstance(image, str)
        self.assertGreater(len(image), 100)

    def test_renders_mixed_planes(self):
        image = self._render(self._mixed())
        self.assertIsInstance(image, str)
        self.assertGreater(len(image), 100)


if __name__ == "__main__":
    unittest.main()
