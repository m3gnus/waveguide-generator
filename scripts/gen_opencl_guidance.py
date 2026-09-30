#!/usr/bin/env python3
"""Generate the Windows installer notice and help page from shared guidance.

After editing shared/opencl-driver-guidance.v1.json, run this with --write. --check
refuses stale or missing generated files; scripts/tests also checks for drift.
The frontend imports the JSON directly.
"""

from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "shared/opencl-driver-guidance.v1.json"


def windows_warnings(guidance: dict) -> list[str]:
    return [warning["text"] for warning in guidance["warnings"] if "windows" in warning["platforms"]]


def windows_gpu_alternatives(guidance: dict) -> list[str]:
    return [entry["text"] for entry in guidance.get("gpu_alternatives", []) if "windows" in entry["platforms"]]


def pascal_string(text: str) -> str:
    """Quote Pascal strings, including apostrophes and control characters."""
    text = text.replace("\r\n", "\n")
    return "'" + "".join(
        "''" if char == "'" else "' + #13#10 + '" if char == "\n"
        else f"' + #{ord(char)} + '" if ord(char) < 32 else char
        for char in text
    ) + "'"


def check_inno_source(text: str) -> None:
    """Refuse Inno source that ISCC cannot compile, by two textual rules.

    ISPP reads any line whose first non-blank character is ``#`` as a
    preprocessor directive, so a continuation line that begins with a ``#13``
    character code fails with "Unknown preprocessor directive". A ``{ ... }``
    comment ends at the first ``}``, so a ``{`` inside one (an ``{app}``
    mention, say) closes the comment early and the rest compiles as code.
    Neither shows up without running the compiler, which only Windows can do.
    """

    for number, line in enumerate(text.split("\n"), start=1):
        if line.lstrip().startswith("#"):
            raise ValueError(f"generated Inno line {number} starts with '#': ISPP would read it as a directive")
    in_string = in_comment = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if char == "'":
                if text[index + 1:index + 2] == "'":
                    index += 1
                else:
                    in_string = False
        elif in_comment:
            if char == "{":
                raise ValueError("generated Inno brace comment contains '{': the comment would end early")
            if char == "}":
                in_comment = False
        elif char == "'":
            in_string = True
        elif char == "{":
            in_comment = True
        index += 1
    if in_string or in_comment:
        raise ValueError("generated Inno source ends inside a string or a comment")


def render_include(guidance: dict) -> str:
    platform = guidance["platforms"]["windows"]
    paragraphs = [platform["summary"], *windows_gpu_alternatives(guidance)]
    for step in platform["steps"]:
        paragraphs.append(f"{step['label']}: {step['url']}\n{step['note']}")
    paragraphs.extend(windows_warnings(guidance))
    text = "\n\n".join(paragraphs)
    rendered = (
        f"{{ Generated from {SOURCE}.\n"
        "  Regenerate with scripts/gen_opencl_guidance.py --write. }\n"
        f"  OpenClGuidanceTitle = {pascal_string(guidance['title'])};\n"
        f"  OpenClGuidanceText = {pascal_string(text)};\n"
    )
    check_inno_source(rendered)
    return rendered


def render_help(guidance: dict) -> str:
    platform = guidance["platforms"]["windows"]
    title = escape(guidance["title"])
    heading = escape(guidance["labels"]["heading"])
    summary = escape(platform["summary"])
    alternatives = "\n".join(f"  <p>{escape(text)}</p>" for text in windows_gpu_alternatives(guidance))
    warnings = "\n".join(f"  <p>{escape(text)}</p>" for text in windows_warnings(guidance))
    links = "\n".join(
        f'    <li><a href="{escape(step["url"], quote=True)}">'
        f'{escape(step["label"])}</a><p>{escape(step["note"])}</p></li>'
        for step in platform["steps"]
    )
    return f"""<!doctype html>
<!-- Generated from {SOURCE}.
     Regenerate with scripts/gen_opencl_guidance.py --write. -->
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font: 1rem/1.5 system-ui, sans-serif; max-width: 44rem; margin: 2rem auto; padding: 0 1rem; }}
    li {{ margin: 0.75rem 0; }}
  </style>
</head>
<body>
  <h1>{heading}</h1>
  <p>{summary}</p>
{alternatives}
  <ul>
{links}
  </ul>
{warnings}
</body>
</html>
"""


def generated_files(root: Path) -> dict[Path, str]:
    guidance = json.loads((root / SOURCE).read_text(encoding="utf-8"))
    installer = root / "installers/windows"
    return {
        installer / "opencl-guidance.iss": render_include(guidance),
        root / "shared/opencl-guidance.html": render_help(guidance),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="rewrite the generated files")
    group.add_argument("--check", action="store_true", help="fail when generated files are stale")
    args = parser.parse_args(argv)
    stale = []
    for path, rendered in generated_files(ROOT).items():
        if args.write:
            path.write_text(rendered, encoding="utf-8", newline="\n")
        elif not path.exists() or path.read_bytes() != rendered.encode("utf-8"):
            stale.append(path.relative_to(ROOT).as_posix())
    if stale:
        print(
            "Stale OpenCL guidance: " + ", ".join(stale)
            + "; run scripts/gen_opencl_guidance.py --write",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
