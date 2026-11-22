"""The one reader for the ASCII Gmsh 2.2 surface meshes WG builds and stores.

Every solve mesh crosses the pipeline as Gmsh 2.2 text: the mesher writes it,
the stores persist it byte for byte, and the engines hand it to native
packages. Each stage used to read that text again with its own parser. They
now all ask :func:`read_msh`, which tokenises a text once and hands back the
same immutable :class:`MeshArtifact` to every stage that asks for that text
soon after, so a solve reads its mesh once however many stages look at it.

The reader never refuses a text. It records what it found -- the header, each
section, and every row it could not read -- and leaves the verdict to each
caller's view of the artifact, because callers have always tolerated
different things: a triangle without a tag, a missing section, a point or
line element beside the surface, a header that is absent. Each view keeps its
own tolerance and its own errors. Text that Gmsh or WG writes always reads
cleanly, so every view sees the same arrays.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import cached_property

import numpy as np

__all__ = ["ElementSection", "MeshArtifact", "NodeSection", "clear_cache", "read_msh", "read_msh_nodes"]


def _immutable(array: np.ndarray) -> np.ndarray:
    """A read-only copy that owns immutable memory: it cannot be made writeable."""

    array = np.ascontiguousarray(array)
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


_EMPTY_POINTS = _immutable(np.zeros((0, 3), dtype=float))
_EMPTY_INTS = _immutable(np.zeros(0, dtype=np.int64))
_EMPTY_CORNERS = _immutable(np.zeros((0, 3), dtype=np.int64))


@dataclass(frozen=True)
class NodeSection:
    """The ``$Nodes`` section as written.

    ``problem`` names the first thing that kept the rows from reading as
    nodes, in the words the strict reader has always used; the arrays are
    then empty. ``count_text`` is the raw count line and ``rows_read`` how many
    rows the count asked for that the text holds.
    """

    header_line: int
    #: The marker line was written exactly, with no surrounding whitespace.
    exact_marker: bool
    count: int | None
    problem: tuple[str, str] | None
    #: The ``$EndNodes`` marker sits right after the counted rows.
    end_ok: bool
    #: Every node row has exactly an id and three coordinates.
    rows_exact: bool
    #: Fewer rows than the count asks for remain in the text.
    truncated: bool
    first_line: int
    last_line: int
    node_ids: tuple[int, ...]
    points: np.ndarray
    #: Why the coordinates did not read, when they did not.
    coordinates_problem: str | None
    #: When the rows did not all read as nodes: each coordinate column of the
    #: rows that have one, or why that column did not read. A reader of a
    #: single coordinate column never needed more.
    columns: tuple[np.ndarray | str, ...] | None

    @property
    def unique(self) -> bool:
        return len(set(self.node_ids)) == len(self.node_ids)


@dataclass(frozen=True)
class ElementSection:
    """The ``$Elements`` section as written.

    Every row is kept in ``element_types`` (-1 where the type field is missing
    or not an integer) and ``row_lengths``. Triangles are type-2 rows of at
    least four fields whose integers all read; a type-2 row the reader could
    not take is listed in ``triangle_problems`` as ``(row, tag_count, kind)``
    with kind ``"short"`` (fewer fields than its tag count needs) or
    ``"integer"`` (a field is not an integer; ``tag_count`` is ``None`` when
    that field is the tag count itself).
    """

    header_line: int
    exact_marker: bool
    count: int | None
    problem: tuple[str, str] | None
    end_ok: bool
    truncated: bool
    element_types: np.ndarray
    row_lengths: np.ndarray
    #: ``(row, message)`` of every row with a field that is not an integer.
    integer_errors: tuple[tuple[int, str], ...]
    triangle_rows: np.ndarray
    triangle_ids: np.ndarray
    triangle_tag_counts: np.ndarray
    triangle_tags: np.ndarray
    #: The row holds more fields than ``6 + tag_count``.
    triangle_extra: np.ndarray
    #: Gmsh node ids of each triangle's corners, as written.
    triangle_node_ids: np.ndarray
    triangle_problems: tuple[tuple[int, int | None, str], ...]


@dataclass(frozen=True, eq=False)
class MeshArtifact:
    """One tokenised Gmsh 2.2 text. Every array is immutable and in file order.

    ``version`` is ``None`` without a ``$MeshFormat`` line followed by another
    line; ``has_format_line`` says whether the marker itself was found.
    ``nodes`` and ``elements`` are ``None`` when the text has no such section.
    """

    text: str
    line_count: int
    has_format_line: bool
    version: tuple[str, ...] | None
    physical_names_line: int | None
    physical_names: tuple[tuple[str, ...], ...]
    #: The exception the old row-by-row names reader raised, re-raised by views.
    physical_names_error: tuple[type[BaseException], str] | None
    nodes: NodeSection | None
    elements: ElementSection | None

    @property
    def points(self) -> np.ndarray:
        return self.nodes.points if self.nodes is not None else _EMPTY_POINTS

    @property
    def node_ids(self) -> tuple[int, ...]:
        return self.nodes.node_ids if self.nodes is not None else ()

    @cached_property
    def corner_indices(self) -> np.ndarray:
        """0-based corner indices into ``points``, -1 where a corner names no node.

        A node id written twice resolves to its last row, as an id-keyed lookup
        filled row by row does. Empty without readable nodes and triangles.
        """

        elements = self.elements
        nodes = self.nodes
        if elements is None or not len(elements.triangle_node_ids):
            return _EMPTY_CORNERS
        wanted = elements.triangle_node_ids
        if nodes is None or nodes.problem is not None:
            return _immutable(np.full(wanted.shape, -1, dtype=np.int64))
        ids = np.asarray(nodes.node_ids, dtype=np.int64)
        if not len(ids):
            return _immutable(np.full(wanted.shape, -1, dtype=np.int64))
        order = np.argsort(ids, kind="stable")
        ordered = ids[order]
        found = (np.searchsorted(ordered, wanted, side="right") - 1).clip(min=0)
        indices = order[found]
        indices[ordered[found] != wanted] = -1
        return _immutable(indices)


def _section(lines: list[str], marker: str) -> int | None:
    try:
        return lines.index(marker)
    except ValueError:
        return None


def _count(lines: list[str], header: int) -> tuple[int | None, tuple[str, str] | None]:
    try:
        return int(lines[header + 1]), None
    except IndexError:
        return None, ("index", "the section has no count")
    except ValueError as exc:
        return None, ("value", str(exc))


def _nodes(lines: list[str], header: int, exact: bool) -> NodeSection:
    count, problem = _count(lines, header)
    first = header + 2
    rows_text = lines[first : first + count] if count is not None and count > 0 else []
    end_index = header + (count or 0) + 2
    end_ok = count is not None and end_index < len(lines) and lines[end_index] == "$EndNodes"
    truncated = count is not None and len(rows_text) != count
    node_ids: tuple[int, ...] = ()
    points = _EMPTY_POINTS
    rows_exact = True
    coordinates_problem: str | None = None
    columns: tuple[np.ndarray | str, ...] | None = None
    if problem is None:
        rows = [row.split() for row in rows_text]
        rows_exact = all(len(row) == 4 for row in rows)
        if any(len(row) < 4 for row in rows):
            problem = ("row", "a node row has fewer than three coordinates")
        else:
            try:
                node_ids = tuple(int(row[0]) for row in rows)
            except ValueError as exc:
                problem = ("id", str(exc))
            # Coordinates are read even when an id is not an integer: a reader
            # of coordinates alone never looked at the ids.
            try:
                parsed = np.asarray([row[1:4] for row in rows], dtype=float).reshape(-1, 3)
            except ValueError as exc:
                coordinates_problem = str(exc)
                if problem is None:
                    problem = ("float", coordinates_problem)
            else:
                points = _immutable(parsed)
        if problem is not None and problem[0] in {"row", "id", "float"}:
            whole = [row for row in rows if len(row) >= 4]
            found: list[np.ndarray | str] = []
            for column in range(1, 4):
                try:
                    found.append(_immutable(np.asarray([row[column] for row in whole], dtype=float)))
                except ValueError as exc:
                    found.append(str(exc))
            columns = tuple(found)
    return NodeSection(
        header_line=header,
        exact_marker=exact,
        count=count,
        problem=problem,
        end_ok=end_ok,
        rows_exact=rows_exact,
        truncated=truncated,
        first_line=first,
        last_line=first + max(count or 0, 0),
        node_ids=node_ids,
        points=points,
        coordinates_problem=coordinates_problem,
        columns=columns,
    )


def _elements(lines: list[str], header: int, exact: bool) -> ElementSection:
    count, problem = _count(lines, header)
    first = header + 2
    rows_text = lines[first : first + count] if count is not None and count > 0 else []
    end_index = header + (count or 0) + 2
    end_ok = count is not None and end_index < len(lines) and lines[end_index] == "$EndElements"
    truncated = count is not None and len(rows_text) != count
    types: list[int] = []
    lengths: list[int] = []
    integer_errors: list[tuple[int, str]] = []
    triangle_rows: list[int] = []
    triangle_ids: list[int] = []
    tag_counts: list[int] = []
    tags: list[int] = []
    extra: list[bool] = []
    corners: list[list[int]] = []
    problems: list[tuple[int, int | None, str]] = []
    for position, line in enumerate(rows_text):
        parts = line.split()
        lengths.append(len(parts))
        try:
            row = [int(value) for value in parts]
        except ValueError as exc:
            row = None
            integer_errors.append((position, str(exc)))
        if row is not None:
            kind = row[1] if len(row) >= 2 else -1
        else:
            try:
                kind = int(parts[1])
            except (IndexError, ValueError):
                kind = -1
        types.append(kind)
        if kind != 2 or len(parts) < 4:
            continue
        if row is None:
            try:
                tag_count: int | None = int(parts[2])
            except ValueError:
                tag_count = None
            problems.append((position, tag_count, "integer"))
            continue
        tag_count = row[2]
        if tag_count < 0 or len(row) < 6 + tag_count:
            problems.append((position, tag_count, "short"))
            continue
        triangle_rows.append(position)
        triangle_ids.append(row[0])
        tag_counts.append(tag_count)
        tags.append(row[3] if tag_count >= 1 else 0)
        extra.append(len(row) > 6 + tag_count)
        corners.append(row[3 + tag_count : 6 + tag_count])
    return ElementSection(
        header_line=header,
        exact_marker=exact,
        count=count,
        problem=problem,
        end_ok=end_ok,
        truncated=truncated,
        element_types=_immutable(np.asarray(types, dtype=np.int64)),
        row_lengths=_immutable(np.asarray(lengths, dtype=np.int64)),
        integer_errors=tuple(integer_errors),
        triangle_rows=_immutable(np.asarray(triangle_rows, dtype=np.int64)),
        triangle_ids=_immutable(np.asarray(triangle_ids, dtype=np.int64)),
        triangle_tag_counts=_immutable(np.asarray(tag_counts, dtype=np.int64)),
        triangle_tags=_immutable(np.asarray(tags, dtype=np.int64)),
        triangle_extra=_immutable(np.asarray(extra, dtype=bool)),
        triangle_node_ids=_immutable(np.asarray(corners, dtype=np.int64).reshape(-1, 3)),
        triangle_problems=tuple(problems),
    )


def _physical_names(
    lines: list[str], header: int
) -> tuple[tuple[tuple[str, ...], ...], tuple[type[BaseException], str] | None]:
    """The rows the count names, read as the tagged-mesh reader always read them."""

    try:
        count = int(lines[header + 1])
    except (ValueError, IndexError) as exc:
        return (), (type(exc), str(exc))
    rows = lines[header + 2 : header + 2 + count]
    return tuple(tuple(row.split(maxsplit=2)) for row in rows), None


def _parse(text: str, *, elements: bool = True) -> MeshArtifact:
    raw = text.splitlines()
    lines = [line.strip() for line in raw]
    header = _section(lines, "$MeshFormat")
    version = (
        tuple(lines[header + 1].split())
        if header is not None and header + 1 < len(lines)
        else None
    )
    names_header = _section(lines, "$PhysicalNames")
    names: tuple[tuple[str, ...], ...] = ()
    names_error = None
    if names_header is not None:
        names, names_error = _physical_names(lines, names_header)
    nodes_header = _section(lines, "$Nodes")
    elements_header = _section(lines, "$Elements") if elements else None
    return MeshArtifact(
        text=text,
        line_count=len(lines),
        has_format_line=header is not None,
        version=version,
        physical_names_line=names_header,
        physical_names=names,
        physical_names_error=names_error,
        nodes=(
            _nodes(lines, nodes_header, raw[nodes_header] == "$Nodes")
            if nodes_header is not None
            else None
        ),
        elements=(
            _elements(lines, elements_header, raw[elements_header] == "$Elements")
            if elements_header is not None
            else None
        ),
    )


#: A solve reads one or two meshes (the build, a rescaled copy, a ladder's
#: bands) within minutes. Entries go after a few minutes unused, so a finished
#: solve's mesh is not held for the server's lifetime.
_CACHE_ENTRIES = 4
_CACHE_MAX_BYTES = 192 * 1024 * 1024
_CACHE_IDLE_SECONDS = 300.0
_cache: OrderedDict[str, tuple[MeshArtifact, int, float]] = OrderedDict()
_cache_lock = threading.Lock()


def _reset_lock_in_child() -> None:
    # A fork taken while another thread held the lock would leave the child
    # waiting on it forever (bempp's process pool forks on Linux).
    global _cache_lock
    _cache_lock = threading.Lock()
    _cache.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_lock_in_child)


def _size(artifact: MeshArtifact) -> int:
    size = len(artifact.text)
    for section in (artifact.nodes, artifact.elements):
        if section is None:
            continue
        for value in vars(section).values():
            if isinstance(value, np.ndarray):
                size += value.nbytes
        # A Python int above 256 and its tuple slot take about 36 bytes.
        size += 36 * len(getattr(section, "node_ids", ()))
    # The lazy corner indices take as much as the triangle node ids.
    if artifact.elements is not None:
        size += artifact.elements.triangle_node_ids.nbytes
    return size


def _expire(now: float) -> None:
    """Drop entries unused for the idle time; call with the lock held."""

    for key in [key for key, (_, _, used) in _cache.items() if now - used > _CACHE_IDLE_SECONDS]:
        del _cache[key]


def read_msh(text: str) -> MeshArtifact:
    """Tokenise ``text`` once and return the shared artifact for it.

    A request for an equal text soon after returns the same object. The key is
    the text itself: Python caches a string's hash, so a stage handing on the
    string it was given finds the artifact without rehashing it. Two threads
    reading a new text at once may each tokenise it; they get equal artifacts.
    """

    now = time.monotonic()
    with _cache_lock:
        _expire(now)
        entry = _cache.get(text)
        if entry is not None:
            _cache[text] = (entry[0], entry[1], now)
            _cache.move_to_end(text)
            return entry[0]
    artifact = _parse(text)
    size = _size(artifact)
    with _cache_lock:
        _expire(now)
        if size <= _CACHE_MAX_BYTES:
            _cache[text] = (artifact, size, now)
            _cache.move_to_end(text)
        total = sum(entry_size for _, entry_size, _ in _cache.values())
        while _cache and (len(_cache) > _CACHE_ENTRIES or total > _CACHE_MAX_BYTES):
            _, (_, evicted, _) = _cache.popitem(last=False)
            total -= evicted
    return artifact


def read_msh_nodes(text: str) -> MeshArtifact:
    """The header and nodes of ``text`` alone, unshared.

    For a caller that reads only node rows and does not hand the text on
    (a ground-plane placement rewrites it): it costs no element parse and
    keeps nothing alive.
    """

    with _cache_lock:
        _expire(time.monotonic())
        entry = _cache.get(text)
    if entry is not None:
        return entry[0]
    return _parse(text, elements=False)


def clear_cache() -> None:
    """Forget every tokenised mesh."""

    with _cache_lock:
        _cache.clear()
