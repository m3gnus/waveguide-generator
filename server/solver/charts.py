"""
Matplotlib-based chart rendering for BEM simulation results.

Produces publication-quality charts with dark background styling
matching the directivity plot in directivity_plot.py.

All renderers return base64-encoded PNG strings (without data URI prefix).
"""

import io
import base64
import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from .directivity_plot import _log_grid_lines, _freq_formatter, _preferred_frequency_ticks


def _setup_dark_axes(ax, xlabel, ylabel, title):
    """Apply dark theme styling to axes."""
    ax.set_facecolor('#1a1a1a')
    ax.set_xlabel(xlabel, color='#cccccc', fontsize=11)
    ax.set_ylabel(ylabel, color='#cccccc', fontsize=11)
    ax.set_title(title, color='#e0e0e0', fontsize=13, fontweight='600', pad=8)
    ax.tick_params(colors='#aaaaaa', labelsize=9)
    ax.spines['bottom'].set_color('#555555')
    ax.spines['left'].set_color('#555555')
    ax.spines['top'].set_color('#333333')
    ax.spines['right'].set_color('#333333')
    ax.grid(True, alpha=0.15, color='white', linewidth=0.5)


def _add_log_grid(ax, freq_min, freq_max, *, detailed=False):
    """Add log-frequency grid lines matching directivity_plot style."""
    if detailed:
        ticks = _preferred_frequency_ticks(freq_min, freq_max)
        if ticks:
            ax.set_xticks(ticks)
        for freq in ticks:
            ax.axvline(freq, color='white', alpha=0.22, linewidth=0.7)
        for freq in _log_grid_lines(freq_min, freq_max):
            if not any(np.isclose(freq, tick, rtol=1e-6, atol=1e-6) for tick in ticks):
                ax.axvline(freq, color='white', alpha=0.08, linewidth=0.5)
        return

    for freq in _log_grid_lines(freq_min, freq_max):
        ax.axvline(freq, color='white', alpha=0.12, linewidth=0.5)


def _fig_to_base64(fig, dpi=150):
    """Export figure to base64-encoded PNG."""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, facecolor=fig.get_facecolor(),
                edgecolor='none', bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('ascii')


def render_frequency_response(frequencies, spl, dpi=150):
    """
    Render frequency response (SPL on-axis) chart.

    Args:
        frequencies: List of frequencies in Hz
        spl: List of SPL values in dB
        dpi: Image resolution

    Returns:
        Base64-encoded PNG string
    """
    freqs = np.array(frequencies, dtype=float)
    spl_vals = np.array(spl, dtype=float)

    if len(freqs) == 0 or len(spl_vals) == 0:
        return None

    fig, ax = plt.subplots(1, 1, figsize=(10, 4))
    fig.patch.set_facecolor('#1a1a1a')

    ax.semilogx(freqs, spl_vals, color='#4fc3f7', linewidth=1.5)

    _setup_dark_axes(ax, 'Frequency [Hz]', 'SPL [dB]', 'Frequency Response (On-Axis)')
    ax.xaxis.set_major_formatter(FuncFormatter(_freq_formatter))

    ax.set_xlim(freqs[0], freqs[-1])
    spl_min, spl_max = np.nanmin(spl_vals), np.nanmax(spl_vals)
    margin = max(2, (spl_max - spl_min) * 0.1)
    ax.set_ylim(spl_min - margin, spl_max + margin)

    _add_log_grid(ax, freqs[0], freqs[-1])

    fig.tight_layout(pad=1.5)
    return _fig_to_base64(fig, dpi)


