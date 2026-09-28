"""WG's default CAD setup: one statement, shared with the frontend.

A first-time model Fusion sends is solved with WG's default settings
(server/cadlink/default_setup.py). Those defaults live once, in
shared/solve-defaults.json, which the frontend's initial settings read too; the
fixtures under fixtures/cad_default_setup pin that the backend builds exactly
the setup a first-time manual Solve in WG records for the same model
(frontend/src/shell/cadDefaultSetupParity.test.ts checks the frontend side).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.cadlink.default_setup import (
    SOLVE_DEFAULTS_PATH,
    default_setup,
    solve_defaults,
    unsized_sources,
)
from server.cadlink.setup import DEFAULTS_ORIGIN, setup_content, validate_setup
from server.jobs.models import SolveOptions


FIXTURES = sorted((Path(__file__).parent / "fixtures" / "cad_default_setup").glob("*.json"))
ROOT = Path(__file__).resolve().parents[2]


def test_the_defaults_are_read_from_the_shared_file() -> None:
    assert SOLVE_DEFAULTS_PATH == ROOT / "shared" / "solve-defaults.json"
    assert solve_defaults() == json.loads(SOLVE_DEFAULTS_PATH.read_text(encoding="utf-8"))


def test_there_are_parity_fixtures() -> None:
    assert len(FIXTURES) == 3


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.stem)
def test_the_backend_builds_the_setup_the_frontend_records(fixture: Path) -> None:
    case = json.loads(fixture.read_text(encoding="utf-8"))

    built = default_setup({"sources": case["sources"]}, case["selection"])

    assert built.origin == DEFAULTS_ORIGIN
    expected = validate_setup({**case["setup"], "origin": DEFAULTS_ORIGIN})
    assert json.loads(setup_content(built)) == json.loads(setup_content(expected))


def test_the_default_options_are_a_valid_solve() -> None:
    built = default_setup(
        {"sources": [{"id": "s", "role": "HF", "suggested_resolution_mm": 4,
                      "default_drive_channel_id": "drive"}]}
    )
    options = SolveOptions.model_validate(built.options)
    assert options.engine == "auto"
    assert options.num_frequencies == solve_defaults()["sweep"]["points"]


def test_a_source_with_no_suggested_mesh_size_is_named_not_guessed() -> None:
    manifest = {"sources": [
        {"id": "sized", "role": "HF", "suggested_resolution_mm": 4, "default_drive_channel_id": "a"},
        {"id": "unsized-1", "role": "LF", "default_drive_channel_id": "b"},
        {"id": "unsized-2", "role": "LF", "suggested_resolution_mm": None, "default_drive_channel_id": "b"},
    ]}

    assert unsized_sources(manifest) == ["unsized-1", "unsized-2"]
    with pytest.raises(ValueError, match="no mesh size for unsized-1, unsized-2"):
        default_setup(manifest)


def test_a_role_default_outside_the_sweep_falls_back_inside_it(monkeypatch) -> None:
    defaults = json.loads(json.dumps(solve_defaults()))
    defaults["sweep"]["start_hz"] = 2000
    monkeypatch.setattr("server.cadlink.default_setup.solve_defaults", lambda: defaults)
    manifest = {"sources": [
        {"id": "lf", "role": "lf", "suggested_resolution_mm": 10, "default_drive_channel_id": "drive-lf"},
        {"id": "hf", "role": " HF ", "suggested_resolution_mm": 3, "default_drive_channel_id": "drive-hf"},
    ]}

    combine = default_setup(manifest).geometry["combine"]

    # Band roles in any spelling order the chain; LF->HF's 1 kHz is below the
    # 2 kHz sweep start, so the log-spaced frequency is used instead.
    assert combine["members"] == ["drive-lf", "drive-hf"]
    assert combine["channels"]["drive-lf"]["lp"]["fc_hz"] == 6325
