import asyncio
import base64
import sys
import types
import unittest
from unittest.mock import Mock, call, patch

import api.routes_misc as routes_misc
from pydantic import ValidationError

from contracts import (
    ChartsReferencePayload,
    ChartsRenderRequest,
    DirectivityRenderRequest,
)
import services.solver_runtime as solver_runtime


class RenderReferenceContractsTest(unittest.TestCase):
    def test_reference_contracts_accept_pinned_shapes(self):
        reference_fields = {
            "label": "Baseline",
            "frequencies": [100.0, 200.0],
            "spl": [89.0, None],
            "di_frequencies": [100.0, 200.0],
            "impedance_frequencies": [100.0, 200.0],
            "impedance_real": [1.0, None],
            "impedance_imaginary": [0.1, -0.1],
            "impedance_units": "normalized",
            "impedance_normalization": "rho_c",
        }
        for di in ([2.0, None], {"horizontal": [2.0, 3.0]}):
            with self.subTest(di=di):
                request = ChartsRenderRequest(
                    frequencies=[100.0, 200.0],
                    reference={**reference_fields, "di": di},
                )

                self.assertIsInstance(request.reference, ChartsReferencePayload)
                dumped_reference = request.model_dump()["reference"]
                self.assertIsInstance(dumped_reference, dict)
                self.assertEqual(dumped_reference["di"], di)

        reference_directivity = {
            "horizontal": [
                [[0.0, 90.0], [30.0, 84.0]],
                [[0.0, 91.0], [30.0, 82.0]],
            ]
        }
        directivity_request = DirectivityRenderRequest(
            frequencies=[100.0, 200.0],
            directivity=reference_directivity,
            reference_frequencies=[125.0, 250.0],
            reference_directivity=reference_directivity,
            reference_label="Baseline",
        )
        self.assertEqual(directivity_request.reference_frequencies, [125.0, 250.0])
        self.assertEqual(directivity_request.reference_directivity, reference_directivity)
        self.assertEqual(directivity_request.reference_label, "Baseline")

    def test_reference_contracts_reject_garbage_shapes(self):
        invalid_chart_references = (
            "garbage",
            {"frequencies": "garbage"},
            {"spl": [{}]},
            {"di": "garbage"},
        )
        for reference in invalid_chart_references:
            with self.subTest(chart_reference=reference):
                with self.assertRaises(ValidationError):
                    ChartsRenderRequest(reference=reference)

        valid_directivity = {"horizontal": [[[0.0, 90.0]]]}
        invalid_directivity_references = (
            {"reference_frequencies": "garbage"},
            {"reference_frequencies": ["garbage"]},
            {"reference_directivity": []},
            {"reference_label": []},
        )
        for fields in invalid_directivity_references:
            with self.subTest(directivity_reference=fields):
                with self.assertRaises(ValidationError):
                    DirectivityRenderRequest(
                        frequencies=[100.0],
                        directivity=valid_directivity,
                        **fields,
                    )


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

    def test_render_charts_cache_key_and_payload_include_reference(self):
        first_request = ChartsRenderRequest(
            frequencies=[100.0],
            spl=[90.0],
            reference={"label": "Baseline A", "frequencies": [100.0], "spl": [80.0]},
        )
        second_request = ChartsRenderRequest(
            frequencies=[100.0],
            spl=[90.0],
            reference={"label": "Baseline B", "frequencies": [100.0], "spl": [81.0]},
        )
        seen_references = []

        def fake_render(payload):
            seen_references.append(payload["reference"])
            return {"frequency_response": "ZnJlcXVlbmN5LXBuZw=="}

        with patch.object(routes_misc, "render_all_charts", side_effect=fake_render) as renderer:
            asyncio.run(routes_misc.render_charts(first_request))
            asyncio.run(routes_misc.render_charts(second_request))

        self.assertEqual(renderer.call_count, 2)
        self.assertTrue(all(isinstance(reference, dict) for reference in seen_references))
        self.assertEqual(
            [reference["label"] for reference in seen_references],
            ["Baseline A", "Baseline B"],
        )
        self.assertEqual(
            [reference["spl"] for reference in seen_references],
            [[80.0], [81.0]],
        )

    def test_render_directivity_cache_inputs_and_call_include_reference(self):
        reference_directivity = {"horizontal": [[[0.0, 88.0]]]}
        request = DirectivityRenderRequest(
            frequencies=[100.0],
            directivity={"horizontal": [[[0.0, 90.0]]]},
            reference_frequencies=[125.0],
            reference_directivity=reference_directivity,
            reference_label="Baseline",
            reference_level=-8.0,
            theme="dark",
        )

        with patch.object(
            routes_misc,
            "_stable_render_cache_key",
            wraps=routes_misc._stable_render_cache_key,
        ) as cache_key_builder, patch.object(
            routes_misc,
            "render_directivity_plot",
            return_value="ZGlyZWN0aXZpdHktcG5n",
        ) as renderer:
            asyncio.run(routes_misc.render_directivity(request))

        render_inputs = cache_key_builder.call_args.args[1]
        self.assertEqual(render_inputs["reference_frequencies"], [125.0])
        self.assertEqual(render_inputs["reference_directivity"], reference_directivity)
        self.assertEqual(render_inputs["reference_label"], "Baseline")
        renderer.assert_called_once_with(
            request.frequencies,
            request.directivity,
            reference_level=-8.0,
            theme="dark",
            reference_frequencies=[125.0],
            reference_directivity=reference_directivity,
            reference_label="Baseline",
        )


