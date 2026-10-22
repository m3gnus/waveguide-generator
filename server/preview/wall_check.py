"""Bounded reuse of fine-preview wall diagnoses, independent of solve meshes."""

from __future__ import annotations

from collections import OrderedDict
import logging
import threading
from typing import Any, Callable, Mapping

from server.contracts.canonical import canonical_ascii_strict_json
from server.integration.mesher_identity import mesher_identity
from server.preview.errors import wall_fold_message

logger = logging.getLogger(__name__)
_LIMIT = 32
_results: OrderedDict[str, str | None] = OrderedDict()
_lock = threading.Lock()


def _key(config: Mapping[str, Any]) -> str:
    return canonical_ascii_strict_json({"mesher": mesher_identity(), "config": config})


def remember(config: Mapping[str, Any], detail: str | None) -> None:
    """Only a successful fine preview or a recognised offset fold is evidence."""
    if detail is not None and wall_fold_message(config, detail) is None:
        return
    key = _key(config)
    with _lock:
        _results[key] = detail
        _results.move_to_end(key)
        while len(_results) > _LIMIT:
            _results.popitem(last=False)


def lookup(config: Mapping[str, Any]) -> tuple[bool, str | None]:
    with _lock:
        key = _key(config)
        if key not in _results:
            return False, None
        _results.move_to_end(key)
        return True, _results[key]


def clear() -> None:
    with _lock:
        _results.clear()


def check_fine_wall(
    config: Mapping[str, Any], cancel_cb: Callable[[], None] | None = None
) -> tuple[bool, str | None]:
    """Run only on a cache miss, in the existing killable mesher worker.

    Use the public fine-preview builder, so sampling and offset validation stay
    owned by the pinned mesher. No second approximation of that guard lives in
    WG. Curvature is display-only and does not affect the wall validation.
    Other preview failures are logged, never turned into a solve refusal.
    """
    from hornlab_mesher.preview.api import build_preview_geometry
    from server.preview.core import preview_options

    if cancel_cb is not None:
        cancel_cb()
    try:
        build_preview_geometry(config, preview_options("fine", curvature=False))
    except Exception as exc:
        detail = str(exc)
        if wall_fold_message(config, detail) is not None:
            return True, detail
        logger.warning("Fine-preview wall check unavailable: %s", detail, exc_info=True)
        return False, None
    if cancel_cb is not None:
        cancel_cb()
    return True, None
