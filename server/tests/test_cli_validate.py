"""Headless validation contracts for text designs and local solve readiness."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from server.cli.args import main
from server.design.textcfg import parse
from server.engines.dryrun import DryRunEngine
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.models import SolveRequest


ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT.parent / "Waveguide Generator"
CORPUS_MWG = sorted((V1 / "output").glob("*/script.snapshot.mwg"))

VALID_MWG = """; Parameter config
Length = 120
Coverage.Angle = 45
Throat.Diameter = 25.4
Simulation.F1 = 250
Simulation.F2 = 8000
Simulation.NumFrequencies = 7
WG.Solve = {
Engine = dryrun
SweepSpacing = linear
}
"""


def _registry() -> EngineRegistry:
    return EngineRegistry(
        detector=lambda: [EngineInfo("dryrun", True, "test capability", "builtin")],
        factory=lambda _name: DryRunEngine(),
    )


def _design_path(tmp_path: Path, *, source: str = VALID_MWG) -> Path:
    path = tmp_path / "design.mwg"
    path.write_text(source, encoding="utf-8")
    return path


def _request_path(tmp_path: Path) -> Path:
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "design": parse(VALID_MWG).semantic_data(),
                "options": {
                    "engine": "dryrun",
                    "frequencies_hz": [500.0, 1000.0],
                },
                "client_request_id": "validate-7",
            }
        ),
        encoding="utf-8",
    )
    return path


def _corpus_design_or_fixture(tmp_path: Path) -> Path:
    for path in CORPUS_MWG:
        try:
            parsed = parse(path.read_text(encoding="utf-8"))
            SolveRequest.model_validate({"design": parsed.semantic_data()})
        except (OSError, TypeError, ValueError):
            continue
        if (
            parsed.dialect == "mwg"
            and parsed.design.root.simulation.solver_mode != "circsym"
            and any(
                name == "WG.Solve" or name.startswith("ABEC.Polars:")
                for name in parsed.extra_blocks
            )
        ):
            return path
    return _design_path(tmp_path)


def test_validate_real_corpus_mwg_emits_versioned_json(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    # The v1 corpus is deliberately external to this repository. Use it when a
    # contributor has the historical checkout beside v2, matching the corpus
    # contract tests, and retain a representative MWG document so hosted CI
    # still exercises every CLI field instead of turning this test into a skip.
    path = _corpus_design_or_fixture(tmp_path)
    lifecycle: list[str] = []

    async def prewarm() -> None:
        lifecycle.append("prewarm")

    async def compile_mesh(_design, _options) -> dict[str, object]:
        lifecycle.append("mesh")
        return {
            "stats": {
                "triangle_count": 24,
                "vertex_count": 15,
                "warnings": ["representative mesh warning"],
            },
            "integrity": {"valid": True, "boundary_edge_count": 4},
        }

    async def shutdown() -> None:
        lifecycle.append("shutdown")

    monkeypatch.setattr("server.cli.validate.prewarm_gmsh_worker", prewarm)
    monkeypatch.setattr("server.cli.validate.build_solver_mesh", compile_mesh)
    monkeypatch.setattr("server.cli.validate.shutdown_gmsh_worker", shutdown)

    exit_code = main(
        ["validate", str(path), "--json"],
        engine_registry=_registry(),
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["schemaVersion"] == 1
    assert report["file"] == str(path)
    assert report["dialect"] == "mwg"
    assert report["settingsSource"] == "file"
    assert report["frequencies"]["count"] >= 1
    # Legacy WG.Solve Engine is a machine hint and cannot force the host.
    assert report["engine"] == {
        "requested": "auto",
        "resolved": "dryrun",
        "available": True,
        "reason": "test capability",
    }
    assert report["symmetry"]["requested"] == "auto"
    assert report["solvePath"]["predicted"] == "full-3d"
    assert report["mesh"] == {
        "triangles": 24,
        "vertices": 15,
        "integrity": {"valid": True, "boundary_edge_count": 4},
        "warnings": ["representative mesh warning"],
    }
    assert report["refusals"] == []
    assert report["errors"] == []
    assert lifecycle == ["prewarm", "mesh", "shutdown"]


def test_validate_accepts_the_canonical_http_solve_request(
    tmp_path: Path,
    capsys,
) -> None:
    path = _request_path(tmp_path)

    exit_code = main(
        ["validate", "--request", str(path), "--json", "--no-mesh"],
        engine_registry=_registry(),
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["inputKind"] == "solve_request"
    assert report["dialect"] == "solve-request-json"
    assert report["settingsSource"] == "request"
    assert report["clientRequestId"] == "validate-7"
    assert report["frequencies"] == {
        "start": 500.0,
        "end": 1000.0,
        "count": 2,
        "spacing": "explicit",
        "source": "flags",
    }
    assert report["errors"] == []


def test_metal_auto_path_prediction_uses_circsym_eligibility(monkeypatch) -> None:
    from server.cli import validate

    request = SolveRequest.model_validate(
        {
            "design": parse(VALID_MWG).semantic_data(),
            "options": {"engine": "metal", "solver_mode": "auto"},
        }
    )
    monkeypatch.setattr(
        validate,
        "circsym_eligibility_reasons",
        lambda _request: [],
    )

    summary = asyncio.run(validate._solve_path_summary(request, "metal"))

    assert summary == {"predicted": "axisymmetric-meridian", "reasons": []}


def test_metal_auto_path_prediction_reports_full_3d_fallback(monkeypatch) -> None:
    from server.cli import validate

    request = SolveRequest.model_validate(
        {
            "design": parse(VALID_MWG).semantic_data(),
            "options": {"engine": "metal", "solver_mode": "auto"},
        }
    )
    monkeypatch.setattr(
        validate,
        "circsym_eligibility_reasons",
        lambda _request: ["custom observation needs full 3D"],
    )

    summary = asyncio.run(validate._solve_path_summary(request, "metal"))

    assert summary == {
        "predicted": "full-3d",
        "reasons": ["custom observation needs full 3D"],
    }


def test_validate_preserves_request_identity_on_post_load_refusal(
    tmp_path: Path,
    capsys,
) -> None:
    path = _request_path(tmp_path)

    exit_code = main(
        [
            "validate",
            "--request",
            str(path),
            "--json",
            "--no-mesh",
            "--engine",
            "quantum",
        ],
        engine_registry=_registry(),
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["clientRequestId"] == "validate-7"
    assert report["errors"][0]["client_request_id"] == "validate-7"


def test_validate_overlay_is_deep_merged_and_engine_flag_wins(
    tmp_path: Path,
    capsys,
) -> None:
    path = _design_path(tmp_path)
    overlay = tmp_path / "overlay.json"
    overlay.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "options": {
                    "engine": "metal",
                    "polar_config": {"distance": 4, "field_plane": False},
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "validate",
            str(path),
            "--json",
            "--no-mesh",
            "--overlay",
            str(overlay),
            "--engine",
            "dryrun",
        ],
        engine_registry=_registry(),
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["settingsSource"] == "file+overlay"
    assert report["engine"]["requested"] == "dryrun"


def test_validate_overlay_typo_is_a_refusal(tmp_path: Path, capsys) -> None:
    path = _design_path(tmp_path, source=VALID_MWG.replace("WG.Solve", "Other"))
    overlay = tmp_path / "overlay.json"
    overlay.write_text(
        json.dumps({"schemaVersion": 1, "options": {"frequecy_spacing": "linear"}}),
        encoding="utf-8",
    )

    exit_code = main(
        ["validate", str(path), "--json", "--no-mesh", "--overlay", str(overlay)],
        engine_registry=_registry(),
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["settingsSource"] == "defaults+overlay"
    assert any("frequecy_spacing" in item for item in report["refusals"])
    assert report["errors"][0]["code"] == "invalid_request"


def test_validate_unknown_engine_is_a_listed_refusal(
    tmp_path: Path,
    capsys,
) -> None:
    path = _design_path(tmp_path)

    exit_code = main(
        ["validate", str(path), "--json", "--engine", "quantum"],
        engine_registry=_registry(),
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["engine"]["requested"] == "quantum"
    assert report["engine"]["resolved"] == "quantum"
    assert report["engine"]["available"] is False
    assert report["mesh"] is None
    assert any("Unknown solve engine: quantum" in item for item in report["refusals"])


def test_validate_no_mesh_skips_worker_and_compilation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    path = _design_path(tmp_path)

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("--no-mesh must not touch the gmsh lifecycle")

    monkeypatch.setattr("server.cli.validate.prewarm_gmsh_worker", unexpected)
    monkeypatch.setattr("server.cli.validate.build_solver_mesh", unexpected)
    monkeypatch.setattr("server.cli.validate.shutdown_gmsh_worker", unexpected)

    exit_code = main(
        ["validate", str(path), "--json", "--no-mesh"],
        engine_registry=_registry(),
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["mesh"] is None
    assert report["refusals"] == []
