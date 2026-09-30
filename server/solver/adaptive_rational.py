"""Small set-valued AAA implementation; no engine or application imports.

Shared scalar barycentric denominator, one numerator per output channel. Only supplied
sample values enter fitting, selection, scaling, and cleanup.
"""

from dataclasses import dataclass
import numpy as np
from scipy.linalg import eigvals


@dataclass
class Rational:
    support: np.ndarray
    values: np.ndarray
    weights: np.ndarray
    removed: list

    def __call__(self, x):
        x = np.atleast_1d(x).astype(complex)
        delta = x[:, None] - self.support
        exact = delta == 0
        with np.errstate(divide="ignore", invalid="ignore"):
            c = 1 / delta
            result = (c @ (self.weights[:, None] * self.values)) / (c @ self.weights)[:, None]
        rows, cols = np.where(exact)
        result[rows] = self.values[cols]
        return result

    def poles_residues(self):
        n = len(self.support)
        if n <= 1:
            return np.array([], complex), np.empty((0, self.values.shape[1]), complex)
        e = np.zeros((n + 1, n + 1), complex)
        e[0, 1:] = self.weights
        e[1:, 0] = 1
        e[1:, 1:] = np.diag(self.support)
        b = np.eye(n + 1)
        b[0, 0] = 0
        poles = eigvals(e, b)
        poles = poles[np.isfinite(poles)]
        c = 1 / (poles[:, None] - self.support)
        residues = (c @ (self.weights[:, None] * self.values)) / (-(c * c) @ self.weights)[:, None]
        return poles, residues


def _weights(x, y, indices):
    other = np.setdiff1d(np.arange(len(x)), indices)
    if len(indices) == 1:
        return np.ones(1, complex)
    if not len(other):
        raise ValueError("AAA needs at least one non-support sample")
    c = 1 / (x[other, None] - x[np.asarray(indices)][None, :])
    # Stack one Loewner block per channel; the right singular vector is shared.
    # QR compression bounds memory when retained boundary traces contribute
    # thousands of channels. It preserves the stacked least-squares problem
    # without forming normal equations (which square its condition number).
    reduced = None
    for start in range(0, y.shape[1], 128):
        block = y[:, start : start + 128]
        a = (block[other, None, :] - block[np.asarray(indices)][None, :, :]) * c[:, :, None]
        a = a.transpose(0, 2, 1).reshape(-1, len(indices))
        if y.shape[1] <= 128:
            reduced = a
        else:
            part = np.linalg.qr(a, mode="r")
            reduced = (
                part if reduced is None else np.linalg.qr(np.vstack([reduced, part]), mode="r")
            )
    _, _, vh = np.linalg.svd(reduced, full_matrices=reduced.shape[0] < reduced.shape[1])
    return vh[-1].conj()


def aaa(x, y, max_support=30, sample_tolerance=1e-7, cleanup=1e-8):
    x = np.asarray(x, float)
    y = np.asarray(y, complex)
    if y.ndim == 1:
        y = y[:, None]
    if len(x) < 2:
        return Rational(x.copy(), y.copy(), np.ones(len(x)), [])
    max_support = min(max_support, len(x) - 1)
    prediction = np.broadcast_to(y.mean(axis=0), y.shape).copy()
    indices = []
    for _ in range(max_support):
        error = np.max(np.abs(y - prediction), axis=1)
        error[indices] = -1
        indices.append(int(np.argmax(error)))
        model = Rational(x[indices], y[indices], _weights(x, y, indices), [])
        prediction = model(x)
        if np.max(np.abs(prediction - y)) < sample_tolerance:
            break
    # Froissart candidates: weak vector residue divided by nearest sample distance.
    # Remove a support only when refitting does not spoil the observed samples.
    removed = []
    for _ in range(len(indices) - 1):
        poles, residue = model.poles_residues()
        if not len(poles):
            break
        distance = np.min(np.abs(poles[:, None] - x), axis=1)
        score = np.max(np.abs(residue), axis=1) / np.maximum(distance, 1e-14)
        candidates = np.flatnonzero(score < cleanup)
        if not len(candidates):
            break
        pole = poles[candidates[np.argmin(score[candidates])]]
        nearest = int(np.argmin(np.abs(x[indices] - pole)))
        trial_indices = indices[:nearest] + indices[nearest + 1 :]
        trial = Rational(x[trial_indices], y[trial_indices], _weights(x, y, trial_indices), [])
        allowed = max(sample_tolerance * 5, np.max(np.abs(model(x) - y)) * 2, cleanup * 5)
        if np.max(np.abs(trial(x) - y)) > allowed:
            break
        removed.append([float(pole.real), float(pole.imag)])
        indices = trial_indices
        model = trial
    model.removed = removed
    return model


class SweepModel:
    """Normalize channels and remove known propagation delay before shared AAA.

    The native WG contract uses exp(-i omega t), outgoing exp(+ikr).
    Remove that delay with exp(-ikr). Surface quantities have zero delay.
    ``time_sign=+1`` supports the conjugate positive-time convention.
    """

    def __init__(
        self, frequencies, values, band, max_support, delays_s, time_sign=-1, physical=False
    ):
        self.center = (band[0] + band[1]) / 2
        self.span = (band[1] - band[0]) / 2
        self.time_sign = time_sign
        self.delays_s = np.asarray(delays_s)
        data = np.array(values, complex, copy=True)
        data *= np.exp(time_sign * 2j * np.pi * np.asarray(frequencies)[:, None] * self.delays_s)
        self.scale = np.maximum(np.max(np.abs(data), axis=0), 1e-30)
        self.fit = aaa(
            (np.asarray(frequencies) - self.center) / self.span,
            data / self.scale,
            max_support=max_support,
        )

        if physical and not self.safe():
            # Stabilize the surrogate denominator, never the underlying BEM
            # solve: reflect growing poles, then refit all vector numerators
            # on observed samples. The planner checks the resulting residual.
            poles, _ = self.fit.poles_residues()
            bad = (poles.real >= -1) & (poles.real <= 1) & (self.time_sign * poles.imag <= 1e-10)
            poles[bad] = poles[bad].real + 1j * self.time_sign * np.maximum(
                abs(poles[bad].imag), 1e-6
            )
            supports = self.fit.support
            weights = np.array(
                [
                    np.prod(point - poles) / np.prod(point - np.delete(supports, i))
                    for i, point in enumerate(supports)
                ]
            )
            weights /= np.max(abs(weights))
            x = (np.asarray(frequencies) - self.center) / self.span
            delta = x[:, None] - supports
            exact = delta == 0
            with np.errstate(divide="ignore", invalid="ignore"):
                basis = weights[None, :] / delta
                basis /= basis.sum(axis=1)[:, None]
            for row, col in zip(*np.where(exact), strict=True):
                basis[row] = 0
                basis[row, col] = 1
            values = np.linalg.lstsq(basis, data / self.scale, rcond=None)[0]
            self.fit = Rational(supports, values, weights, self.fit.removed)

    def __call__(self, frequencies):
        f = np.atleast_1d(frequencies)
        y = self.fit((f - self.center) / self.span) * self.scale
        return y * np.exp(-self.time_sign * 2j * np.pi * f[:, None] * self.delays_s)

    def safe(self):
        # Physical damped poles lie on the time-sign side of the real
        # axis. Reject the opposite half-plane and near-real poles after the
        # vector Froissart cleanup; sample agreement alone cannot certify them.
        poles, _ = self.fit.poles_residues()
        in_band = (poles.real >= -1) & (poles.real <= 1)
        return not np.any(in_band & (self.time_sign * poles.imag <= 1e-10))
