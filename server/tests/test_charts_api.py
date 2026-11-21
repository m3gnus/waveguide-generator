from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from server.charts import api as charts_api


def _clear_chart_cache() -> None:
    with charts_api._cache_lock:
        charts_api._cache.clear()


@pytest.mark.parametrize("reference_level", [-3.0, -6.0, -9.0, -12.0])
def test_directivity_request_passes_display_range_to_renderer_and_cache(
    monkeypatch, reference_level: float,
) -> None:
    calls = []
    monkeypatch.setattr(charts_api, "_plots", lambda: object())

    def render(_plots, _frequencies, _directivity, **kwargs):
        calls.append(kwargs)
        return str(kwargs["display_range_db"])

    monkeypatch.setattr(charts_api, "render_directivity_heatmap_b64", render)
    _clear_chart_cache()
    payload = {
        "frequencies": [100.0, 200.0],
        "directivity": {"horizontal": [[[0.0, 0.0]], [[0.0, 0.0]]]},
        "reference_level": reference_level,
        "display_range_db": [reference_level * 5, 0.0],
    }
    request = charts_api.DirectivityRenderRequest(**payload)
    first = asyncio.run(charts_api.render_directivity(request))
    assert asyncio.run(charts_api.render_directivity(request)) == first
    assert len(calls) == 1
    assert calls[0]["display_range_db"] == [reference_level * 5, 0.0]
    payload["display_range_db"] = [-45.0 if reference_level == -6.0 else -30.0, 0.0]
    second = asyncio.run(charts_api.render_directivity(charts_api.DirectivityRenderRequest(**payload)))
    assert second != first
    assert len(calls) == 2
    _clear_chart_cache()


@pytest.mark.parametrize("display_range_db", [[0.0, -30.0], [-30.0, -30.0], [-30.0], [-30.0, float("nan")]])
def test_directivity_request_rejects_invalid_display_ranges(display_range_db) -> None:
    with pytest.raises(ValidationError):
        charts_api.DirectivityRenderRequest(frequencies=[], directivity={}, display_range_db=display_range_db)


def _spec() -> dict:
    return {
        "kind": "lines",
        "title": "Group delay",
        "x": {"label": "Frequency [Hz]", "scale": "log"},
        "y": {"label": "Group delay [ms]"},
        "series": [{"label": "Run", "x": [100.0, 1000.0, 10000.0], "y": [1.0, None, 0.5], "color": 0}],
    }


def test_chart_spec_route_renders_once_per_spec_and_theme(monkeypatch) -> None:
    calls = []

    class Plots:
        @staticmethod
        def chart_spec_b64(spec, *, theme):
            calls.append((spec, theme))
            return "YQ=="

    monkeypatch.setattr(charts_api, "_plots", lambda: Plots)
    _clear_chart_cache()
    request = charts_api.ChartSpecRenderRequest(spec=_spec())
    assert request.theme == "classic"
    first = asyncio.run(charts_api.render_chart(request))
    assert first == {"image": "data:image/png;base64,YQ=="}
    # The same chart asked for again (copy, then export) is not drawn again.
    assert asyncio.run(charts_api.render_chart(request)) == first
    assert len(calls) == 1
    asyncio.run(charts_api.render_chart(charts_api.ChartSpecRenderRequest(spec=_spec(), theme="vellum")))
    assert [theme for _spec_, theme in calls] == ["classic", "vellum"]
    # What reaches the renderer is the request as sent, with defaults filled.
    sent = calls[0][0]
    assert sent["series"][0]["x"] == [100.0, 1000.0, 10000.0]
    assert sent["series"][0]["y"] == [1.0, None, 0.5]
    assert sent["x"] == {"label": "Frequency [Hz]", "scale": "log"}
    _clear_chart_cache()


