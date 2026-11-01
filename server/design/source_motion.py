"""The design's prescribed source motion, shared by every solve route."""

from server.design.schema import SourceConfig


def source_motion(source: SourceConfig) -> str:
    """Explicit conventions win; omitted and legacy conventions use Velocity."""

    if source.velocity_convention in {"normal", "axial"}:
        return source.velocity_convention
    velocity = 1.0 if source.velocity is None else source.velocity.constant_value()
    if velocity is None:
        raise ValueError("solver control must be a finite scalar expression")
    if velocity not in {1.0, 2.0}:
        raise ValueError("source.velocity must be 1 (normal) or 2 (axial)")
    return "axial" if velocity == 2.0 else "normal"
