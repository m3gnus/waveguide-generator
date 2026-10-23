"""The file editor's replay/edit contract must remain valid server input.

meshImport.test.ts compares real Save/Solve output with these same fixtures.
Validate complete setups here, including cross-channel and option constraints,
instead of duplicating the server's driver/assignment rules in TypeScript.
"""

import json
from pathlib import Path

import pytest

from server.jobs.imported_setup import validate_setup


CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures" / "imported_setup_edits.json").read_text()
)
SETUPS = [
    *CONTRACT["setups"].items(),
    *((edit["name"], edit["expected"]) for edit in CONTRACT["edits"]),
]


@pytest.mark.parametrize("name,setup", SETUPS, ids=[name for name, _ in SETUPS])
def test_editor_setup_passes_server_validation(name, setup):
    validate_setup({"schema_version": 1, **setup})
