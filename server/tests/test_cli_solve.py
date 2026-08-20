"""Headless solve command contracts over the real persistent runtime."""

from __future__ import annotations

import asyncio
import hashlib
from io import StringIO
import json
from pathlib import Path

from server.cli.args import build_parser, main
from server.cli.solve import solve_path
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.design.textcfg import parse
from server.solver.base import EngineRunResult


VALID_MWG = """; Parameter config
Length = 120
Coverage.Angle = 45
Throat.Diameter = 25.4
Simulation.F1 = 500
Simulation.F2 = 1000
Simulation.NumFrequencies = 2
"""

MESH = """$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
3
1 0 0 0
2 1 0 0
3 0 1 0
$EndNodes
$Elements
1
1 2 2 1 1 1 2 3
$EndElements
"""


class TinyEngine:
    name = "metal"

    async def run(self, request, *, cancel_cb, stage_cb):
        cancel_cb()
        stage_cb("frequency_solve", 0.5, "Solving tiny fixture")
        frequencies = request.options.frequencies_hz or [500.0]
        return EngineRunResult(
            results={
                "frequencies": frequencies,
                "metadata": {"engine": "tiny-metal"},
            },
            msh_text=MESH,
            mesh_stats={"vertex_count": 3, "triangle_count": 1},
        )


def _registry(*, engines: tuple[str, ...] = ("metal",)) -> EngineRegistry:
    return EngineRegistry(
        detector=lambda: [
            EngineInfo(name, True, "test capability", "test") for name in engines
        ],
        factory=lambda _name: TinyEngine(),
    )


def _design(tmp_path: Path, source: str = VALID_MWG) -> Path:
    path = tmp_path / "design.mwg"
    path.write_text(source, encoding="utf-8")
    return path


def _request(tmp_path: Path) -> Path:
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "design": parse(VALID_MWG).semantic_data(),
                "options": {"engine": "metal", "frequencies_hz": [500.0]},
                "client_request_id": "external-candidate-9",
                "client_metadata": {"campaign": "cli-contract"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_solve_happy_path_streams_completed_ndjson(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "solve",
            str(_design(tmp_path)),
            "--json-events",
            "--data-dir",
            str(tmp_path / "data"),
        ],
        engine_registry=_registry(),
    )
    captured = capsys.readouterr()
    messages = [json.loads(line) for line in captured.out.splitlines()]

    assert exit_code == 0
    assert captured.err == ""
    assert messages[0]["kind"] == "hello"
    assert any(
        message.get("kind") == "event" and message.get("type") == "completed"
        for message in messages
    )
    assert messages[-1]["kind"] == "outcome"
    assert messages[-1]["status"] == "complete"
    assert len(messages[-1]["result_sha256"]) == 64


def test_solve_output_writes_artifacts_and_refuses_existing_dir(
    tmp_path: Path,
    capsys,
) -> None:
    design = _design(tmp_path)
    output = tmp_path / "output"
    argv = [
        "solve",
        str(design),
        "--data-dir",
        str(tmp_path / "data"),
        "--output",
        str(output),
    ]

    assert main(argv, engine_registry=_registry()) == 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert json.loads((output / "results.json").read_text(encoding="utf-8"))[
        "metadata"
    ]["engine"] == "tiny-metal"
    assert (output / "mesh.msh").read_text(encoding="utf-8") == MESH
    assert (output / "job.log").is_file()
    assert (output / "request.json").is_file()
    assert summary["schemaVersion"] == 1
    assert summary["status"] == "complete"
    assert summary["engine"] == "metal"
    assert summary["runNumber"] == 1
    assert summary["resultKind"] == "parametric"
    assert summary["resultContractVersion"] == 1
    assert summary["artifacts"]["results.json"]["sha256"] == hashlib.sha256(
        (output / "results.json").read_bytes()
    ).hexdigest()
    assert summary["conventions"] == {
        "frame": {
            "axes": {
                "x": "horizontal",
                "y": "vertical",
                "z": "axial (throat to mouth)",
            },
            "axis_remap_matrix": [[1, 0, 0], [0, -1, 0], [0, 0, 1]],
            "winding": "reversed-on-remap",
        },
        "units": {
            "solver_length": "m",
            "cad_length": "mm",
            "frequency": "Hz",
            "phase": "degrees",
        },
        "phasor": "exp(-i omega t)",
    }

    capsys.readouterr()
    assert main(argv, engine_registry=_registry()) == 1
    assert "already exists" in capsys.readouterr().err


