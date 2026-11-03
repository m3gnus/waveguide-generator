from __future__ import annotations

import asyncio
import sys
import threading
from types import ModuleType, SimpleNamespace

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


def test_identical_concurrent_theme_previews_share_the_inflight_render(
    monkeypatch,
) -> None:
    _clear_chart_cache()
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def render(theme: str) -> str:
        calls.append(theme)
        started.set()
        assert release.wait(timeout=2.0)
        return "rendered"

    monkeypatch.setattr(charts_api, "_preview", render)

    async def exercise() -> list[dict[str, str]]:
        first = asyncio.create_task(charts_api.theme_preview("classic"))
        assert await asyncio.to_thread(started.wait, 2.0)
        second = asyncio.create_task(charts_api.theme_preview("classic"))
        await asyncio.sleep(0.01)
        assert calls == ["classic"]
        release.set()
        return await asyncio.gather(first, second)

    responses = asyncio.run(exercise())

    assert responses == [
        {"theme": "classic", "image": "data:image/png;base64,rendered"},
        {"theme": "classic", "image": "data:image/png;base64,rendered"},
    ]
    assert calls == ["classic"]
    _clear_chart_cache()


def test_theme_preview_holds_render_lock_through_pyplot_composition(monkeypatch) -> None:
    composition_started = threading.Event()
    release_composition = threading.Event()
    render_calls: list[str] = []

    class Plots:
        def render_all_charts_b64(self, _payload, *, theme: str):
            render_calls.append(theme)
            return {"panel": "cGFuZWw="}

        def get_theme(self, _theme: str):
            composition_started.set()
            assert release_composition.wait(timeout=2.0)
            return SimpleNamespace(figure_bg="#000000")

    class Axis:
        def axis(self, _value: str) -> None:
            pass

        def imshow(self, _value) -> None:
            pass

    class Axes:
        def __init__(self) -> None:
            self._axes = [Axis() for _ in range(4)]

        def ravel(self) -> list[Axis]:
            return self._axes

    class Patch:
        def set_facecolor(self, _value: str) -> None:
            pass

    class Figure:
        patch = Patch()

        def tight_layout(self) -> None:
            pass

        def get_facecolor(self) -> str:
            return "#000000"

        def savefig(self, output, **_kwargs) -> None:
            output.write(b"preview")

    matplotlib = ModuleType("matplotlib")
    matplotlib.__path__ = []  # type: ignore[attr-defined]
    matplotlib.use = lambda _backend: None  # type: ignore[attr-defined]
    pyplot = ModuleType("matplotlib.pyplot")
    pyplot.subplots = lambda *_args, **_kwargs: (Figure(), Axes())  # type: ignore[attr-defined]
    pyplot.imread = lambda *_args, **_kwargs: object()  # type: ignore[attr-defined]
    pyplot.close = lambda _figure: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "matplotlib", matplotlib)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", pyplot)
    monkeypatch.setattr(charts_api, "_plots", lambda: Plots())

    errors: list[BaseException] = []

    def preview() -> None:
        try:
            charts_api._preview("classic")
        except BaseException as exc:
            errors.append(exc)

    preview_thread = threading.Thread(target=preview)
    preview_thread.start()
    assert composition_started.wait(timeout=2.0)

    chart_thread = threading.Thread(
        target=charts_api._render_charts, args=({"theme": "hornlab"},)
    )
    chart_thread.start()
    assert render_calls == ["classic"]

    release_composition.set()
    preview_thread.join(timeout=2.0)
    chart_thread.join(timeout=2.0)

    assert not preview_thread.is_alive()
    assert not chart_thread.is_alive()
    assert errors == []
    assert render_calls == ["classic", "hornlab"]
