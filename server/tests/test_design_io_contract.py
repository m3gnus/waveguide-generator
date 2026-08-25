"""Design file endpoint and real v1 corpus round-trip contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException
import pytest

from server.design.textcfg import parse, serialize
from server.design_io.api import import_report, open_design, save_design


ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT.parent / "Waveguide Generator"
CORPUS = sorted((V1 / "output").glob("*/script.snapshot.mwg")) + sorted(
    (V1 / "tests/fixtures/ath").glob("*")
)


@pytest.mark.skipif(
    not any(path.suffix == ".mwg" for path in CORPUS),
    reason="the v1 checkout is not beside this one",
)
def test_open_real_legacy_mwg_reports_dialect_and_passthrough() -> None:
    path = next(path for path in CORPUS if path.suffix == ".mwg")
    result = asyncio.run(open_design(path.read_text()))
    assert result["dialect"] == "mwg"
    assert result["design"]["formula"] in {"OSSE", "R-OSSE", "ICW", "FREEFORM"}
    assert set(result["passthrough"]) == {
        "keysPreserved", "blocksPreserved", "keyCount", "blockCount"
    }


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: str(path.relative_to(V1)))
def test_open_reuses_batch_a_round_trip_law_on_the_real_corpus(path: Path) -> None:
    source = path.read_text()
    parsed = parse(source)
    opened = asyncio.run(open_design(source))
    assert opened["design"] == parsed.semantic_data()
    emitted = serialize(parsed)
    assert emitted == source
    assert parse(emitted).semantic_data() == parsed.semantic_data()


def test_save_bare_design_json_uses_cfg_and_v1_header() -> None:
    design = parse("OSSE = {\nL = 120\na = 45\n}\n").semantic_data()
    result = asyncio.run(save_design(design))
    assert result["suggestedFilename"] == "untitled.cfg"
    assert result["text"].startswith("; Parameter config\n")
    assert "Waveguide Generator design-format: 2" in result["text"]


@pytest.mark.parametrize(
    "design",
    [
        {"formula": "OSSE", "throat_profile": 1},
        {"formula": "R-OSSE"},
        {"formula": "ICW"},
    ],
    ids=("OSSE", "R-OSSE", "ICW"),
)
def test_save_sparse_design_can_be_opened_again(design: dict[str, object]) -> None:
    saved = asyncio.run(save_design({"design": design, "filename": "sparse.cfg"}))
    reopened = asyncio.run(open_design(saved["text"]))

    assert reopened["design"]["formula"] == design["formula"]


def test_import_report_is_dry_run_and_invalid_text_has_parse_detail() -> None:
    source = "OSSE = {\nL = 120\na = 45\n}\n"
    first = asyncio.run(import_report(source))
    second = asyncio.run(import_report(source))
    assert first == second
    assert first["dialect"] == "ath"
    with pytest.raises(HTTPException) as caught:
        asyncio.run(open_design("this is not a design"))
    assert caught.value.status_code == 422
    assert caught.value.detail["type"] == "parse_error"
    assert "could not find" in caught.value.detail["message"]


def test_open_report_names_the_machine_solver_mode_it_refused_to_honour() -> None:
    """The one thing a stated solver mode must not do is vanish without a word.

    It is not honoured -- which formulation this host can run is a machine
    fact, so the mode travels in solve options and export strips it -- so the
    open report has to carry the refusal.
    """

    source = "; Parameter config\nOSSE = {\nL = 120\na = 45\n}\nSimulation.SolverMode = circsym\n"

    result = asyncio.run(open_design(source))

    assert result["design"]["simulation"]["solver_mode"] is None
    applied = result["migrationsApplied"]
    assert [item["name"] for item in applied] == ["006_machine_solver_mode_not_portable"]
    assert "Simulation.SolverMode" in applied[0]["note"]
    assert "Solve options" in applied[0]["note"]
