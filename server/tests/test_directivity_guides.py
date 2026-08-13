from __future__ import annotations

import base64

import hornlab_plots
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from server.charts.directivity import (
    add_ten_degree_angle_guides,
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

    add_ten_degree_angle_guides(figure, _Theme())

    assert _horizontal_values(axis) == set(range(-80, 90, 10))
    assert len(axis.lines) == 17
    plt.close(figure)


def test_non_directivity_axes_are_left_alone() -> None:
    figure, axis = plt.subplots()
    axis.set_ylabel("dB")

    add_ten_degree_angle_guides(figure, _Theme())

    assert len(axis.lines) == 0
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
