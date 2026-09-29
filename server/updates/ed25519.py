"""Verify-only Ed25519 (RFC 8032, section 6 reference implementation).

Vendored so update verification needs no new dependency. Nothing here signs:
signing happens in the release workflow's ``update-signing`` job with OpenSSL.
Verification handles only public data, so the reference implementation's lack of
constant-time arithmetic does not matter. Tested against the RFC 8032 vectors in
``server/tests/test_updates_signing.py``.

Kept free of package-relative imports so ``scripts/release_manifest.py`` can load
it by path on a bare release runner.
"""

from __future__ import annotations

import hashlib

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


_D = -121665 * _inv(121666) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_Point = tuple[int, int, int, int]
_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
assert _GX is not None
_G: _Point = (_GX, _GY, 1, _GX * _GY % _P)
_ZERO: _Point = (0, 1, 1, 0)


def _add(p: _Point, q: _Point) -> _Point:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p: _Point) -> _Point:
    q = _ZERO
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(p: _Point, q: _Point) -> bool:
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (
        p[1] * q[2] - q[1] * p[2]
    ) % _P == 0


def _decompress(s: bytes) -> _Point | None:
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _challenge(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Return True only for a valid signature; never raises on malformed input."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    if a is None:
        return False
    r_bytes = signature[:32]
    r = _decompress(r_bytes)
    if r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = _challenge(r_bytes + public_key + message)
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))
