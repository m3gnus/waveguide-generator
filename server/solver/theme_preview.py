"""Chart-theme metadata and montage previews backed by ``hornlab_plots``.

This module owns the small amount of chart-theme domain state that the render
routes and the Appearance settings UI share:

- ``DEFAULT_CHART_THEME`` — the hornlab-plots theme used when a render request
  does not name one. ``dark`` is the built-in Arctic-Night theme modeled on the
  Waveguide Generator's former hardcoded-dark axes, so defaulting to it
  preserves the legacy look (the directivity heatmap is effectively identical;
  the line charts adopt the same navy surface as the heatmap).
- ``resolve_chart_theme`` — turn ``None``/empty into the default theme name.
- ``list_available_themes`` — name + human label for every registered theme,
  so the frontend does not hardcode the registry.
- ``build_theme_montage_b64`` — a 2x2 montage (directivity heatmap, frequency
  response, directivity index, impedance) rendered for one theme from synthetic
  demo data by the four canonical ``hornlab_plots`` renderers, for the settings
  preview. Results are cached per theme.

Everything degrades gracefully when the optional ``hornlab_plots`` sibling is
not installed: theme listing collapses to the default name and montage/render
callers fall back to the in-repo legacy renderer.
"""

from __future__ import annotations

import base64
import io
from typing import Any, Dict, List, Optional

import numpy as np


# ``hornlab`` (Arctic Night) is the default chart theme; ``classic`` leads the
# picker order but is not the default. ``dark`` is byte-identical to ``hornlab``
# and is hidden from the picker (see _CHART_THEME_ORDER) so only one Arctic Night
# shows; both stay valid theme values for back-compat.
DEFAULT_CHART_THEME = "hornlab"


# Short human labels for the hornlab-plots built-in themes, shown in the
# Appearance settings picker. Any registered theme missing here falls back to a
# title-cased name.
_CHART_THEME_LABELS: Dict[str, str] = {
    "classic": "Classic",
    "hornlab": "Arctic Night",
    "dark": "Arctic Night",
    "granite": "Light Paper",
    "abyss": "Dark Studio",
    "blueprint": "Blueprint",
    "journal": "Journal",
    "contrast": "High Contrast",
    "sepia": "Sepia",
    "phosphor": "CRT Green",
    "ember": "Warm Charcoal",
}


# Picker order: Classic first, then the rest. ``dark`` is intentionally omitted
# because it is byte-identical to ``hornlab`` (Arctic Night) — the picker shows
# a single Arctic Night entry.
_CHART_THEME_ORDER: tuple = (
    "classic",
    "hornlab",
    "granite",
    "abyss",
    "blueprint",
    "journal",
    "contrast",
    "sepia",
    "phosphor",
    "ember",
)


# Montage cache keyed by resolved theme name (montages are deterministic).
_MONTAGE_CACHE: Dict[str, str] = {}


def resolve_chart_theme(theme: Optional[str]) -> str:
    """Return a concrete theme name, mapping ``None``/empty to the default."""
    if theme is None:
        return DEFAULT_CHART_THEME
    name = str(theme).strip().lower()
    return name or DEFAULT_CHART_THEME


def list_available_themes() -> List[Dict[str, Any]]:
    """Return ``[{name, label, default}, ...]`` for every registered theme.

    Falls back to a single-entry list (the default) when ``hornlab_plots`` is
    not installed, so the UI still renders a coherent picker.
    """
    try:
        from hornlab_plots import BUILTIN_THEMES
    except ImportError:
        name = DEFAULT_CHART_THEME
        return [
            {
                "name": name,
                "label": _CHART_THEME_LABELS.get(name, name.replace("_", " ").title()),
                "default": True,
            }
        ]

    # Curated order (Classic first); ``dark`` is hidden as a duplicate Arctic
    # Night. Any newly registered theme not in the curated order is appended
    # (except the hidden ``dark`` alias) so the picker never silently drops one.
    ordered = [name for name in _CHART_THEME_ORDER if name in BUILTIN_THEMES]
    extras = [
        name
        for name in BUILTIN_THEMES
        if name not in _CHART_THEME_ORDER and name != "dark"
    ]
    themes: List[Dict[str, Any]] = []
    for name in [*ordered, *extras]:
        themes.append(
            {
                "name": name,
                "label": _CHART_THEME_LABELS.get(name, name.replace("_", " ").title()),
                "default": name == DEFAULT_CHART_THEME,
            }
        )
    return themes


# ── Synthetic demo data for the montage preview ────────────────────────────────


def _lr4_lowpass_db(freqs: np.ndarray, fc: float) -> np.ndarray:
    return -10.0 * np.log10(1.0 + (freqs / fc) ** 8)


def _lr4_highpass_db(freqs: np.ndarray, fc: float) -> np.ndarray:
    return -10.0 * np.log10(1.0 + (fc / freqs) ** 8)


def _synthetic_frequency_response():
    """3-way LR4 crossover (LF/MF/HF + summed) around a 400 Hz low crossover."""
    from hornlab_plots import FrequencyResponseCurve

    freqs = np.geomspace(30.0, 20000.0, 320)
    xo_low, xo_high = 400.0, 2000.0
    level = 96.0
    lf = level + _lr4_lowpass_db(freqs, xo_low)
    hf = level + _lr4_highpass_db(freqs, xo_high)
    mf = level + _lr4_highpass_db(freqs, xo_low) + _lr4_lowpass_db(freqs, xo_high)
    summed = 20.0 * np.log10(
        10.0 ** (lf / 20.0) + 10.0 ** (mf / 20.0) + 10.0 ** (hf / 20.0)
    )
    curves = [
        FrequencyResponseCurve(freqs, lf, "LF LR4 LP", role="lf", crossover=True),
        FrequencyResponseCurve(freqs, mf, "MF BP", role="mf", crossover=True),
        FrequencyResponseCurve(freqs, hf, "HF LR4 HP", role="hf", crossover=True),
        FrequencyResponseCurve(freqs, summed, "Summed on-axis", role="combined"),
    ]
    return curves, xo_low


