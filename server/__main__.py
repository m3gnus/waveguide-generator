"""Forward ``python -m server`` to the supported local launcher."""

from __future__ import annotations

from launch.serve import main


if __name__ == "__main__":
    raise SystemExit(main())
