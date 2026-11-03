from __future__ import annotations

import base64
from copy import deepcopy
import importlib
import inspect
import sys
from types import ModuleType, SimpleNamespace

import hornlab_plots
import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from server.charts.directivity import (
    add_angle_guides,
    render_directivity_heatmap_b64,
)


class _Theme:
    grid_color = "#808080"
    secondary_grid_alpha = 0.25


def _horizontal_values(axis) -> set[float]:
    values: set[float] = set()
    for line in axis.lines:
        y_values = list(line.get_ydata())
        if len(y_values) >= 2 and all(value == y_values[0] for value in y_values):
            values.add(float(y_values[0]))
    return values


def test_directivity_graticule_fills_every_ten_degrees_without_duplicates() -> None:
    figure, axis = plt.subplots()
    axis.set_ylabel("Angle [deg]")
    axis.set_ylim(-90, 90)
    axis.axhline(0, color="black")

    add_angle_guides(figure, _Theme())

    assert _horizontal_values(axis) == set(range(-80, 90, 10))
    assert len(axis.lines) == 17
    plt.close(figure)


def test_non_directivity_axes_are_left_alone() -> None:
    figure, axis = plt.subplots()
    axis.set_ylabel("dB")

    add_angle_guides(figure, _Theme())

    assert len(axis.lines) == 0
    plt.close(figure)


def test_directivity_graticule_uses_the_requested_interval() -> None:
    figure, axis = plt.subplots()
    axis.set_ylabel("Angle [deg]")
    axis.set_ylim(-90, 90)

    add_angle_guides(figure, _Theme(), angle_guide_step=15)

    assert _horizontal_values(axis) == set(range(-75, 90, 15))
    plt.close(figure)


def test_pinned_renderer_emits_png_with_reference_overlay_path_enabled() -> None:
    frequencies = [500.0, 1_000.0, 2_000.0]
    directivity = {
        "horizontal": [
            [[-90.0, -12.0], [0.0, 0.0], [90.0, -12.0]]
            for _frequency in frequencies
        ]
    }

    encoded = render_directivity_heatmap_b64(
        hornlab_plots,
        frequencies,
        directivity,
        reference_level=-6.0,
        theme="console",
        reference_frequencies=frequencies,
        reference_directivity=directivity,
        reference_label="baseline",
    )

    assert encoded is not None
    assert base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n")


def test_zero_interval_draws_no_guides() -> None:
    # 0 is the shipped default: the map keeps only the renderer's own lines.
    figure, axis = plt.subplots()
    axis.set_ylabel("Angle [deg]")
    axis.set_ylim(-90, 90)
    axis.axhline(0, color="black")

    add_angle_guides(figure, _Theme(), angle_guide_step=0)

    assert _horizontal_values(axis) == {0.0}
    assert len(axis.lines) == 1
    plt.close(figure)


def test_non_finite_interval_is_rejected() -> None:
    figure, axis = plt.subplots()
    axis.set_ylabel("Angle [deg]")
    try:
        with pytest.raises(ValueError):
            add_angle_guides(figure, _Theme(), angle_guide_step=float("nan"))
    finally:
        plt.close(figure)


def test_png_renders_with_the_graticule_turned_off() -> None:
    # The PNG export renders this map before any chart is saved, so a step of 0
    # must not fail the whole format.
    frequencies = [500.0, 1_000.0, 2_000.0]
    directivity = {
        "horizontal": [
            [[-90.0, -12.0], [0.0, 0.0], [90.0, -12.0]]
            for _frequency in frequencies
        ]
    }

    encoded = render_directivity_heatmap_b64(
        hornlab_plots,
        frequencies,
        directivity,
        reference_level=-6.0,
        theme="console",
        angle_guide_step=0.0,
    )

    assert encoded is not None
    assert base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("reference_level", [-3.0, -6.0, -9.0, -12.0])
