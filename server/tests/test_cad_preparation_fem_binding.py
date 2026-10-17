"""Both snapshot-binding call sites reset exclusions for a changed FEM set.

Both bind through the composition every solve of a model shares
(``compose_solve_request``).

The retired operation lane's binding is exercised directly without reviving its
lifecycle; the active job lane also has an end-to-end preparation regression.
"""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest

from server.cadlink.joblane.project_setup import compose_solve_request
from server.cadlink.joblane.setup import validate_setup


@pytest.mark.parametrize("lane", ["operations", "jobs"])
@pytest.mark.parametrize("excluded", [None, ["old"], ["current"]])
def test_snapshot_binding_supplies_current_regions(lane, excluded):
    from server.cadlink.joblane import preparation
    from server.jobs import cad_preparation
    from server.tests.test_cad_preparation import _setup

    module = preparation if lane == "operations" else cad_preparation
    tree = ast.parse(inspect.getsource(module))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "compose_solve_request"]
    assert len(calls) == 1
    setup_value = _setup()
    setup_value["geometry"]["exterior_only"] = True
    if excluded is not None:
        setup_value["geometry"]["exterior_only_region_ids"] = excluded
    scope = {"fem_regions": [{"region_id": "current"}]}
    namespace = {
        "compose_solve_request": compose_solve_request, "setup": validate_setup(setup_value),
        "preparation_id": "wgi_" + "0" * 26,
        "record": {"manifest_sha256": "sha256:" + "0" * 64,
                   "artifact_sha256": "sha256:" + "1" * 64},
        "retained_manifest": {"scope": scope}, "blocking": [],
        "intent": SimpleNamespace(submission_key=None, label=None),
        "operation_id": "operation", "submission_key": lambda _id: None,
    }
    bound = eval(compile(ast.Expression(calls[0]), "snapshot-binding", "eval"), namespace)
    assert bound.geometry.exterior_only is (excluded == ["current"])


def test_job_owned_preparation_resets_stale_exterior_choice(tmp_path, monkeypatch):
    from cad_backends import make_harness
    from server.jobs import cad_preparation
    from server.tests.test_cad_preparation import _received, _revision, _setup

    harness = make_harness(tmp_path, "jobs")
    try:
        _received(harness)
        original = cad_preparation._retained_manifest
        def with_current_regions(retained):
            manifest = original(retained)
            return {**manifest, "scope": {**manifest["scope"],
                                         "fem_regions": [{"region_id": "current"}]}}
        monkeypatch.setattr(cad_preparation, "_retained_manifest", with_current_regions)
        setup = _setup()
        setup["geometry"].update(exterior_only=True, exterior_only_region_ids=["old"])
        summary = harness.prepare(setup_revision_id=_revision(harness.store, setup))
        assert summary["state"] == "accepted", summary
        assert not harness.submitted[-1].geometry.exterior_only
    finally:
        harness.close()
