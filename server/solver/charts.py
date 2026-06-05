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


def _coerce_float_array(values):
    """Convert a possibly sparse list to float array with invalid values as NaN."""
    coerced = []
    if values is None:
        values = []
    for value in values:
        if value is None:
            coerced.append(np.nan)
            continue
        try:
            coerced.append(float(value))
        except (TypeError, ValueError):
            coerced.append(np.nan)
    return np.asarray(coerced, dtype=float)


def _finite_xy(frequencies, values):
    """Return finite positive-frequency x/y pairs trimmed to matching length."""
    freqs = _coerce_float_array(frequencies)
    vals = _coerce_float_array(values)
    if freqs.size == 0 or vals.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    n = min(freqs.size, vals.size)
    freqs = freqs[:n]
    vals = vals[:n]
    finite = np.isfinite(freqs) & np.isfinite(vals) & (freqs > 0)
    return freqs[finite], vals[finite]


def _unwrap_degrees(values):
    """Unwrap phase degrees for a continuous response trace."""
    vals = np.asarray(values, dtype=float)
    if vals.size == 0:
        return vals
    return np.rad2deg(np.unwrap(np.deg2rad(vals)))


def render_frequency_response(frequencies, spl, phase_degrees=None, dpi=150):
    """
    Render frequency response (SPL on-axis) chart.

    Args:
        frequencies: List of frequencies in Hz
        spl: List of SPL values in dB
        phase_degrees: Optional list of on-axis pressure phase values in degrees
        dpi: Image resolution

    Returns:
        Base64-encoded PNG string
    """
    freqs, spl_vals = _finite_xy(frequencies, spl)
    phase_freqs, phase_vals = _finite_xy(frequencies, phase_degrees)

    if len(freqs) == 0 and len(phase_freqs) == 0:
        return None

    fig, ax = plt.subplots(1, 1, figsize=(10, 4))
    fig.patch.set_facecolor('#1a1a1a')

    if len(freqs) > 0:
        ax.semilogx(freqs, spl_vals, color='#4fc3f7', linewidth=1.5, label='SPL')

    _setup_dark_axes(ax, 'Frequency [Hz]', 'SPL [dB]', 'Frequency Response (On-Axis)')
    ax.xaxis.set_major_formatter(FuncFormatter(_freq_formatter))

    all_freqs = np.concatenate([arr for arr in (freqs, phase_freqs) if len(arr) > 0])
    freq_min, freq_max = float(np.nanmin(all_freqs)), float(np.nanmax(all_freqs))
    ax.set_xlim(freq_min, freq_max)

    if len(spl_vals) > 0:
        spl_min, spl_max = np.nanmin(spl_vals), np.nanmax(spl_vals)
        margin = max(2, (spl_max - spl_min) * 0.1)
        ax.set_ylim(spl_min - margin, spl_max + margin)

    phase_ax = None
    if len(phase_freqs) > 0:
        phase_ax = ax.twinx()
        phase_ax.set_facecolor('none')
        phase_ax.set_ylabel('Phase [deg]', color='#ffb74d', fontsize=11)
        phase_ax.tick_params(axis='y', colors='#ffb74d', labelsize=9)
        phase_ax.spines['right'].set_color('#ffb74d')
        phase_ax.spines['top'].set_color('#333333')
        phase_ax.semilogx(
            phase_freqs,
            _unwrap_degrees(phase_vals),
            color='#ffb74d',
            linewidth=1.25,
            linestyle='--',
            label='Phase',
        )

    _add_log_grid(ax, freq_min, freq_max)

    if phase_ax is not None:
        handles, labels = ax.get_legend_handles_labels()
        phase_handles, phase_labels = phase_ax.get_legend_handles_labels()
        legend = ax.legend(
            handles + phase_handles,
            labels + phase_labels,
            loc='best',
            fontsize=9,
            facecolor='#2a2a2a',
            edgecolor='#555555',
            labelcolor='#cccccc',
        )
        legend.get_frame().set_alpha(0.9)

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
    freqs = _coerce_float_array(frequencies)
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

    if isinstance(di, dict) and isinstance(di.get("di"), dict):
        di = di.get("di")

    if isinstance(di, dict):
        planes = {}
        for plane_id in ("horizontal", "vertical", "diagonal"):
            vals = di.get(plane_id)
            if vals is not None and len(vals) > 0 and any(v is not None for v in vals):
                plane_freqs, arr = _finite_xy(freqs, vals)
                if len(arr) > 0:
                    planes[plane_id] = (plane_freqs, arr)
    elif isinstance(di, list) and len(di) > 0:
        plane_freqs, arr = _finite_xy(freqs, di)
        if len(arr) > 0:
            planes = {"horizontal": (plane_freqs, arr)}
        else:
            return None
    else:
        return None

    if not planes:
        return None

    fig, ax = plt.subplots(1, 1, figsize=(10, 4))
    fig.patch.set_facecolor('#1a1a1a')

    all_vals = []
    all_freqs = []
    for plane_id, (plane_freqs, di_vals) in planes.items():
        color = plane_colors.get(plane_id, "#81c784")
        label = plane_labels.get(plane_id, plane_id.capitalize())
        ax.semilogx(plane_freqs, di_vals, color=color, linewidth=1.5, label=label)
        all_freqs.extend(plane_freqs.tolist())
        all_vals.extend(di_vals.tolist())

    if not all_vals:
        plt.close(fig)
        return None

    if len(planes) > 1:
        ax.legend(loc='upper left', fontsize=9, facecolor='#2a2a2a',
                  edgecolor='#555', labelcolor='white')

    _setup_dark_axes(ax, 'Frequency [Hz]', 'DI [dB]', 'Directivity Index')
    ax.xaxis.set_major_formatter(FuncFormatter(_freq_formatter))

    freq_min, freq_max = min(all_freqs), max(all_freqs)
    ax.set_xlim(freq_min, freq_max)
    di_min, di_max = np.nanmin(all_vals), np.nanmax(all_vals)
    margin = max(2, (di_max - di_min) * 0.1)
    ax.set_ylim(min(0, di_min - margin), di_max + margin)

    _add_log_grid(ax, freq_min, freq_max, detailed=True)
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
    re_freqs, re_vals = _finite_xy(frequencies, real)
    im_freqs, im_vals = _finite_xy(frequencies, imaginary)

    if len(re_freqs) == 0 and len(im_freqs) == 0:
        return None

    fig, ax = plt.subplots(1, 1, figsize=(10, 4))
    fig.patch.set_facecolor('#1a1a1a')

    if len(re_freqs) > 0:
        ax.semilogx(re_freqs, re_vals, color='#64b5f6', linewidth=1.5, label='Re(Z)')
    if len(im_vals) > 0:
        ax.semilogx(im_freqs, im_vals, color='#ffb74d', linewidth=1.5, label='Im(Z)')

    _setup_dark_axes(ax, 'Frequency [Hz]', 'Z [Pa.s/m]', 'Acoustic Impedance')
    ax.xaxis.set_major_formatter(FuncFormatter(_freq_formatter))

    all_freqs = np.concatenate([arr for arr in (re_freqs, im_freqs) if len(arr) > 0])
    freq_min, freq_max = float(np.nanmin(all_freqs)), float(np.nanmax(all_freqs))
    ax.set_xlim(freq_min, freq_max)

    all_vals = np.concatenate([arr for arr in (re_vals, im_vals) if len(arr) > 0])
    z_min, z_max = np.nanmin(all_vals), np.nanmax(all_vals)
    margin = max(50, (z_max - z_min) * 0.1)
    ax.set_ylim(z_min - margin, z_max + margin)

    _add_log_grid(ax, freq_min, freq_max)

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
    phase_degrees = payload.get('phase_degrees', [])
    di_freqs = payload.get('di_frequencies', []) or freqs
    imp_freqs = payload.get('impedance_frequencies', []) or freqs
    imp_real = payload.get('impedance_real', [])
    imp_imag = payload.get('impedance_imaginary', [])
    directivity = payload.get('directivity', {})

    charts = {}

    charts['frequency_response'] = (
        render_frequency_response(freqs, spl, phase_degrees, dpi)
        if spl or phase_degrees
        else None
    )
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
