"""
Matplotlib-based directivity heatmap rendering.

Produces publication-quality directivity plots with:
- Viridis colormap, -20 to 0 dB range
- Prominent white reference contour at configurable dB level (default -6)
- Subtle contour lines at -3, -6, -9, -12 dB
- Log frequency axis with sub-decade grid
- H and V subplots (or single plot if symmetric)
"""

import io
import base64
import numpy as np

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


def render_directivity_plot(frequencies, directivity, dpi=150,
                            reference_level=-6.0):
    """
    Render directivity heatmap(s) as a PNG image.

    Args:
        frequencies: List of frequencies in Hz (one per pattern entry)
        directivity: Dict with keys 'horizontal', 'vertical', 'diagonal'.
            Each is a list of [[angle_deg, dB], ...] per frequency.
        dpi: Image resolution
        reference_level: Reference dB level for prominent contour (default -6)

    Returns:
        Base64-encoded PNG string (without data URI prefix)
    """
    h_patterns = directivity.get('horizontal', [])
    v_patterns = directivity.get('vertical', [])

    if not h_patterns:
        return None

    freqs = np.array(frequencies, dtype=float)

    # Build 2D grids
    h_angles, h_values = _build_grid(freqs, h_patterns)
    v_angles, v_values = _build_grid(freqs, v_patterns) if v_patterns else (None, None)

    # Detect symmetry (H == V)
    symmetric = _check_symmetry(h_values, v_values)

    # Create figure — larger for better detail
    if symmetric or v_values is None:
        fig, axes = plt.subplots(1, 1, figsize=(11, 5))
        axes = [axes]
        titles = ['Directivity (H = V, Symmetric)' if symmetric else 'H Normalized Directivity']
        all_angles = [h_angles]
        all_values = [h_values]
    else:
        fig, axes = plt.subplots(2, 1, figsize=(11, 8))
        axes = list(axes)
        titles = ['H Normalized Directivity', 'V Normalized Directivity']
        all_angles = [h_angles, v_angles]
        all_values = [h_values, v_values]

    # Dark background
    fig.patch.set_facecolor('#1a1a1a')

    for ax, title, angles, values in zip(axes, titles, all_angles, all_values):
        _render_single_heatmap(ax, freqs, angles, values, title,
                               reference_level=reference_level)

    fig.tight_layout(pad=1.5)

    # Export to PNG
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, facecolor=fig.get_facecolor(),
                edgecolor='none', bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('ascii')


def _build_grid(freqs, patterns):
    """
    Convert list of [[angle, dB], ...] per frequency into 2D arrays.

    Returns:
        angles: 1D array of angle values
        values: 2D array (n_angles x n_freqs)
    """
    if not patterns or not patterns[0]:
        return None, None

    # Extract angles from first pattern
    angles = np.array([p[0] for p in patterns[0]], dtype=float)
    n_angles = len(angles)
    n_freqs = len(patterns)

    values = np.full((n_angles, n_freqs), np.nan)
    for fi, pattern in enumerate(patterns):
        for ai, point in enumerate(pattern):
            if ai < n_angles:
                values[ai, fi] = point[1]

    return angles, values


def _check_symmetry(h_values, v_values):
    """Check if H and V patterns are identical within 1% tolerance."""
    if v_values is None or h_values is None:
        return False
    if h_values.shape != v_values.shape:
        return False

    max_val = max(np.nanmax(np.abs(h_values)), np.nanmax(np.abs(v_values)))
    if max_val < 1e-10:
        return True

    relative_diff = np.nanmax(np.abs(h_values - v_values)) / max_val
    return relative_diff < 0.01


