"""The one reader for the ASCII Gmsh 2.2 surface meshes WG builds and stores.

Every solve mesh crosses the pipeline as Gmsh 2.2 text: the mesher writes it,
the stores persist it byte for byte, and the engines hand it to native
packages. Each stage used to read that text again with its own parser. They
now all ask :func:`read_msh`, which parses a text once per process and hands
back the same immutable :class:`MeshArtifact` for every later request with the
same text, so a solve parses its mesh once however many stages look at it.

The reader is strict about structure: section counts and end markers, four
fields per node row, integer element rows, unique node ids, and triangles that
name declared nodes. Gmsh and WG's own writers only ever produce such text.
What callers may tolerate is a separate question, answered by each caller's
view of the artifact: whether a triangle without a physical tag counts, which
element types may accompany the surface, whether a missing section means an
empty mesh, and which error a caller raises.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from functools import cached_property
from typing import Literal

import numpy as np

__all__ = ["MeshArtifact", "MshFormatError", "read_msh"]


class MshFormatError(ValueError):
    """The text is not a well-formed ASCII Gmsh 2.2 mesh.

    ``kind`` is ``"format"`` when the ``$MeshFormat`` header is missing or names
    another format, and ``"parse"`` when a section does not parse.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Literal["format", "parse"],
        version: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.version = version


@dataclass(frozen=True, eq=False)
class MeshArtifact:
    """One parsed Gmsh 2.2 mesh, with the exact text it was read from.

    Arrays are read-only and in file order. ``points`` are in the text's own
    units. Triangles are rows of 0-based indices into ``points``; triangles
    that carry no tag have a ``triangle_tags`` entry of 0 and a
    ``triangle_tag_counts`` entry of 0. ``element_types`` lists every element
    row, triangles included, and ``triangle_rows`` places each triangle in it.
    """

    text: str
    version: tuple[str, ...]
    #: ``(dim, tag, name)`` fields of each ``$PhysicalNames`` row, as written.
    physical_names: tuple[tuple[str, ...], ...]
    has_nodes: bool
    has_elements: bool
    #: ``[first, last)`` line range of the node rows in ``text.splitlines()``.
    node_lines: tuple[int, int]
    node_ids: tuple[int, ...]
    points: np.ndarray
    element_types: np.ndarray
    triangle_rows: np.ndarray
    triangle_ids: np.ndarray
    triangle_tag_counts: np.ndarray
    triangle_tags: np.ndarray
    #: Gmsh node ids of each triangle's corners, as written.
    triangle_node_ids: np.ndarray

    @property
    def tagged(self) -> np.ndarray:
        """Mask of the triangles that carry at least one tag."""

        return self.triangle_tag_counts >= 1

    @cached_property
    def corner_indices(self) -> np.ndarray:
        """0-based corner indices into ``points``, -1 where a corner names no node.

        Resolved on first use, so a reader of nodes alone never depends on the
        element rows naming declared nodes. Empty without ``$Nodes``.
        """

        if not self.has_nodes or not len(self.triangle_node_ids):
            return _frozen(np.zeros((0, 3), dtype=np.int64))
        ids = np.asarray(self.node_ids, dtype=np.int64)
        if not len(ids):
            return _frozen(np.full(self.triangle_node_ids.shape, -1, dtype=np.int64))
        order = np.argsort(ids, kind="stable")
        ordered = ids[order]
        wanted = self.triangle_node_ids
        found = np.searchsorted(ordered, wanted).clip(max=len(ordered) - 1)
        indices = order[found].astype(np.int64, copy=False)
        indices[ordered[found] != wanted] = -1
        return _frozen(indices.reshape(-1, 3))

    @property
    def triangles(self) -> np.ndarray:
        """0-based corner indices into ``points``; empty without ``$Nodes``.

        Raises :class:`MshFormatError` when a triangle names a node the text
        does not declare.
        """

        indices = self.corner_indices
        if np.any(indices < 0):
            raise MshFormatError(
                "Invalid ASCII Gmsh 2.2 surface: a triangle names an undeclared node",
                kind="parse",
            )
        return indices


def _frozen(array: np.ndarray) -> np.ndarray:
    array.flags.writeable = False
    return array


def _section(lines: list[str], marker: str) -> int | None:
    try:
        return lines.index(marker)
    except ValueError:
        return None


def _counted_rows(
    lines: list[str], header: int, end_marker: str, message: str
) -> tuple[int, int]:
    """``[first, last)`` of a counted section's rows, checked against its end marker."""

    try:
        count = int(lines[header + 1])
        if count < 0 or lines[header + count + 2] != end_marker:
            raise MshFormatError(message, kind="parse")
    except (IndexError, ValueError) as exc:
        if isinstance(exc, MshFormatError):
            raise
        raise MshFormatError(message, kind="parse") from exc
    first = header + 2
    return first, first + count


