"""Reuse same-process request validation without weakening engine negotiation."""

from __future__ import annotations

from types import FunctionType
from typing import Any


def validated_negotiator(contract: Any, request: dict) -> Any:
    """Validate once, then retain every engine version/capability check.

    The pinned engine has no prevalidated argument. Bind an isolated copy of
    its Python negotiation function to a request-specific validator, leaving
    the installed module and host trust boundary untouched. Unknown API shapes
    fall back to ordinary negotiation (and its redundant validation).
    Callers must submit this exact request without mutating it after this call.
    """
    contract.validate_solve_request(request)
    negotiate = contract.negotiate_submission
    if not isinstance(negotiate, FunctionType) or "validate_solve_request" not in negotiate.__code__.co_names:
        return negotiate

    def already_validated(candidate: dict) -> None:
        if candidate is not request:
            contract.validate_solve_request(candidate)

    namespace = dict(negotiate.__globals__, validate_solve_request=already_validated)
    rebuilt = FunctionType(negotiate.__code__, namespace, negotiate.__name__,
                        negotiate.__defaults__, negotiate.__closure__)
    rebuilt.__kwdefaults__ = dict(negotiate.__kwdefaults__) if negotiate.__kwdefaults__ is not None else None
    return rebuilt