class SolverRuntimeReferenceTest(unittest.TestCase):
    def test_directivity_runtime_forwards_reference_kwargs(self):
        renderer = Mock(return_value="directivity-png")
        fake_hornlab_plots = types.ModuleType("hornlab_plots")
        fake_hornlab_plots.directivity_heatmap_from_legacy_dict = renderer
        frequencies = [100.0]
        directivity = {"horizontal": [[[0.0, 90.0]]]}
        reference_frequencies = [125.0]
        reference_directivity = {"horizontal": [[[0.0, 88.0]]]}

        with patch.dict(sys.modules, {"hornlab_plots": fake_hornlab_plots}):
            result = solver_runtime.render_directivity_plot(
                frequencies,
                directivity,
                reference_level=-8.0,
                theme="dark",
                reference_frequencies=reference_frequencies,
                reference_directivity=reference_directivity,
                reference_label="Baseline",
            )

        self.assertEqual(result, "directivity-png")
        renderer.assert_called_once_with(
            frequencies,
            directivity,
            reference_level=-8.0,
            theme="dark",
            reference_frequencies=reference_frequencies,
            reference_directivity=reference_directivity,
            reference_label="Baseline",
        )

    def test_directivity_runtime_retries_old_signature_once(self):
        def old_signature_renderer(
            frequencies,
            directivity,
            *,
            reference_level,
            theme,
        ):
            return "legacy-compatible-png"

        renderer = Mock(side_effect=old_signature_renderer)
        fake_hornlab_plots = types.ModuleType("hornlab_plots")
        fake_hornlab_plots.directivity_heatmap_from_legacy_dict = renderer
        frequencies = [100.0]
        directivity = {"horizontal": [[[0.0, 90.0]]]}
        reference_frequencies = [125.0]
        reference_directivity = {"horizontal": [[[0.0, 88.0]]]}

        with patch.dict(sys.modules, {"hornlab_plots": fake_hornlab_plots}):
            with self.assertLogs("services.solver_runtime", level="WARNING") as logs:
                result = solver_runtime.render_directivity_plot(
                    frequencies,
                    directivity,
                    reference_level=-8.0,
                    theme="dark",
                    reference_frequencies=reference_frequencies,
                    reference_directivity=reference_directivity,
                    reference_label="Baseline",
                )

        self.assertEqual(result, "legacy-compatible-png")
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(
            renderer.call_args_list,
            [
                call(
                    frequencies,
                    directivity,
                    reference_level=-8.0,
                    theme="dark",
                    reference_frequencies=reference_frequencies,
                    reference_directivity=reference_directivity,
                    reference_label="Baseline",
                ),
                call(
                    frequencies,
                    directivity,
                    reference_level=-8.0,
                    theme="dark",
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
