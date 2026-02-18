"""
Matplotlib-based directivity heatmap rendering.

Uses log-frequency interpolation and light fractional-octave smoothing to
produce stable, readable polar maps from per-frequency angle slices.
"""

import io
import base64
import numpy as np

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


MIN_DB = -30.0
MAX_DB = 0.0
FRACTIONAL_OCTAVE = 24.0
ANGLE_SAMPLES = 361
FREQ_SAMPLES = 500


def render_directivity_plot(frequencies, directivity, dpi=150, reference_level=-6.0):
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
    freqs = np.array(frequencies, dtype=float)
    if freqs.size == 0:
        return None

    planes = []
    for key in ("horizontal", "vertical", "diagonal"):
        patterns = directivity.get(key, [])
        if not patterns:
            continue
        angles_raw, freqs_raw, values_raw = _build_grid(freqs, patterns)
        if values_raw is None:
            continue
        angles, plane_freqs, values = _prepare_heatmap_data(angles_raw, freqs_raw, values_raw)
        planes.append({
            "key": key,
            "angles": angles,
            "freqs": plane_freqs,
            "values": values,
            "values_raw": values_raw,
        })

    if not planes:
        return None

    by_key = {entry["key"]: entry for entry in planes}
    has_only_hv = set(by_key.keys()) == {"horizontal", "vertical"}
    symmetric = has_only_hv and _check_symmetry(
        by_key["horizontal"]["values_raw"],
        by_key["vertical"]["values_raw"],
    )

    if symmetric:
        fig, axes = plt.subplots(1, 1, figsize=(11, 5))
        axes = [axes]
        titles = ["Directivity (H = V, Symmetric)"]
        datasets = [(
            by_key["horizontal"]["freqs"],
            by_key["horizontal"]["angles"],
            by_key["horizontal"]["values"],
        )]
    else:
        plane_count = len(planes)
        fig_height = 5 if plane_count == 1 else (4 * plane_count)
        fig, axes = plt.subplots(plane_count, 1, figsize=(11, fig_height))
        if not isinstance(axes, (list, np.ndarray)):
            axes = [axes]
        else:
            axes = list(np.atleast_1d(axes))
        titles = [_plane_title(entry["key"]) for entry in planes]
        datasets = [(entry["freqs"], entry["angles"], entry["values"]) for entry in planes]

    fig.patch.set_facecolor("#1a1a1a")
    for ax, title, (plot_freqs, plot_angles, plot_values) in zip(axes, titles, datasets):
        _render_single_heatmap(
            ax,
            plot_freqs,
            plot_angles,
            plot_values,
            title,
            reference_level=reference_level,
        )

    fig.tight_layout(pad=1.5)

    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=dpi,
        facecolor=fig.get_facecolor(),
        edgecolor="none",
        bbox_inches="tight",
    )
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _plane_title(key):
    if key == "horizontal":
        return "H Normalized Directivity"
    if key == "vertical":
        return "V Normalized Directivity"
    if key == "diagonal":
        return "D Normalized Directivity"
    return "Normalized Directivity"


def _build_grid(freqs, patterns):
    """
    Convert list of [[angle, dB], ...] per frequency into 2D arrays.

    Returns:
        angles: 1D array of angle values
        freqs: 1D array of frequencies (with empty columns removed)
        values: 2D array (n_angles x n_freqs)
    """
    if not patterns:
        return None, None, None

    n_freqs = min(len(patterns), len(freqs))
    if n_freqs == 0:
        return None, None, None

    angles = None
    for pattern in patterns[:n_freqs]:
        candidate = _extract_angles(pattern)
        if candidate is not None and candidate.size > 0:
            angles = candidate
            break
    if angles is None or angles.size == 0:
        return None, None, None

    values = np.full((angles.size, n_freqs), np.nan, dtype=float)
    for fi in range(n_freqs):
        pattern = patterns[fi]
        if not isinstance(pattern, list):
            continue
        for ai, point in enumerate(pattern[: angles.size]):
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            db = _safe_float(point[1])
            if db is not None:
                values[ai, fi] = db

    keep_cols = np.any(np.isfinite(values), axis=0)
    if not np.any(keep_cols):
        return None, None, None

    return angles, freqs[:n_freqs][keep_cols], values[:, keep_cols]


