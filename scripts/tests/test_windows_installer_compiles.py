"""Source rules that Inno Setup's compiler enforces and nothing else here does.

ISCC runs only on Windows, and only ``rc-build.yml`` runs it, so a script that
cannot compile passes every other check. These three rules each stopped a real
compile on 2026-09-30 (Inno Setup 6.7.3); they are checked from the text so a
host without Inno still sees them.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "installers/windows").glob("*.iss"))


def code_section(text: str) -> tuple[str, int]:
    """The [Code] section and the line it starts on; the whole file for an include."""
    if "[Code]" not in text:
        return text, 1
    start = text.index("[Code]")
    return text[start:], text.count("\n", 0, start) + 1


def nested_brace_comments(text: str) -> list[int]:
    """Lines where a ``{`` sits inside a brace comment of the [Code] section.

    A Pascal comment opened with ``{`` ends at the first ``}``. A constant such
    as ``{app}`` written inside one therefore ends it, and the rest of the
    comment is compiled as code: "'BEGIN' expected" or "Syntax error".
    """

    code, first_line = code_section(text)
    found: list[int] = []
    i, size = 0, len(code)
    while i < size:
        char = code[i]
        if char == "'":
            i += 1
            while i < size and code[i] != "\n":
                if code[i] == "'":
                    if code[i + 1:i + 2] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        elif code.startswith("//", i):
            while i < size and code[i] != "\n":
                i += 1
        elif code.startswith("(*", i):
            i = code.index("*)", i) + 2
        elif char == "{":
            end = code.index("}", i)
            inner = code.find("{", i + 1, end)
            if inner >= 0:
                found.append(first_line + code.count("\n", 0, inner))
            i = end + 1
        else:
            i += 1
    return found


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda path: path.name)
def test_no_brace_comment_contains_an_opening_brace(path: Path) -> None:
    lines = nested_brace_comments(path.read_text(encoding="utf-8"))
    assert not lines, (
        f"{path.name}: a brace comment contains '{{' on line(s) {lines}. The comment "
        "ends at the first '}', so write <app> rather than {app} inside a comment."
    )


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda path: path.name)
def test_no_code_line_starts_with_a_character_literal(path: Path) -> None:
    """The preprocessor reads a line whose first character is ``#`` as a directive.

    A continuation line that begins with ``#13#10`` is "Unknown preprocessor
    directive". Put the literal at the end of the previous line instead.
    """

    code, first_line = code_section(path.read_text(encoding="utf-8"))
    lines = [
        first_line + index
        for index, line in enumerate(code.split("\n"))
        if re.match(r"\s*#\d", line)
    ]
    assert not lines, f"{path.name}: line(s) {lines} start with a '#' character literal"


def test_the_detector_sees_the_failures_it_exists_for() -> None:
    assert nested_brace_comments("[Code]\n{ beside {app}, never inside }\n") == [2]
    assert nested_brace_comments("[Code]\n{ {from, to}; then }\n") == [2]
    assert nested_brace_comments("[Code]\nS := '{app}'; { plain comment }\n// {app} {\n") == []
    assert nested_brace_comments("[Code]\n(* {app} { *)\n{ ok }\n") == []


def test_no_constant_shadows_one_inno_setup_predefines() -> None:
    """Inno Setup 6.7.3 predefines these; declaring one again is a duplicate identifier."""

    script = (ROOT / "installers/windows/bundle-setup.iss").read_text(encoding="utf-8")
    code, _ = code_section(script)
    for name in ("FILE_ATTRIBUTE_REPARSE_POINT",):
        assert not re.search(rf"^\s*{name}\s*=", code, flags=re.MULTILINE), (
            f"{name} is declared in [Code]; Inno Setup 6.7.3 already defines it"
        )
