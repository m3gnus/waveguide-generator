"""Byte compatibility with the STEP header ordering shipped for CATIA."""

from pathlib import Path
import re

import gmsh
import pytest

from server.design.schema import DesignConfig
from server.exports import core


@pytest.mark.parametrize("body", ["surface", "solid"])
def test_native_export_header_bytes_match_before_consolidation(body, monkeypatch):
    written_headers = []
    write = gmsh.write

    def capture_native_write(path):
        write(path)
        written_headers.append(Path(path).read_bytes().partition(b"ENDSEC;")[0])

    monkeypatch.setattr(gmsh, "write", capture_native_write)
    design = DesignConfig.model_validate({"formula": "OSSE", "L": 40, "a": 45})
    build = core._build_step_sync if body == "surface" else core._build_step_solid_sync
    result = build(design.model_dump(mode="json"))
    assert len(written_headers) == 1
    # The pre-consolidation WG writer swapped these adjacent entities and
    # preserved every other byte. Use that as the compatibility oracle on the
    # very same native write, including its timestamp and temporary filename.
    before = re.sub(
        rb"(FILE_NAME\s*\(.*?\);)(\s*)(FILE_DESCRIPTION\s*\(.*?\);)",
        rb"\3\2\1",
        written_headers[0],
        count=1,
        flags=re.DOTALL,
    )
    after = result.step_text.encode("utf-8").partition(b"ENDSEC;")[0]
    assert after == before
    core._assert_step(result.step_text)
