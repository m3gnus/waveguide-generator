"""Real solver adapters and v1-compatible result mapping.

Adapters stay lazily imported by ``server.engines.registry`` so mesh helpers can
import the shared quadrant module without creating a mesh↔engine import cycle.
"""

__all__: list[str] = []
