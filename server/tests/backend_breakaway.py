"""Environment admission for tests that start a detached backend."""

import sys

import pytest

from server.platform import process_tree


WINDOWS_BREAKAWAY_REFUSAL = (
    "The caller's Windows job does not allow backend breakaway. "
    "Start Waveguide Generator outside that job, or enable job breakaway."
)


def backend_breakaway_permitted() -> bool:
    """Use the owner's own job query; POSIX has no breakaway requirement."""
    if sys.platform != "win32":
        return True
    try:
        process_tree.windows_backend_breakaway_flags()
    except OSError as exc:
        if str(exc) == WINDOWS_BREAKAWAY_REFUSAL:
            return False
        raise  # A failed job query is not evidence that breakaway is forbidden.
    return True  # No job, explicit breakaway and silent breakaway all qualify.


def require_backend_breakaway() -> None:
    if not backend_breakaway_permitted():
        pytest.skip(
            "this runner's Windows job forbids backend breakaway; "
            "the refusal tests cover startup in that environment"
        )


def backend_spawn_refusal_message(executable: str) -> str:
    return (
        f"Could not start the backend ({executable!r}): {WINDOWS_BREAKAWAY_REFUSAL}. "
        "Open Waveguide Generator from the Start menu and try again, "
        "or run outside the CI/IDE job that forbids breakaway."
    )
