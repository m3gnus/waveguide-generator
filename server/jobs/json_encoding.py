"""Bounded JSON encoding shared by result persistence and streaming routes."""

from collections.abc import Iterator, Mapping
import json
import time
from typing import Any


def _small_numeric_array_size(value: Any, budget: int = 2048) -> int | None:
    """Bound fast encoding of sampled numeric rows, including [angle, value]."""
    if isinstance(value, (int, float, type(None))):
        return 1
    if not isinstance(value, (list, tuple)):
        return None
    size = 1
    for member in value:
        remaining = budget - size
        if remaining <= 0:
            return None
        member_size = _small_numeric_array_size(member, remaining)
        if member_size is None or member_size > remaining:
            return None
        size += member_size
    return size


def _json_tokens(value: Any, separators: tuple[str, str]) -> Iterator[str]:
    """Yield JSON without encoding one unbounded string or array at once."""
    comma, colon = separators
    if isinstance(value, str):
        yield '"'
        for offset in range(0, len(value), 64 * 1024):
            yield json.dumps(value[offset:offset + 64 * 1024], allow_nan=False)[1:-1]
        yield '"'
    elif isinstance(value, Mapping):
        yield "{"
        for index, (key, member) in enumerate(value.items()):
            if index:
                yield comma
            if isinstance(key, str):
                yield from _json_tokens(key, separators)
            else:
                # Let the standard encoder enforce its key types and spelling.
                yield json.dumps({key: None}, allow_nan=False, separators=(",", ":"))[1:-6]
            yield colon
            yield from _json_tokens(member, separators)
        yield "}"
    elif isinstance(value, (list, tuple)):
        yield "["
        if _small_numeric_array_size(value) is not None:
            yield json.dumps(value, allow_nan=False, separators=separators)[1:-1]
        elif all(isinstance(member, (int, float, bool, type(None))) for member in value):
            # Numeric leaves dominate result arrays. Keep the fast encoder,
            # but limit each invocation to a small slice of the leaf.
            for offset in range(0, len(value), 256):
                if offset:
                    yield comma
                yield json.dumps(value[offset:offset + 256], allow_nan=False, separators=separators)[1:-1]
        else:
            for index, member in enumerate(value):
                if index:
                    yield comma
                yield from _json_tokens(member, separators)
        yield "]"
    else:
        yield json.dumps(value, allow_nan=False, separators=separators)


def _json_text_chunks(value: Any, separators: tuple[str, str]) -> Iterator[str]:
    buffered: list[str] = []
    characters = 0
    for token in _json_tokens(value, separators):
        buffered.append(token)
        characters += len(token)
        if characters >= 256 * 1024:
            yield "".join(buffered)
            buffered.clear()
            characters = 0
    if buffered:
        yield "".join(buffered)


def json_chunks(value: Any) -> Iterator[bytes]:
    """Compact route JSON, consumed through StreamingResponse's threadpool."""
    for chunk in _json_text_chunks(value, (",", ":")):
        yield chunk.encode("utf-8")


def result_json_text(value: Any) -> str:
    """Match json.dumps(allow_nan=False), releasing the GIL between chunks."""
    chunks = []
    for chunk in _json_text_chunks(value, (", ", ": ")):
        chunks.append(chunk)
        time.sleep(0)
    return "".join(chunks)