def _extract_angles(pattern):
    if not isinstance(pattern, list):
        return None
    out = []
    for point in pattern:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        ang = _safe_float(point[0])
        if ang is not None:
            out.append(ang)
    if not out:
        return None
    return np.array(out, dtype=float)


def _safe_float(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(out):
        return None
    return out


def _prepare_heatmap_data(angles, freqs, values):
    values_filled = _fill_missing_values(values)
    values_smooth = _fractional_octave_smooth(values_filled, freqs, FRACTIONAL_OCTAVE)
    interp_angles, interp_freqs, interp_values = _interpolate_heatmap_grid(
        angles, freqs, values_smooth, ANGLE_SAMPLES, FREQ_SAMPLES
    )
    return interp_angles, interp_freqs, np.clip(interp_values, MIN_DB, MAX_DB)


def _fill_missing_values(values):
    filled = np.array(values, dtype=float, copy=True)

    # Fill missing values along angle for each frequency.
    for col in range(filled.shape[1]):
        y = filled[:, col]
        finite = np.isfinite(y)
        if np.all(finite):
            continue
        if np.count_nonzero(finite) == 0:
            continue
        x = np.arange(y.size)
        if np.count_nonzero(finite) == 1:
            filled[:, col] = y[finite][0]
        else:
            filled[:, col] = np.interp(x, x[finite], y[finite])

    # Fill remaining gaps across frequency.
    for row in range(filled.shape[0]):
        y = filled[row, :]
        finite = np.isfinite(y)
        if np.all(finite):
            continue
        if np.count_nonzero(finite) == 0:
            filled[row, :] = MIN_DB
            continue
        x = np.arange(y.size)
        if np.count_nonzero(finite) == 1:
            filled[row, :] = y[finite][0]
        else:
            filled[row, :] = np.interp(x, x[finite], y[finite])

    filled[~np.isfinite(filled)] = MIN_DB
    return filled


def _fractional_octave_smooth(values, freqs, fraction):
    if fraction is None or fraction <= 0 or len(freqs) < 2:
        return values

    log2_freqs = np.log2(freqs)
    half_band = 1.0 / (2.0 * float(fraction))
    smoothed = np.empty_like(values)
    for i in range(freqs.size):
        mask = np.abs(log2_freqs - log2_freqs[i]) <= half_band
        smoothed[:, i] = np.mean(values[:, mask], axis=1)
    return smoothed


def _interpolate_heatmap_grid(angles, freqs, values, angle_samples, freq_samples):
    if len(angles) < 2 or len(freqs) < 2:
        return angles, freqs, values

    target_angles = np.linspace(float(angles[0]), float(angles[-1]), max(int(angle_samples), len(angles)))
    log_freqs = np.log10(freqs)
    target_log_freqs = np.linspace(
        float(log_freqs[0]),
        float(log_freqs[-1]),
        max(int(freq_samples), len(freqs)),
    )

    angle_interp = np.empty((target_angles.size, freqs.size), dtype=float)
    for i in range(freqs.size):
        angle_interp[:, i] = np.interp(target_angles, angles, values[:, i])

    full_interp = np.empty((target_angles.size, target_log_freqs.size), dtype=float)
    for j in range(target_angles.size):
        full_interp[j, :] = np.interp(target_log_freqs, log_freqs, angle_interp[j, :])

    return target_angles, np.power(10.0, target_log_freqs), full_interp


def _check_symmetry(h_values, v_values):
    """Check if H and V patterns are effectively identical."""
    if h_values is None or v_values is None:
        return False
    if h_values.shape != v_values.shape:
        return False

    finite = np.isfinite(h_values) & np.isfinite(v_values)
    if not np.any(finite):
        return False

    h_ref = np.nanmax(np.abs(h_values[finite]))
    v_ref = np.nanmax(np.abs(v_values[finite]))
    scale = max(h_ref, v_ref, 1e-9)
    rel_diff = np.nanmax(np.abs(h_values[finite] - v_values[finite])) / scale
    return rel_diff < 0.01


def _render_single_heatmap(ax, freqs, angles, values, title, reference_level=-6.0):
    """Render a single directivity heatmap on the given axes."""
    ax.set_facecolor("#1a1a1a")

    log_freqs = np.log10(freqs)
    if len(log_freqs) > 1:
        d_log = np.diff(log_freqs)
        freq_edges = np.zeros(len(freqs) + 1)
        freq_edges[0] = 10 ** (log_freqs[0] - d_log[0] / 2)
        freq_edges[-1] = 10 ** (log_freqs[-1] + d_log[-1] / 2)
        for i in range(1, len(freqs)):
            freq_edges[i] = 10 ** ((log_freqs[i - 1] + log_freqs[i]) / 2)
    else:
        freq_edges = np.array([freqs[0] * 0.9, freqs[0] * 1.1])

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
        freq_edges,
        angle_edges,
        values,
        cmap="viridis",
        vmin=MIN_DB,
        vmax=MAX_DB,
        shading="flat",
    )

    X, Y = np.meshgrid(freqs, angles)
    contour_levels = [-24, -18, -12, -9, -6, -3]
    try:
        ax.contour(
            X,
            Y,
            values,
            levels=contour_levels,
            colors="white",
            linewidths=0.6,
            alpha=0.35,
        )
    except Exception:
        pass

    try:
        ref_contour = ax.contour(
            X,
            Y,
            values,
            levels=[reference_level],
            colors="white",
            linewidths=1.5,
        )
    except Exception:
        ref_contour = None

    ax.set_xscale("log")
    ax.set_xlim(freqs[0], freqs[-1])
    ax.set_ylim(angles[0], angles[-1])

    for freq in _log_grid_lines(freqs[0], freqs[-1]):
        ax.axvline(freq, color="white", alpha=0.15, linewidth=0.5)

    angle_range = angles[-1] - angles[0]
    if angle_range > 120:
        angle_step = 30
    elif angle_range > 60:
        angle_step = 15
    else:
        angle_step = 10
    start = np.ceil(angles[0] / angle_step) * angle_step
    for a in np.arange(start, angles[-1] + angle_step * 0.5, angle_step):
        if angles[0] < a < angles[-1]:
            ax.axhline(a, color="white", alpha=0.15, linewidth=0.5)

    ax.xaxis.set_major_formatter(FuncFormatter(_freq_formatter))
    ax.set_xlabel("Frequency [Hz]", color="#cccccc", fontsize=11)
    ax.set_ylabel("Angle [deg]", color="#cccccc", fontsize=11)
    ax.set_title(title, color="#e0e0e0", fontsize=13, fontweight="600", pad=8)
    ax.tick_params(colors="#aaaaaa", labelsize=9)

    for spine in ax.spines.values():
        spine.set_color("#444444")

    cbar = plt.colorbar(mesh, ax=ax, shrink=0.85, pad=0.02)
    cbar.set_label("dB", color="#cccccc", fontsize=10)
    cbar.ax.tick_params(colors="#aaaaaa", labelsize=9)
    cbar.outline.set_edgecolor("#444444")

    if ref_contour is not None:
        from matplotlib.lines import Line2D

        legend_line = Line2D([0], [0], color="white", linewidth=1.5, label=f"ref @ {reference_level:g} dB")
        ax.legend(
            handles=[legend_line],
            loc="upper right",
            fontsize=8,
            facecolor="#2a2a2a",
            edgecolor="#555555",
            labelcolor="#cccccc",
            framealpha=0.85,
        )


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
            return f"{int(x / 1000)}k"
        return f"{x / 1000:.1f}k"
    return f"{int(x)}"
