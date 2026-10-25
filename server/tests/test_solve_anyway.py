"""The one rule for whether a Solve anyway answer carries to a press."""

from __future__ import annotations

import ast
from pathlib import Path

from server.jobs.solve_anyway import Answered, Pressed, carries

ROOT = Path(__file__).resolve().parents[2]


def test_no_answer_never_carries():
    assert carries(None, Pressed()) is False


def test_identity_evidence_keeps_the_setup_and_the_axis():
    answered = Answered("rev-a", "+z", recorded_setup_revision_id="rev-r")
    assert carries(answered, Pressed())
    assert carries(answered, Pressed("rev-a", "+z"))
    assert carries(answered, Pressed("rev-r"))  # The revision it was prepared with.
    assert not carries(answered, Pressed("rev-b"))
    assert not carries(answered, Pressed(frame_axis="-z"))
    # A delivery that named no revision: a press naming one is another mesh.
    assert not carries(Answered(None, None), Pressed("rev-a"))


def test_mesh_evidence_decides_when_the_answer_was_given_on_a_mesh():
    answered = Answered("rev-a", "+z", mesh_known=True, mesh_lookup_key="key-1", domain={"planes": ["x"]})
    # Another revision whose mesh is the same (a solve option changed) carries.
    assert carries(answered, Pressed("rev-b", mesh_lookup_key="key-1", domain={"planes": ["x"]}))
    assert not carries(answered, Pressed("rev-a", "+z", mesh_lookup_key="key-2", domain={"planes": ["x"]}))
    assert not carries(answered, Pressed(mesh_lookup_key="key-1", domain={"planes": ["x", "y"]}))
    # Unknown on either side is not the same.
    assert not carries(answered, Pressed("rev-a", "+z"))
    assert not carries(Answered(mesh_known=True), Pressed(mesh_lookup_key="key-1"))


def test_every_caller_asks_the_one_rule():
    """No second copy: each caller of the rule imports ``carries`` and none restates it."""

    callers = {
        "server/jobs/cad_intent.py": "solve_again_intent",
        "server/jobs/runtime/core.py": "prepare_cad_solve",
        "server/cadlink/joblane/job_shims.py": "_admit_operation_solve",
        "server/jobs/model_solve.py": "_solve_anyway_carries",
    }
    for path, function in callers.items():
        tree = ast.parse((ROOT / path).read_text())
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function)
        called = {c.func.id for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "carries" in called, (path, function)
