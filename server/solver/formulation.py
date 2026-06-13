from __future__ import annotations

from typing import Any


DEFAULT_BEM_FORMULATION = "complex_k"
DEFAULT_COMPLEX_K_SHIFT = 0.005


def formulation_from_request(
    request: Any,
    *,
    allow_burton_miller: bool,
) -> str:
    advanced = getattr(request, "advanced_settings", None)
    raw = getattr(advanced, "bem_formulation", None)
    if raw is None and getattr(advanced, "use_burton_miller", False):
        raw = "burton_miller"
    formulation = str(raw or DEFAULT_BEM_FORMULATION).strip().lower().replace("-", "_")
    if formulation == "burton_miller" and not allow_burton_miller:
        raise ValueError("Burton-Miller formulation is not supported by the Metal backend.")
    if formulation not in {"standard", "complex_k", "burton_miller"}:
        raise ValueError("BEM formulation must be one of: standard, complex_k, burton_miller.")
    return formulation


def complex_k_shift_from_request(request: Any) -> float:
    advanced = getattr(request, "advanced_settings", None)
    raw = getattr(advanced, "complex_k_shift", None)
    if raw is None:
        return DEFAULT_COMPLEX_K_SHIFT
    return float(raw)