def render_directivity_index(frequencies, di, dpi=150):
    """
    Render directivity index chart with per-plane traces.

    Args:
        frequencies: List of frequencies in Hz
        di: Either a flat list of DI values (legacy single-plane) or a dict
            mapping plane IDs to DI value lists, e.g.
            {"horizontal": [...], "vertical": [...], "diagonal": [...]}.
        dpi: Image resolution

    Returns:
        Base64-encoded PNG string
    """
    freqs = np.array(frequencies, dtype=float)
    if len(freqs) == 0:
        return None

    # Normalize input: accept both legacy flat list and per-plane dict
    plane_colors = {
        "horizontal": "#81c784",  # green
        "vertical":   "#64b5f6",  # blue
        "diagonal":   "#ffb74d",  # orange
    }
    plane_labels = {
        "horizontal": "H",
        "vertical":   "V",
        "diagonal":   "D",
    }

    if isinstance(di, dict):
        planes = {}
        for plane_id in ("horizontal", "vertical", "diagonal"):
            vals = di.get(plane_id)
            if vals and any(v is not None for v in vals):
                arr = np.array([v if v is not None else np.nan for v in vals], dtype=float)
                planes[plane_id] = arr
    elif isinstance(di, list) and len(di) > 0:
        arr = np.array([v if v is not None else np.nan for v in di], dtype=float)
        if not np.all(np.isnan(arr)):
            planes = {"horizontal": arr}
        else:
            return None
    else:
        return None

    if not planes:
        return None

    fig, ax = plt.subplots(1, 1, figsize=(10, 4))
    fig.patch.set_facecolor('#1a1a1a')

    all_vals = []
    for plane_id, di_vals in planes.items():
        color = plane_colors.get(plane_id, "#81c784")
        label = plane_labels.get(plane_id, plane_id.capitalize())
        ax.semilogx(freqs, di_vals, color=color, linewidth=1.5, label=label)
        valid = di_vals[~np.isnan(di_vals)]
        if len(valid) > 0:
            all_vals.extend(valid.tolist())

    if not all_vals:
        plt.close(fig)
        return None

    if len(planes) > 1:
        ax.legend(loc='upper left', fontsize=9, facecolor='#2a2a2a',
                  edgecolor='#555', labelcolor='white')

    _setup_dark_axes(ax, 'Frequency [Hz]', 'DI [dB]', 'Directivity Index')
    ax.xaxis.set_major_formatter(FuncFormatter(_freq_formatter))

    ax.set_xlim(freqs[0], freqs[-1])
    di_min, di_max = np.nanmin(all_vals), np.nanmax(all_vals)
    margin = max(2, (di_max - di_min) * 0.1)
    ax.set_ylim(min(0, di_min - margin), di_max + margin)

    _add_log_grid(ax, freqs[0], freqs[-1], detailed=True)
    ax.tick_params(axis='x', labelsize=8)

    fig.tight_layout(pad=1.5)
    return _fig_to_base64(fig, dpi)


def render_impedance(frequencies, real, imaginary, dpi=150):
    """
    Render acoustic impedance chart (real + imaginary).

    Args:
        frequencies: List of frequencies in Hz
        real: List of real impedance values
        imaginary: List of imaginary impedance values
        dpi: Image resolution

    Returns:
        Base64-encoded PNG string
    """
    freqs = np.array(frequencies, dtype=float)
    re_vals = np.array(real, dtype=float)
    im_vals = np.array(imaginary, dtype=float)

    if len(freqs) == 0 or len(re_vals) == 0:
        return None

    fig, ax = plt.subplots(1, 1, figsize=(10, 4))
    fig.patch.set_facecolor('#1a1a1a')

    ax.semilogx(freqs, re_vals, color='#64b5f6', linewidth=1.5, label='Re(Z)')
    if len(im_vals) > 0:
        ax.semilogx(freqs, im_vals, color='#ffb74d', linewidth=1.5, label='Im(Z)')

    _setup_dark_axes(ax, 'Frequency [Hz]', u'Z [\u03A9]', 'Acoustic Impedance')
    ax.xaxis.set_major_formatter(FuncFormatter(_freq_formatter))

    ax.set_xlim(freqs[0], freqs[-1])

    all_vals = np.concatenate([re_vals, im_vals]) if len(im_vals) > 0 else re_vals
    z_min, z_max = np.nanmin(all_vals), np.nanmax(all_vals)
    margin = max(50, (z_max - z_min) * 0.1)
    ax.set_ylim(z_min - margin, z_max + margin)

    _add_log_grid(ax, freqs[0], freqs[-1])

    legend = ax.legend(loc='upper right', fontsize=10,
                       facecolor='#2a2a2a', edgecolor='#555555',
                       labelcolor='#cccccc')
    legend.get_frame().set_alpha(0.9)

    fig.tight_layout(pad=1.5)
    return _fig_to_base64(fig, dpi)


def render_all_charts(payload, dpi=150):
    """
    Render all charts from a combined results payload.

    Args:
        payload: Dict with keys: frequencies, spl, di, di_frequencies,
                 impedance_frequencies, impedance_real, impedance_imaginary,
                 directivity
        dpi: Image resolution

    Returns:
        Dict with keys: frequency_response, directivity_index, impedance,
        directivity_map — each a base64 PNG or None
    """
    from .directivity_plot import render_directivity_plot

    freqs = payload.get('frequencies', [])
    spl = payload.get('spl', [])
    di_freqs = payload.get('di_frequencies', []) or freqs
    imp_freqs = payload.get('impedance_frequencies', []) or freqs
    imp_real = payload.get('impedance_real', [])
    imp_imag = payload.get('impedance_imaginary', [])
    directivity = payload.get('directivity', {})

    charts = {}

    charts['frequency_response'] = render_frequency_response(freqs, spl, dpi) if spl else None
    # DI can be a flat list (legacy) or per-plane dict
    di_input = payload.get('di', [])
    charts['directivity_index'] = render_directivity_index(di_freqs, di_input, dpi) if di_input else None
    charts['impedance'] = render_impedance(imp_freqs, imp_real, imp_imag, dpi) if imp_real else None

    dir_b64 = None
    if directivity and freqs:
        try:
            dir_b64 = render_directivity_plot(freqs, directivity, dpi)
        except Exception:
            pass
    charts['directivity_map'] = dir_b64

    return charts
