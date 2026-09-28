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
    sweep_points,
    unsized_sources,
)
from server.cadlink.setup import DEFAULTS_ORIGIN, setup_content, validate_setup
from server.jobs.models import SolveOptions


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cad_default_setup"
FIXTURES = sorted(path for path in _FIXTURE_DIR.glob("*.json") if path.name != "sweep_points.json")
SWEEP_POINTS = json.loads((_FIXTURE_DIR / "sweep_points.json").read_text(encoding="utf-8"))["cases"]
ROOT = Path(__file__).resolve().parents[2]


def test_the_defaults_are_read_from_the_shared_file() -> None:
    assert SOLVE_DEFAULTS_PATH == ROOT / "shared" / "solve-defaults.json"
    assert solve_defaults() == json.loads(SOLVE_DEFAULTS_PATH.read_text(encoding="utf-8"))


def test_there_are_parity_fixtures() -> None:
    assert [path.stem for path in FIXTURES] == [
        "hf-only", "three-way-lf-mf-hf", "three-way-shared-channel", "two-way-accurate",
    ]


def test_the_fixtures_are_what_the_generator_writes() -> None:
    """A changed default is followed by `gen_cad_default_setup_fixtures.py --write`."""

    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_cad_default_setup_fixtures.py"), "--check"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_lf_mf_hf_chain_crosses_at_the_role_defaults() -> None:
    case = json.loads((FIXTURES[1]).read_text(encoding="utf-8"))
    channels = default_setup({"sources": case["sources"]}, case["selection"]).geometry[
        "combine"
    ]["channels"]
    assert channels["drive-lf"]["lp"]["fc_hz"] == 100
    assert channels["drive-mf"]["hp"]["fc_hz"] == 100
    assert channels["drive-mf"]["lp"]["fc_hz"] == 1000
    assert channels["drive-hf"]["hp"]["fc_hz"] == 1000


def test_the_shipped_defaults_file_passes_the_shape_check() -> None:
    from server.cadlink.default_setup import _check_shape

    _check_shape(json.loads(SOLVE_DEFAULTS_PATH.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        ("{ not json", "solve-defaults.json"),
        ('{"sweep": {}}', "sweep.start_hz is missing"),
        (None, "sweep.points_per_octave has the wrong type"),
    ],
)
def test_a_damaged_defaults_file_is_reported_as_damaged(
    monkeypatch, tmp_path: Path, content: str | None, problem: str
) -> None:
    from server.cadlink import default_setup as module

    if content is None:
        data = json.loads(SOLVE_DEFAULTS_PATH.read_text(encoding="utf-8"))
        data["sweep"]["points_per_octave"] = "4"
        content = json.dumps(data)
    damaged = tmp_path / "solve-defaults.json"
    damaged.write_text(content, encoding="utf-8")
    monkeypatch.setattr(module, "SOLVE_DEFAULTS_PATH", damaged)
    module.solve_defaults.cache_clear()
    try:
        with pytest.raises(module.SolveDefaultsDamaged, match=problem):
            module.solve_defaults()
    finally:
        module.solve_defaults.cache_clear()


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
    sweep = solve_defaults()["sweep"]
    assert options.num_frequencies == sweep_points(
        sweep["start_hz"], sweep["end_hz"], sweep["points_per_octave"]
    ) == 36


@pytest.mark.parametrize("case", SWEEP_POINTS, ids=lambda case: f"{case['start_hz']}-{case['end_hz']}@{case['points_per_octave']}")
def test_the_default_point_count_follows_the_shared_rule(case: dict) -> None:
    assert sweep_points(case["start_hz"], case["end_hz"], case["points_per_octave"]) == case["points"]


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
