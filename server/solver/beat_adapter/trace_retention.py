"""Optional coupled BEM trace retention budget, independent of job storage."""

MAX_BEM_TRACE_BYTES = 256 * 1024 * 1024


def check_trace_size(frequencies: int, channels: int, nodes: int, faces: int, precision: str) -> None:
    size = frequencies * channels * (nodes + faces) * (8 if precision == "float32" else 16)
    # Include frequency-invariant coordinates and connectivity in the same cap.
    size += nodes * 3 * 8 + faces * 3 * 8 + 64 * 1024
    if size > MAX_BEM_TRACE_BYTES:
        raise ValueError("Coupled BEM traces exceed the 256 MiB retention limit; reduce the mesh or frequency sweep")


