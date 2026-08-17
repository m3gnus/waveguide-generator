"""Dependency-free exception types shared across solver boundaries."""


class RecombineError(ValueError):
    """A user-addressable recombination refusal (maps to HTTP 422)."""
