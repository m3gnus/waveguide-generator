"""Frozen lexical rules from v0.3.5:server/design/textcfg.py.

Only the lexer and its dependencies; comment handling must remain independent
of the current implementation. Keep the upstream rules unchanged.
"""
from dataclasses import dataclass, field
import re

_BLOCK_START = re.compile(r"^([\w.:-]+)\s*=\s*\{$")


class TextConfigError(ValueError):
    """A source-located syntax or design validation failure."""


@dataclass
class _RawBlock:
    items: dict[str, str] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    entries: list[str] = field(default_factory=list)


def _without_comment(line: str) -> str:
    return line.split(";", 1)[0].strip()


def _collect_function(lines: list[str], index: int, first_value: str) -> tuple[str, int]:
    """Recover multiline Function#toString values found in 143 v1 snapshots."""

    raw = [first_value]
    depth = first_value.count("{") - first_value.count("}")
    opened = "{" in first_value
    index += 1
    while index < len(lines):
        part = lines[index].strip()
        raw.append(part)
        if "{" in part:
            opened = True
        depth += part.count("{") - part.count("}")
        index += 1
        if opened and depth <= 0:
            break
    if not opened or depth > 0:
        raise TextConfigError("unterminated function expression at end of file")
    return "\n".join(raw), index


def _lex(text: str) -> tuple[dict[str, str], dict[str, _RawBlock], list[str], dict[str, str]]:
    flat: dict[str, str] = {}
    blocks: dict[str, _RawBlock] = {}
    comments: list[str] = []
    raw_values: dict[str, str] = {}
    lines = text.splitlines()
    current: str | None = None
    index = 0
    while index < len(lines):
        original = lines[index]
        stripped = original.strip()
        if ";" in original:
            comment = original[original.index(";") :].strip()
            if comment:
                if current is None:
                    comments.append(comment)
                else:
                    blocks[current].comments.append(comment)
        line = _without_comment(original)
        index += 1
        if not line:
            if current is not None and stripped:
                blocks[current].entries.append(stripped)
            continue
        match = _BLOCK_START.fullmatch(line)
        if match:
            if current is not None:
                raise TextConfigError(f"nested block {match.group(1)!r} inside {current!r}")
            current = match.group(1)
            blocks[current] = _RawBlock()
            continue
        if line == "}":
            if current is None:
                raise TextConfigError("unexpected block terminator")
            current = None
            continue
        if current is not None:
            blocks[current].entries.append(stripped)
        if "=" in line:
            key, value = (part.strip() for part in line.split("=", 1))
            if value.startswith("function anonymous("):
                value, index = _collect_function(lines, index - 1, value)
                if current is not None:
                    blocks[current].entries.extend(value.splitlines()[1:])
            qualified = f"{current}.{key}" if current else key
            raw_values[qualified] = value
            if current is None:
                flat[key] = value
            else:
                blocks[current].items[key] = value
            continue
        if current is not None:
            blocks[current].lines.append(line)
    if current is not None:
        raise TextConfigError(f"unterminated block {current!r} at end of file")
    return flat, blocks, comments, raw_values
