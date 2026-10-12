"""Tests-only backend entry with a private installer retirement event."""

from __future__ import annotations

import os
from pathlib import Path
import sys

# Direct execution also works from an isolated HOME and working directory.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from server.solver.beat_runtime import retire  # noqa: E402
from server.tests.beat_runtime.fake_host_main import _test_retire_event_name  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    # Reject unsafe names before serve.py can create any retirement event.
    if os.name == "nt" or "WG2_TEST_RETIRE_EVENT" in os.environ:
        retire.RETIRE_IDLE_EVENT = _test_retire_event_name(os.environ.get("WG2_TEST_RETIRE_EVENT", ""))
    from launch import serve

    return serve.main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