def _render_single_heatmap(ax, freqs, angles, values, title,
                            reference_level=-6.0):
    """Render a single directivity heatmap on the given axes."""
    ax.set_facecolor('#1a1a1a')

    # Pcolormesh — needs edge arrays for correct cell boundaries
    # Use log-spaced frequency edges
    log_freqs = np.log10(freqs)
    # Build bin edges in log space, then convert back
    if len(log_freqs) > 1:
        d_log = np.diff(log_freqs)
        freq_edges = np.zeros(len(freqs) + 1)
        freq_edges[0] = 10 ** (log_freqs[0] - d_log[0] / 2)
        freq_edges[-1] = 10 ** (log_freqs[-1] + d_log[-1] / 2)
        for i in range(1, len(freqs)):
            freq_edges[i] = 10 ** ((log_freqs[i - 1] + log_freqs[i]) / 2)
    else:
        freq_edges = np.array([freqs[0] * 0.9, freqs[0] * 1.1])

    # Angle edges
    if len(angles) > 1:
        d_ang = np.diff(angles)
        angle_edges = np.zeros(len(angles) + 1)
        angle_edges[0] = angles[0] - d_ang[0] / 2
        angle_edges[-1] = angles[-1] + d_ang[-1] / 2
        for i in range(1, len(angles)):
            angle_edges[i] = (angles[i - 1] + angles[i]) / 2
    else:
        angle_edges = np.array([angles[0] - 1, angles[0] + 1])

    mesh = ax.pcolormesh(
        freq_edges, angle_edges, values,
        cmap='viridis',
        vmin=-20,
        vmax=0,
        shading='flat'
    )

    # Contour lines — subtle lines at standard levels, prominent at reference
    X, Y = np.meshgrid(freqs, angles)
    contour_levels = [-12, -9, -6, -3]

    # Draw subtle contour lines at all standard levels
    try:
        ax.contour(
            X, Y, values,
            levels=contour_levels,
            colors='white',
            linewidths=0.6,
            alpha=0.4
        )
    except Exception:
        pass

    # Draw prominent reference contour
    try:
        ref_contour = ax.contour(
            X, Y, values,
            levels=[reference_level],
            colors='white',
            linewidths=1.5
        )
    except Exception:
        ref_contour = None

    # Log frequency axis
    ax.set_xscale('log')
    ax.set_xlim(freqs[0], freqs[-1])
    ax.set_ylim(angles[0], angles[-1])

    # Grid lines at log decade boundaries
    for freq in _log_grid_lines(freqs[0], freqs[-1]):
        ax.axvline(freq, color='white', alpha=0.15, linewidth=0.5)

    # Horizontal grid lines at angle ticks
    angle_range = angles[-1] - angles[0]
    if angle_range > 120:
        angle_step = 30
    elif angle_range > 60:
        angle_step = 15
    else:
        angle_step = 10
    for a in np.arange(0, angles[-1] + 1, angle_step):
        if angles[0] < a < angles[-1]:
            ax.axhline(a, color='white', alpha=0.15, linewidth=0.5)

    # Frequency tick formatting
    ax.xaxis.set_major_formatter(FuncFormatter(_freq_formatter))

    # Labels and title
    ax.set_xlabel('Frequency [Hz]', color='#cccccc', fontsize=11)
    ax.set_ylabel('Angle [deg]', color='#cccccc', fontsize=11)
    ax.set_title(title, color='#e0e0e0', fontsize=13, fontweight='600', pad=8)

    # Tick styling
    ax.tick_params(colors='#aaaaaa', labelsize=9)

    # Spine styling
    for spine in ax.spines.values():
        spine.set_color('#444444')

    # Colorbar
    cbar = plt.colorbar(mesh, ax=ax, shrink=0.85, pad=0.02)
    cbar.set_label('dB', color='#cccccc', fontsize=10)
    cbar.ax.tick_params(colors='#aaaaaa', labelsize=9)
    cbar.outline.set_edgecolor('#444444')

    # Legend for reference contour
    if ref_contour is not None:
        from matplotlib.lines import Line2D
        legend_line = Line2D([0], [0], color='white', linewidth=1.5,
                             label=f'ref @ {reference_level:g} dB')
        ax.legend(handles=[legend_line], loc='upper right',
                  fontsize=8, facecolor='#2a2a2a', edgecolor='#555555',
                  labelcolor='#cccccc', framealpha=0.85)


def _log_grid_lines(freq_min, freq_max):
    """Generate frequencies at decade sub-boundaries: 1, 2, 3, 5 x 10^n."""
    min_log = np.log10(freq_min)
    max_log = np.log10(freq_max)
    lines = []
    for decade in range(int(np.floor(min_log)), int(np.ceil(max_log)) + 1):
        for mantissa in [1, 2, 3, 5]:
            freq = mantissa * (10 ** decade)
            if freq_min <= freq <= freq_max:
                lines.append(freq)
    return sorted(set(lines))


def _freq_formatter(x, pos):
    """Format frequency ticks: 100, 200, 1k, 2k, 10k, 20k."""
    if x >= 1000:
        if x % 1000 == 0:
            return f'{int(x / 1000)}k'
        return f'{x / 1000:.1f}k'
    return f'{int(x)}'