def test_solve_accepts_canonical_request_json_and_preserves_identity(
    tmp_path: Path,
    capsys,
) -> None:
    output = tmp_path / "output"
    exit_code = main(
        [
            "solve",
            "--request",
            str(_request(tmp_path)),
            "--json-events",
            "--data-dir",
            str(tmp_path / "data"),
            "--output",
            str(output),
        ],
        engine_registry=_registry(),
    )
    captured = capsys.readouterr()
    messages = [json.loads(line) for line in captured.out.splitlines()]
    request = json.loads((output / "request.json").read_text(encoding="utf-8"))
    results = json.loads((output / "results.json").read_text(encoding="utf-8"))
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert captured.err == ""
    assert request["client_request_id"] == "external-candidate-9"
    assert request["client_metadata"] == {"campaign": "cli-contract"}
    assert results["client_request_id"] == "external-candidate-9"
    assert summary["clientRequestId"] == "external-candidate-9"
    assert messages[-1]["status"] == "complete"
    assert messages[-1]["client_request_id"] == "external-candidate-9"
    assert messages[-1]["result_sha256"] == summary["artifacts"]["results.json"][
        "sha256"
    ]


def test_solve_ndjson_refusal_has_a_stable_error_code(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"design":', encoding="utf-8")

    exit_code = main(
        ["solve", "--request", str(bad), "--json-events"],
        engine_registry=_registry(),
    )
    captured = capsys.readouterr()
    outcome = json.loads(captured.out)

    assert exit_code == 1
    assert outcome["kind"] == "outcome"
    assert outcome["status"] == "refused"
    assert outcome["error"]["code"] == "invalid_input"
    assert outcome["error"]["stage"] == "input"
    assert "not valid JSON" in captured.err


def test_solve_runtime_conflict_exits_two_with_recovery_hint(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    owner = JobRuntime(JobStore.for_data_dir(data_dir), engine_registry=_registry())
    args = build_parser().parse_args(
        ["solve", str(_design(tmp_path)), "--data-dir", str(data_dir)]
    )
    stdout = StringIO()
    stderr = StringIO()

    async def scenario() -> int:
        await owner.start()
        try:
            return await solve_path(
                args,
                engine_registry=_registry(),
                stdout=stdout,
                stderr=stderr,
            )
        finally:
            await owner.shutdown()

    assert asyncio.run(scenario()) == 2
    assert "--data-dir" in stderr.getvalue()
    assert "GUI server" in stderr.getvalue()


def test_solve_overlay_typo_is_refused(tmp_path: Path, capsys) -> None:
    overlay = tmp_path / "overlay.json"
    overlay.write_text(
        json.dumps({"schemaVersion": 1, "options": {"mesh_validaton_mode": "off"}}),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "solve",
            str(_design(tmp_path)),
            "--data-dir",
            str(tmp_path / "data"),
            "--overlay",
            str(overlay),
        ],
        engine_registry=_registry(),
    )

    assert exit_code == 1
    assert "mesh_validaton_mode" in capsys.readouterr().err


def test_solve_overlay_engine_override_is_respected(tmp_path: Path, capsys) -> None:
    source = VALID_MWG + """WG.Solve = {
Engine = dryrun
}
"""
    overlay = tmp_path / "overlay.json"
    overlay.write_text(
        json.dumps({"schemaVersion": 1, "options": {"engine": "metal"}}),
        encoding="utf-8",
    )
    output = tmp_path / "output"

    exit_code = main(
        [
            "solve",
            str(_design(tmp_path, source)),
            "--data-dir",
            str(tmp_path / "data"),
            "--overlay",
            str(overlay),
            "--output",
            str(output),
        ],
        engine_registry=_registry(engines=("dryrun", "metal")),
    )
    capsys.readouterr()
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert summary["engine"] == "metal"