def test_chart_spec_route_refuses_bad_specs_and_empty_charts(monkeypatch) -> None:
    class Plots:
        @staticmethod
        def chart_spec_b64(spec, *, theme):
            if spec["title"] == "refused":
                raise AttributeError("'str' object has no attribute 'get'")
            return None

    monkeypatch.setattr(charts_api, "_plots", lambda: Plots)
    _clear_chart_cache()
    with pytest.raises(charts_api.HTTPException) as refused:
        asyncio.run(charts_api.render_chart(charts_api.ChartSpecRenderRequest(spec={**_spec(), "title": "refused"})))
    # A renderer refusal is a program fault: a fixed sentence, not Python's.
    assert refused.value.status_code == 422
    assert refused.value.detail == charts_api.CHART_SPEC_RENDER_FAILED
    with pytest.raises(charts_api.HTTPException) as empty:
        asyncio.run(charts_api.render_chart(charts_api.ChartSpecRenderRequest(spec={**_spec(), "series": []})))
    assert empty.value.status_code == 422
    assert "no values to draw" in empty.value.detail
    with pytest.raises(ValidationError):
        charts_api.ChartSpecRenderRequest(spec=_spec(), theme="no-such-theme")
    with pytest.raises(ValidationError, match="finite"):
        charts_api.ChartSpecRenderRequest(spec={**_spec(), "series": [{"x": [float("nan")], "y": [1.0]}]})
    _clear_chart_cache()


def test_chart_spec_route_draws_with_the_pinned_renderer() -> None:
    plots = pytest.importorskip("hornlab_plots")
    if not hasattr(plots, "chart_spec_b64"):
        pytest.fail("the pinned hornlab-plots has no chart_spec_b64; install the pin in pins.json")
    _clear_chart_cache()
    image = asyncio.run(charts_api.render_chart(charts_api.ChartSpecRenderRequest(spec=_spec())))["image"]
    assert image.startswith("data:image/png;base64,iVBORw0KGgo")
    _clear_chart_cache()


def _polar(**radius) -> dict:
    return {"kind": "polar", "r": {"min": -30.0, "max": 0.0, **radius},
            "series": [{"angle": [-90.0, 0.0, 90.0], "r": [-6.0, 0.0, None]}]}


@pytest.mark.parametrize(("spec", "field"), [
    # The reviewers' probes: each took seconds to minutes under the render lock.
    (_polar(step=0.001), "rings"),
    (_polar(min=-2000.0, step=1.0), "greater than or equal"),
    ({**_spec(), "x": {"scale": "log", "min": 1e-300, "max": 1e300}}, "less than or equal"),
    ({**_spec(), "x": {"scale": "log", "min": 1e-3, "max": 1e6}}, "decades"),
    ({**_spec(), "hlines": [{"y": 0.0}] * 20_000}, "at most 16"),
    ({**_spec(), "bands": [{"x0": 100.0, "x1": 200.0}] * 1_000}, "at most 64"),
    ({**_spec(), "series": ["x"]}, "valid dictionary"),
    ({**_spec(), "x": 5}, "valid dictionary"),
    ({**_spec(), "series": [{"x": [100.0] * 20_001, "y": [1.0] * 20_001}]}, "at most 20000"),
    ({**_spec(), "series": [{"x": [100.0], "y": [1.0]}] * 65}, "at most 64"),
    ({**_spec(), "series": [{"x": [100.0], "y": [1.0], "color": "#ff0000"}]}, "color"),
    ({**_spec(), "series": [{"x": [100.0], "y": [1.0], "opacity": 5.0}]}, "less than or equal"),
    ({**_spec(), "series": [{"x": [0.0, 100.0], "y": [1.0, 2.0]}]}, "above 0"),
    ({**_spec(), "series": [{"x": [100.0], "y": [int("9" * 400)]}]}, "y"),
    ({**_spec(), "kind": "pie"}, "kind"),
    ({**_spec(), "y": {"min": 5.0, "max": 1.0}}, "reversed"),
])
def test_chart_spec_request_refuses_costly_and_malformed_specs(spec, field) -> None:
    with pytest.raises(ValidationError) as refused:
        charts_api.ChartSpecRenderRequest(spec=spec)
    assert field in str(refused.value)


