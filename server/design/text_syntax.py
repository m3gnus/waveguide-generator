"""Structural boundaries shared by lossless expressions and the text lexer/writer."""

import re


# Keep the name capture: the lexer uses it to retain unknown config blocks.
BLOCK_START = re.compile(r"^([\w.:-]+)\s*=\s*\{$")
LINE_BREAK = re.compile(r"[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def without_comment(line: str) -> str:
    return line.split(";", 1)[0].strip()


def has_balanced_braces(source: str) -> bool:
    """Apply the text format's deliberately simple, character-wise accounting."""

    depth = 0
    for character in source:
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def is_complete_legacy_function(source: str) -> bool:
    """Return whether textcfg's multiline function collector consumes all of it."""

    lines = source.splitlines()
    if (
        len(lines) < 2
        or LINE_BREAK.search(source[-1:]) is not None
        or not lines[0].strip().startswith("function anonymous(")
    ):
        return False
    depth = 0
    opened = False
    for index, line in enumerate(lines):
        if "{" in line:
            opened = True
        depth += line.count("{") - line.count("}")
        if depth < 0:
            return False
        if opened and depth == 0:
            return index == len(lines) - 1
    return False


def line_is_config_block_syntax(line: str) -> bool:
    candidate = without_comment(line)
    return candidate == "}" or BLOCK_START.fullmatch(candidate) is not None
