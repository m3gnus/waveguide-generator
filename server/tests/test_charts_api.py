from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from server.charts import api as charts_api


def _clear_chart_cache() -> None:
    with charts_api._cache_lock:
        charts_api._cache.clear()


def test_chart_request_accepts_primary_and_reference_sound_speeds() -> None:
    request = charts_api.ChartsRenderRequest(
        sound_speed_m_per_s=346.0,
        reference={"sound_speed_m_per_s": 341.0},
    )

    assert request.sound_speed_m_per_s == 346.0
    assert request.reference is not None
    assert request.reference.sound_speed_m_per_s == 341.0


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
    assert calls[0][0] == _spec()
    _clear_chart_cache()


def test_chart_spec_route_refuses_bad_specs_and_empty_charts(monkeypatch) -> None:
    class Plots:
        @staticmethod
        def chart_spec_b64(spec, *, theme):
            if spec.get("kind") == "pie":
                raise ValueError("chart kind must be one of: lines, polar")
            return None

    monkeypatch.setattr(charts_api, "_plots", lambda: Plots)
    _clear_chart_cache()
    with pytest.raises(charts_api.HTTPException) as refused:
        asyncio.run(charts_api.render_chart(charts_api.ChartSpecRenderRequest(spec={"kind": "pie"})))
    assert refused.value.status_code == 422
    with pytest.raises(charts_api.HTTPException) as empty:
        asyncio.run(charts_api.render_chart(charts_api.ChartSpecRenderRequest(spec={"series": []})))
    assert empty.value.status_code == 422
    assert "no values to draw" in empty.value.detail
    with pytest.raises(ValidationError):
        charts_api.ChartSpecRenderRequest(spec=_spec(), theme="no-such-theme")
    with pytest.raises(ValidationError, match="finite"):
        charts_api.ChartSpecRenderRequest(spec={"series": [{"x": [float("nan")], "y": [1.0]}]})
    _clear_chart_cache()


def test_chart_spec_route_draws_with_the_pinned_renderer() -> None:
    plots = pytest.importorskip("hornlab_plots")
    if not hasattr(plots, "chart_spec_b64"):
        pytest.fail("the pinned hornlab-plots has no chart_spec_b64; install the pin in pins.json")
    _clear_chart_cache()
    image = asyncio.run(charts_api.render_chart(charts_api.ChartSpecRenderRequest(spec=_spec())))["image"]
    assert image.startswith("data:image/png;base64,iVBORw0KGgo")
    _clear_chart_cache()