def _parse(text: str) -> MeshArtifact:
    lines = [line.strip() for line in text.splitlines()]
    header = _section(lines, "$MeshFormat")
    if header is None or header + 1 >= len(lines):
        raise MshFormatError(
            "the mesh artifact is not an ASCII Gmsh 2.2 mesh", kind="format"
        )
    version = tuple(lines[header + 1].split())
    if len(version) < 2 or not version[0].startswith("2.") or version[1] != "0":
        raise MshFormatError(
            "the mesh artifact is not an ASCII Gmsh 2.2 mesh "
            f"(format {' '.join(version)!r})",
            kind="format",
            version=version,
        )

    physical_names: tuple[tuple[str, ...], ...] = ()
    names_header = _section(lines, "$PhysicalNames")
    if names_header is not None:
        first, last = _counted_rows(
            lines, names_header, "$EndPhysicalNames", "Invalid Gmsh physical names"
        )
        physical_names = tuple(tuple(row.split(maxsplit=2)) for row in lines[first:last])

    nodes_header = _section(lines, "$Nodes")
    node_lines = (0, 0)
    node_ids: tuple[int, ...] = ()
    points = np.zeros((0, 3), dtype=float)
    if nodes_header is not None:
        node_lines = _counted_rows(lines, nodes_header, "$EndNodes", "Invalid Gmsh nodes")
        rows = [row.split() for row in lines[node_lines[0] : node_lines[1]]]
        if any(len(row) != 4 for row in rows):
            raise MshFormatError("Invalid Gmsh node row", kind="parse")
        try:
            node_ids = tuple(int(row[0]) for row in rows)
            points = np.asarray([row[1:] for row in rows], dtype=float).reshape(-1, 3)
        except (ValueError, OverflowError) as exc:
            raise MshFormatError(f"Invalid Gmsh node row: {exc}", kind="parse") from exc
        if len(set(node_ids)) != len(node_ids):
            raise MshFormatError("Invalid or duplicate Gmsh node IDs", kind="parse")

    elements_header = _section(lines, "$Elements")
    element_types: list[int] = []
    triangle_rows: list[int] = []
    triangle_ids: list[int] = []
    tag_counts: list[int] = []
    tags: list[int] = []
    corner_ids: list[list[int]] = []
    if elements_header is not None:
        first, last = _counted_rows(
            lines, elements_header, "$EndElements", "Invalid Gmsh elements"
        )
        try:
            for position, line in enumerate(lines[first:last]):
                row = [int(value) for value in line.split()]
                if len(row) < 3:
                    raise MshFormatError("Invalid Gmsh element row", kind="parse")
                element_types.append(row[1])
                if row[1] != 2:
                    continue
                tag_count = row[2]
                if tag_count < 0 or len(row) != 6 + tag_count:
                    raise MshFormatError(
                        "Triangle requires a positive physical tag", kind="parse"
                    )
                triangle_rows.append(position)
                triangle_ids.append(row[0])
                tag_counts.append(tag_count)
                tags.append(row[3] if tag_count >= 1 else 0)
                corner_ids.append(row[3 + tag_count :])
        except (ValueError, OverflowError) as exc:
            if isinstance(exc, MshFormatError):
                raise
            raise MshFormatError(f"Invalid Gmsh element row: {exc}", kind="parse") from exc

    return MeshArtifact(
        text=text,
        version=version,
        physical_names=physical_names,
        has_nodes=nodes_header is not None,
        has_elements=elements_header is not None,
        node_lines=node_lines,
        node_ids=node_ids,
        points=_frozen(points),
        element_types=_frozen(np.asarray(element_types, dtype=np.int64)),
        triangle_rows=_frozen(np.asarray(triangle_rows, dtype=np.int64)),
        triangle_ids=_frozen(np.asarray(triangle_ids, dtype=np.int64)),
        triangle_tag_counts=_frozen(np.asarray(tag_counts, dtype=np.int64)),
        triangle_tags=_frozen(np.asarray(tags, dtype=np.int64)),
        triangle_node_ids=_frozen(np.asarray(corner_ids, dtype=np.int64).reshape(-1, 3)),
    )


#: A solve reads one or two meshes (the build, a ground-placed or rescaled
#: copy, a ladder's bands); a few entries cover it without holding old solves.
_CACHE_ENTRIES = 4
_CACHE_MAX_CHARS = 64 * 1024 * 1024
_cache: OrderedDict[str, MeshArtifact] = OrderedDict()
_cache_lock = threading.Lock()


def read_msh(text: str) -> MeshArtifact:
    """Parse ``text`` once and return the shared artifact for it.

    Repeated requests for an equal text return the same object. The key is the
    text itself: Python caches a string's hash, so a stage handing on the
    string it was given finds the artifact without rehashing it. Raises
    :class:`MshFormatError` when the text is not a well-formed Gmsh 2.2 mesh.
    """

    with _cache_lock:
        cached = _cache.get(text)
        if cached is not None:
            _cache.move_to_end(text)
            return cached
    artifact = _parse(text)
    with _cache_lock:
        _cache[text] = artifact
        _cache.move_to_end(text)
        total = sum(len(key) for key in _cache)
        while len(_cache) > _CACHE_ENTRIES or (len(_cache) > 1 and total > _CACHE_MAX_CHARS):
            evicted, _ = _cache.popitem(last=False)
            total -= len(evicted)
    return artifact


def clear_cache() -> None:
    """Forget every parsed mesh (tests and memory-pressure callers)."""

    with _cache_lock:
        _cache.clear()
