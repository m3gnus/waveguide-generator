"""One bounded scan of a STEP Part 21 file before any geometry kernel sees it.

This is the text half of the untrusted-CAD boundary.  OCC will happily read a
file with ten million records or a megabyte-long entity label; by the time it
does, the allocation has already happened inside native code where WG has no
say.  So the bytes are walked once here first, in Python, with the record,
record-length, and label limits from ``docs/plans/STEP-PARSER-ISOLATION.md``
applied as they are encountered.

Deliberately a *scanner*, not a parser.  It answers "is this file within its
declared budget, and what does its header say" and nothing else.  It never
builds an entity graph, never holds more than one record, and never decides
what the geometry means -- that remains OCC's job, inside the child process
this scan runs in.

The scan is streaming: memory is bounded by the largest single record, which
is itself bounded by :data:`MAX_STEP_RECORD_BYTES`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import BinaryIO, Iterator

from server.cadlink.limits import (
    MAX_STEP_LABEL_CHARS,
    MAX_STEP_RECORD_BYTES,
    MAX_STEP_RECORDS,
)


_READ_CHUNK_BYTES = 1024 * 1024
_ISO_MARKER = b"ISO-10303-21"
#: How far into the file the Part 21 marker may appear. Real files start with
#: it; a byte-order mark or a stray newline is the only slack worth allowing.
_ISO_MARKER_WINDOW = 4096
_ADVANCED_FACE = b"ADVANCED_FACE"


class StepTextError(ValueError):
    """The STEP text is malformed or exceeds a declared budget."""


class StepBudgetExceeded(StepTextError):
    """A record count, record length, or label length passed its limit."""


@dataclass(frozen=True)
class StepTextEvidence:
    """What one bounded scan is willing to assert about a STEP file."""

    size_bytes: int
    record_count: int
    max_record_bytes: int
    max_label_chars: int
    string_count: int
    #: Literal ``ADVANCED_FACE`` occurrences in the text. Advisory only: it is
    #: a byte count, not a parse, so a label containing the word inflates it.
    #: Face *identity* comes from the mesher's own ordering inside the child.
    advanced_face_occurrences: int

    def as_dict(self) -> dict[str, int]:
        return {
            "size_bytes": self.size_bytes,
            "record_count": self.record_count,
            "max_record_bytes": self.max_record_bytes,
            "max_label_chars": self.max_label_chars,
            "string_count": self.string_count,
            "advanced_face_occurrences": self.advanced_face_occurrences,
        }


def decode_step_string(raw: str, *, limit: int | None = None) -> str:
    """Decode one Part 21 string literal's control directives.

    ``limit`` stops the decode as soon as the result is known to exceed it, so
    a hostile label cannot be expanded in full just to measure it.  The caller
    compares ``len(result)`` against the limit; a decode that stopped early
    returns something already longer than the limit, which fails that
    comparison for the right reason.

    Unknown or truncated directives are left as their literal characters rather
    than raising: this function measures length, and refusing a file over an
    encoding WG merely does not recognise would be a budget gate pretending to
    be a validator.
    """

    out: list[str] = []
    index = 0
    length = len(raw)
    while index < length:
        if limit is not None and len(out) > limit:
            return "".join(out)
        char = raw[index]
        if char == "'" and raw.startswith("''", index):
            out.append("'")
            index += 2
            continue
        if char != "\\":
            out.append(char)
            index += 1
            continue
        rest = raw[index + 1 :]
        if rest.startswith("\\"):
            out.append("\\")
            index += 2
            continue
        if rest.startswith("S\\") and len(rest) >= 3:
            out.append(chr(ord(rest[2]) + 128))
            index += 4
            continue
        if rest.startswith("X\\") and len(rest) >= 4:
            try:
                out.append(chr(int(rest[2:4], 16)))
            except ValueError:
                out.append(raw[index : index + 5])
            index += 5
            continue
        if rest.startswith(("X2\\", "X4\\")):
            width = 4 if rest[1] == "2" else 8
            terminator = rest.find("\\X0\\", 3)
            if terminator < 0:
                out.append(char)
                index += 1
                continue
            digits = rest[3:terminator]
            for start in range(0, len(digits) - width + 1, width):
                try:
                    out.append(chr(int(digits[start : start + width], 16)))
                except ValueError:
                    out.append("�")
                if limit is not None and len(out) > limit:
                    return "".join(out)
            index += 1 + terminator + 4
            continue
        if rest.startswith("P") and len(rest) >= 3 and rest[2] == "\\":
            # ``\PA\`` designates an alphabet and emits no character.
            index += 4
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _chunks(stream: BinaryIO) -> Iterator[bytes]:
    while True:
        chunk = stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            return
        yield chunk


def _prepend(first: bytes, rest: Iterator[bytes]) -> Iterator[bytes]:
    if first:
        yield first
    yield from rest


# Outside a string and a comment, only three things change the scanner's mind.
_OUTSIDE = re.compile(rb"'|;|/\*")


class _Scanner:
    """The chunk-at-a-time state machine behind :func:`scan_step_text`.

    Byte-at-a-time in Python costs seconds on a real waveguide export, so the
    scan jumps between delimiters with ``re.search`` and only ever inspects the
    bytes between them in bulk.  The awkward part is that ``''``, ``/*`` and
    ``*/`` are two bytes wide and a read boundary can fall between them, so
    every state leaves an unconsumed tail behind rather than guessing.
    """

    def __init__(self, *, max_records: int, max_record_bytes: int, max_label_chars: int) -> None:
        self._max_records = max_records
        self._max_record_bytes = max_record_bytes
        self._max_label_chars = max_label_chars
        self.record_count = 0
        self.max_record_bytes = 0
        self.max_label_chars = 0
        self.string_count = 0
        self.in_string = False
        self.in_comment = False
        self._record_bytes = 0
        self._string = bytearray()

    def _advance(self, count: int) -> None:
        self._record_bytes += count
        if self._record_bytes > self._max_record_bytes:
            raise StepBudgetExceeded(
                f"STEP text: a single record exceeds the {self._max_record_bytes:,} "
                "byte limit for one entity record"
            )

    def _end_record(self) -> None:
        self.record_count += 1
        if self.record_count > self._max_records:
            raise StepBudgetExceeded(
                f"STEP text: more than {self._max_records:,} entity records"
            )
        self.max_record_bytes = max(self.max_record_bytes, self._record_bytes)
        self._record_bytes = 0

    def _close_string(self) -> None:
        self.in_string = False
        self.string_count += 1
        decoded = decode_step_string(
            self._string.decode("latin-1"), limit=self._max_label_chars
        )
        if len(decoded) > self._max_label_chars:
            raise StepBudgetExceeded(
                "STEP text: a decoded string label exceeds the "
                f"{self._max_label_chars:,} character limit"
            )
        self.max_label_chars = max(self.max_label_chars, len(decoded))
        self._string.clear()

    def _keep_string(self, data: bytes) -> None:
        # The record budget already bounds this; the guard keeps the bound
        # explicit at the one place memory actually grows.
        if len(self._string) <= self._max_record_bytes:
            self._string += data

    def feed(self, buffer: bytes, *, final: bool) -> bytes:
        """Consume what is unambiguous and return the tail to re-feed."""

        position = 0
        size = len(buffer)
        while position < size:
            if self.in_comment:
                closing = buffer.find(b"*/", position)
                if closing < 0:
                    keep = 1 if not final and buffer.endswith(b"*") else 0
                    self._advance(size - keep - position)
                    return buffer[size - keep :] if keep else b""
                self._advance(closing + 2 - position)
                position = closing + 2
                self.in_comment = False
                continue
            if self.in_string:
                quote = buffer.find(b"'", position)
                if quote < 0:
                    self._advance(size - position)
                    self._keep_string(buffer[position:])
                    return b""
                self._keep_string(buffer[position:quote])
                if quote + 1 >= size:
                    if not final:
                        # Cannot tell a close from an escaped quote yet.
                        self._advance(quote - position)
                        return buffer[quote:]
                    self._advance(size - position)
                    self._close_string()
                    return b""
                if buffer[quote + 1 : quote + 2] == b"'":
                    self._keep_string(b"''")
                    self._advance(quote + 2 - position)
                    position = quote + 2
                    continue
                self._advance(quote + 1 - position)
                position = quote + 1
                self._close_string()
                continue
            match = _OUTSIDE.search(buffer, position)
            if match is None:
                keep = 1 if not final and buffer.endswith(b"/") else 0
                self._advance(size - keep - position)
                return buffer[size - keep :] if keep else b""
            token = match.group()
            self._advance(match.end() - position)
            position = match.end()
            if token == b";":
                self._end_record()
            elif token == b"'":
                self.in_string = True
                self._string.clear()
            else:
                self.in_comment = True
        return b""

    def finish(self) -> None:
        if self.in_string:
            raise StepTextError("STEP text: a string literal is never closed")
        if self.in_comment:
            raise StepTextError("STEP text: a comment is never closed")
        if self.record_count == 0:
            raise StepTextError("STEP text: the file contains no terminated records")
        self.max_record_bytes = max(self.max_record_bytes, self._record_bytes)


def scan_step_text(
    path: str | Path,
    *,
    max_records: int = MAX_STEP_RECORDS,
    max_record_bytes: int = MAX_STEP_RECORD_BYTES,
    max_label_chars: int = MAX_STEP_LABEL_CHARS,
) -> StepTextEvidence:
    """Walk one STEP file once, refusing at the first budget it breaks.

    A record is a ``;``-terminated statement outside a string literal and
    outside a ``/* */`` comment.  That is coarser than the Part 21 grammar --
    it counts header statements alongside entity instances -- and deliberately
    so: the budget is about how much text a kernel is about to be handed, not
    about which clause of the standard produced it.
    """

    source = Path(path)
    size_bytes = source.stat().st_size
    scanner = _Scanner(
        max_records=max_records,
        max_record_bytes=max_record_bytes,
        max_label_chars=max_label_chars,
    )
    occurrences = 0
    seam = b""
    tail = b""

    with source.open("rb") as stream:
        # Refuse a non-STEP file on its first chunk rather than after walking
        # however many megabytes it turned out to be.
        opening = stream.read(_ISO_MARKER_WINDOW)
        if _ISO_MARKER not in opening:
            raise StepTextError(
                "STEP text: the file does not begin with an ISO-10303-21 marker, so "
                "it is not a STEP Part 21 file"
            )
        for chunk in _prepend(opening, _chunks(stream)):
            joined = seam + chunk
            occurrences += joined.count(_ADVANCED_FACE)
            seam = joined[-(len(_ADVANCED_FACE) - 1) :]
            tail = scanner.feed(tail + chunk, final=False)
        if tail:
            scanner.feed(tail, final=True)

    scanner.finish()
    return StepTextEvidence(
        size_bytes=size_bytes,
        record_count=scanner.record_count,
        max_record_bytes=scanner.max_record_bytes,
        max_label_chars=scanner.max_label_chars,
        string_count=scanner.string_count,
        advanced_face_occurrences=occurrences,
    )


__all__ = [
    "StepBudgetExceeded",
    "StepTextError",
    "StepTextEvidence",
    "decode_step_string",
    "scan_step_text",
]