def _synthetic_directivity_legacy():
    """Vertical-plane directivity in the legacy dict shape used by WG."""
    freqs = np.geomspace(500.0, 20000.0, 56)
    angles = np.linspace(-90.0, 90.0, 37)
    octaves = np.log2(freqs / freqs[0])
    sigma = np.clip(55.0 - 6.0 * octaves, 20.0, None)
    lobe = 3.0 * np.exp(-((np.abs(angles) - 55.0) ** 2) / 260.0)
    vertical = []
    for fi in range(freqs.size):
        col = -20.0 * (1.0 - np.exp(-(angles ** 2) / (2.0 * sigma[fi] ** 2)))
        col = col + lobe * (np.clip(octaves[fi] - 3.0, 0.0, None) / 3.0)
        vertical.append([[float(a), float(d)] for a, d in zip(angles, col)])
    return freqs.tolist(), {"vertical": vertical}


def _synthetic_directivity_index():
    freqs = np.geomspace(500.0, 20000.0, 56)
    octaves = np.log2(freqs / freqs[0])
    return freqs.tolist(), {
        "horizontal": (4.0 + 2.0 * octaves).tolist(),
        "vertical": (4.0 + 2.35 * octaves).tolist(),
        "diagonal": (4.0 + 2.15 * octaves).tolist(),
    }


def _synthetic_impedance():
    # Absolute-style acoustic-impedance resonance (tens range) so the panel is
    # legible through impedance_b64's Pa.s/m axis window. A low-frequency
    # reactive resonance near 70 Hz plus a smaller mid resonance.
    freqs = np.geomspace(30.0, 20000.0, 260)
    log_f = np.log10(freqs)
    z_re = (
        18.0
        + 78.0 * np.exp(-((log_f - np.log10(70.0)) ** 2) / 0.028)
        + 28.0 * np.exp(-((log_f - np.log10(1300.0)) ** 2) / 0.02)
    )
    z_im = (
        -68.0 * np.exp(-((log_f - np.log10(95.0)) ** 2) / 0.05)
        + 34.0 * np.exp(-((log_f - np.log10(1200.0)) ** 2) / 0.03)
    )
    return freqs.tolist(), z_re.tolist(), z_im.tolist()


def _decode_png(b64: Optional[str]):
    if not b64:
        return None
    import matplotlib.pyplot as plt

    return plt.imread(io.BytesIO(base64.b64decode(b64)), format="png")


def _compose_montage(theme_name: str, panel_b64: List[Optional[str]]) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from hornlab_plots import get_theme

    theme_obj = get_theme(theme_name)

    fig, axes = plt.subplots(2, 2, figsize=(15.0, 8.4))
    fig.patch.set_facecolor(theme_obj.figure_bg)
    for ax, b64 in zip(axes.ravel(), panel_b64):
        ax.set_facecolor(theme_obj.figure_bg)
        ax.axis("off")
        img = _decode_png(b64)
        if img is not None:
            ax.imshow(img, aspect="auto", interpolation="antialiased")

    fig.suptitle(
        f'hornlab_plots  theme="{theme_name}"  —  four canonical renderers',
        color=theme_obj.text_color,
        fontsize=16,
        fontweight="600",
    )
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.965))

    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=100,
        facecolor=fig.get_facecolor(),
        edgecolor="none",
    )
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def build_theme_montage_b64(theme: Optional[str] = None) -> str:
    """Render (and cache) a 2x2 montage PNG for ``theme``; return base64 (no prefix).

    The panels are produced by the four canonical ``hornlab_plots`` renderers
    (directivity heatmap, 3-way frequency response, directivity index, acoustic
    impedance) from synthetic demo data, then composed with matplotlib subplots.

    Raises ``RuntimeError`` when ``hornlab_plots`` is not installed.
    """
    theme_name = resolve_chart_theme(theme)
    cached = _MONTAGE_CACHE.get(theme_name)
    if cached is not None:
        return cached

    try:
        import hornlab_plots
    except ImportError as exc:
        raise RuntimeError(f"Theme preview renderer not available: {exc}") from exc

    fr_curves, crossover_hz = _synthetic_frequency_response()
    fr_b64 = hornlab_plots.frequency_response_multi_b64(
        fr_curves,
        title="Frequency Response — 3-way",
        crossover_hz=crossover_hz,
        theme=theme_name,
    )

    dir_freqs, dir_map = _synthetic_directivity_legacy()
    heat_b64 = hornlab_plots.directivity_heatmap_from_legacy_dict(
        dir_freqs, dir_map, theme=theme_name
    )

    di_freqs, di_map = _synthetic_directivity_index()
    di_b64 = hornlab_plots.directivity_index_b64(di_freqs, di_map, theme=theme_name)

    imp_freqs, imp_real, imp_imag = _synthetic_impedance()
    imp_b64 = hornlab_plots.impedance_b64(imp_freqs, imp_real, imp_imag, theme=theme_name)

    montage = _compose_montage(theme_name, [heat_b64, fr_b64, di_b64, imp_b64])
    _MONTAGE_CACHE[theme_name] = montage
    return montage
