import asyncio
import base64
import unittest
from unittest.mock import patch

import api.routes_misc as routes_misc
from contracts import ChartsRenderRequest, DirectivityRenderRequest


class RenderRoutesTest(unittest.TestCase):
    def setUp(self):
        routes_misc._RENDER_CACHE.clear()

    def tearDown(self):
        routes_misc._RENDER_CACHE.clear()

    def test_render_charts_without_directivity_does_not_produce_heatmap(self):
        request = ChartsRenderRequest(frequencies=[100.0], spl=[90.0])

        def fake_render(payload):
            self.assertEqual(payload["directivity"], {})
            return {
                "frequency_response": "ZnJlcXVlbmN5LXBuZw==",
                "directivity_map": None,
            }

        with patch.object(routes_misc, "render_all_charts", side_effect=fake_render) as renderer:
            response = asyncio.run(routes_misc.render_charts(request))

        renderer.assert_called_once()
        self.assertEqual(
            response,
            {"charts": {"frequency_response": "data:image/png;base64,ZnJlcXVlbmN5LXBuZw=="}},
        )
        self.assertNotIn("directivity_map", response["charts"])

    def test_render_charts_identical_request_uses_cached_png(self):
        png_bytes = b"chart png bytes"
        encoded = base64.b64encode(png_bytes).decode("ascii")
        request = ChartsRenderRequest(
            frequencies=[100.0, 200.0],
            spl=[90.0, 91.0],
            theme="dark",
        )

        with patch.object(
            routes_misc,
            "render_all_charts",
            return_value={"frequency_response": encoded, "directivity_map": None},
        ) as renderer:
            first = asyncio.run(routes_misc.render_charts(request))
            second = asyncio.run(routes_misc.render_charts(request))

        renderer.assert_called_once()
        self.assertEqual(first, second)
        first_bytes = base64.b64decode(first["charts"]["frequency_response"].split(",", 1)[1])
        second_bytes = base64.b64decode(second["charts"]["frequency_response"].split(",", 1)[1])
        self.assertEqual(first_bytes, png_bytes)
        self.assertEqual(second_bytes, png_bytes)

    def test_render_directivity_identical_request_uses_cached_png(self):
        png_bytes = b"directivity png bytes"
        encoded = base64.b64encode(png_bytes).decode("ascii")
        request = DirectivityRenderRequest(
            frequencies=[100.0],
            directivity={"horizontal": [[[0.0, 90.0]]]},
            reference_level=-6.0,
            theme="dark",
        )

        with patch.object(
            routes_misc,
            "render_directivity_plot",
            return_value=encoded,
        ) as renderer:
            first = asyncio.run(routes_misc.render_directivity(request))
            second = asyncio.run(routes_misc.render_directivity(request))

        renderer.assert_called_once()
        self.assertEqual(first, second)
        first_bytes = base64.b64decode(first["image"].split(",", 1)[1])
        second_bytes = base64.b64decode(second["image"].split(",", 1)[1])
        self.assertEqual(first_bytes, png_bytes)
        self.assertEqual(second_bytes, png_bytes)

    def test_render_charts_cache_key_includes_optional_directivity(self):
        without_map = ChartsRenderRequest(frequencies=[100.0], spl=[90.0])
        with_map = ChartsRenderRequest(
            frequencies=[100.0],
            spl=[90.0],
            directivity={"horizontal": [[[0.0, 90.0]]]},
        )

        def fake_render(payload):
            return {
                "frequency_response": "ZnJlcXVlbmN5LXBuZw==",
                "directivity_map": "bWFwLXBuZw==" if payload["directivity"] else None,
            }

        with patch.object(routes_misc, "render_all_charts", side_effect=fake_render) as renderer:
            first = asyncio.run(routes_misc.render_charts(without_map))
            second = asyncio.run(routes_misc.render_charts(with_map))

        self.assertEqual(renderer.call_count, 2)
        self.assertNotIn("directivity_map", first["charts"])
        self.assertEqual(
            second["charts"]["directivity_map"],
            "data:image/png;base64,bWFwLXBuZw==",
        )


if __name__ == "__main__":
    unittest.main()