def test_display_range_reaches_canonical_preparation_and_rendering(
    monkeypatch, reference_level: float,
) -> None:
    module = ModuleType("_test_range_heatmap")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    calls = []

    def renderer(_frequencies, _directivity, *, display_range_db=(-30.0, 0.0), **kwargs):
        pytest.fail("The compatibility path must retain the angular guides")

    renderer.__module__ = module.__name__

    def planes(_frequencies, directivity, *, smooth, display_range_db=(-30.0, 0.0)):
        calls.append(("planes", display_range_db, directivity))
        return [object()]

    figure = SimpleNamespace(
        axes=[], get_facecolor=lambda: "black",
        savefig=lambda output, **kwargs: output.write(b"selected map"),
    )

    def build_figure(_planes, *, display_range_db=(-30.0, 0.0), **kwargs):
        calls.append(("figure", display_range_db, None))
        return figure

    module._build_planes_from_legacy = planes
    module._build_figure_from_planes = build_figure
    module.apply_theme_overrides = lambda _theme: _Theme()
    module.plt = SimpleNamespace(close=lambda _figure: None)
    directivity = {"horizontal": [[[0.0, 0.0]]]}
    limits = (reference_level * 5, 0.0)

    encoded = render_directivity_heatmap_b64(
        SimpleNamespace(directivity_heatmap_from_legacy_dict=renderer),
        [100.0], directivity, reference_level=reference_level, theme="console",
        display_range_db=limits, reference_frequencies=[100.0],
        reference_directivity=directivity,
    )

    assert base64.b64decode(encoded) == b"selected map"
    assert calls == [
        ("planes", limits, directivity), ("planes", limits, directivity),
        ("figure", limits, None),
    ]


@pytest.mark.parametrize("reference_level", [-3.0, -6.0, -9.0, -12.0])
def test_supported_range_sets_canonical_image_limits(monkeypatch, reference_level) -> None:
    renderer = hornlab_plots.directivity_heatmap_from_legacy_dict
    if "display_range_db" not in inspect.signature(renderer).parameters:
        pytest.skip("The current plots pin has no display-range control")
    module = importlib.import_module(renderer.__module__)
    build_figure = module._build_figure_from_planes
    figures = []

    def capture(*args, **kwargs):
        figure = build_figure(*args, **kwargs)
        figures.append(figure)
        return figure

    monkeypatch.setattr(module, "_build_figure_from_planes", capture)
    directivity = {"horizontal": [
        [[-90.0, -50.0], [-45.0, -35.0], [0.0, 0.0], [45.0, -35.0], [90.0, -50.0]]
        for _ in range(3)
    ]}
    before = deepcopy(directivity)
    floor = reference_level * 5
    encoded = render_directivity_heatmap_b64(
        hornlab_plots, [500.0, 1000.0, 2000.0], directivity,
        reference_level=reference_level, display_range_db=(floor, 0.0),
        theme="console", angle_guide_step=15.0, dpi=30,
    )

    assert base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n")
    image = figures[0].axes[0].collections[0]
    assert image.get_clim() == (floor, 0.0)
    assert np.min(image.get_array()) == pytest.approx(max(floor, -50.0))
    assert directivity == before


def test_old_pin_keeps_rendering_when_the_display_range_is_unsupported() -> None:
    renderer = hornlab_plots.directivity_heatmap_from_legacy_dict
    if "display_range_db" in inspect.signature(renderer).parameters:
        pytest.skip("Compatibility check for the older plots pin")
    directivity = {"horizontal": [
        [[-90.0, -50.0], [0.0, 0.0], [90.0, -50.0]] for _ in range(3)
    ]}
    kwargs = dict(reference_level=-12.0, theme="console", dpi=30)
    original = render_directivity_heatmap_b64(hornlab_plots, [500, 1000, 2000], directivity, **kwargs)
    requested = render_directivity_heatmap_b64(
        hornlab_plots, [500, 1000, 2000], directivity, display_range_db=(-60.0, 0.0), **kwargs,
    )

    assert original is not None
    assert requested == original


def test_public_angle_grid_api_gets_the_display_range() -> None:
    calls = []

    def renderer(frequencies, directivity, *, angle_grid_step, display_range_db, **kwargs):
        calls.append((angle_grid_step, display_range_db))
        return "cG5n"

    assert render_directivity_heatmap_b64(
        SimpleNamespace(directivity_heatmap_from_legacy_dict=renderer),
        [100.0], {"horizontal": [[[0.0, 0.0]]]}, reference_level=-12.0,
        theme="console", display_range_db=(-60.0, 0.0), angle_guide_step=15.0,
    ) == "cG5n"
    assert calls == [(15.0, (-60.0, 0.0))]