def test_chart_spec_route_answers_422_naming_the_field_over_http(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    rendered = []
    monkeypatch.setattr(charts_api, "_plots", lambda: rendered.append(1))
    application = FastAPI()
    charts_api.mount_charts(application)
    client = TestClient(application, raise_server_exceptions=False)
    for spec in ({"series": ["x"]}, {**_spec(), "series": ["x"]}, _polar(step=0.001)):
        response = client.post("/api/render-chart", json={"spec": spec})
        assert response.status_code == 422
        assert all("loc" in item and "msg" in item for item in response.json()["detail"])
    assert rendered == []


def test_valid_polar_spec_reaches_the_renderer_with_its_wire_names(monkeypatch) -> None:
    calls = []

    class Plots:
        @staticmethod
        def chart_spec_b64(spec, *, theme):
            calls.append(spec)
            return "YQ=="

    monkeypatch.setattr(charts_api, "_plots", lambda: Plots)
    _clear_chart_cache()
    spec = {**_polar(step=10.0), "shade": [{"from": 90.0, "to": 180.0, "label": "rear"}]}
    asyncio.run(charts_api.render_chart(charts_api.ChartSpecRenderRequest(spec=spec)))
    assert calls[0]["shade"] == [{"from": 90.0, "to": 180.0, "label": "rear"}]
    assert calls[0]["series"][0]["r"] == [-6.0, 0.0, None]
    _clear_chart_cache()


def test_long_chart_text_is_shortened_not_refused() -> None:
    long = "Spatial average from " + "a solver definition, " * 40
    request = charts_api.ChartSpecRenderRequest(spec={
        **_spec(), "title": long, "subtitle": long,
        "series": [{"label": long, "x": [100.0], "y": [1.0]}],
    })
    for text in (request.spec.title, request.spec.subtitle, request.spec.series[0].label):
        assert len(text) == charts_api.MAX_SPEC_TEXT
        assert text.endswith("…")
        assert long.startswith(text[:-1])
    short = charts_api.ChartSpecRenderRequest(spec={**_spec(), "title": "Group delay"})
    assert short.spec.title == "Group delay"
    with pytest.raises(ValidationError, match="at most 10000 characters"):
        charts_api.ChartSpecRenderRequest(spec={**_spec(), "title": "x" * 10_001})


def _sphere(kind: str = "balloon", **overrides) -> dict:
    spec = {"kind": kind, "title": "View", "theta_deg": [0.0, 90.0, 180.0], "phi_deg": [0.0, 120.0, 240.0],
            "levels_db": [[0.0, 0.0, 0.0], [-6.0, None, -9.0], [-20.0, -20.0, -20.0]]}
    spec.update(overrides)
    return spec


@pytest.mark.parametrize("kind", ["balloon", "beam_map"])
def test_spherical_view_specs_reach_the_renderer(monkeypatch, kind) -> None:
    calls = []

    class Plots:
        @staticmethod
        def chart_spec_b64(spec, *, theme):
            calls.append(spec)
            return "YQ=="

    monkeypatch.setattr(charts_api, "_plots", lambda: Plots)
    _clear_chart_cache()
    asyncio.run(charts_api.render_chart(charts_api.ChartSpecRenderRequest(spec=_sphere(kind))))
    assert calls[0]["kind"] == kind
    assert calls[0]["levels_db"][1] == [-6.0, None, -9.0]
    assert calls[0]["range_db"] == 30.0
    _clear_chart_cache()


@pytest.mark.parametrize(("spec", "field"), [
    (_sphere(theta_deg=[0.0]), "at least 2"),
    (_sphere(phi_deg=[float(i) for i in range(722)]), "at most 721"),
    (_sphere(levels_db=[[0.0]]), "one row per theta"),
    (_sphere(range_db=0.0), "greater than 0"),
    (_sphere(theta_deg=[0.0, 90.0, 200.0]), "less than or equal"),
])
def test_spherical_view_specs_are_bounded(spec, field) -> None:
    with pytest.raises(ValidationError) as refused:
        charts_api.ChartSpecRenderRequest(spec=spec)
    assert field in str(refused.value)


def test_the_multi_chart_route_is_retired() -> None:
    paths = {route.path for route in charts_api.router.routes}
    assert "/api/render-charts" not in paths
    assert {"/api/render-chart", "/api/render-directivity", "/api/themes"} <= paths
