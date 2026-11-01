"""The round-four faults stay private at the final public boundaries."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest

SOLVE_FAILURE = "WG could not finish this solve and has recorded the problem."
REQUEST_FAILURE = "WG could not finish this request and has recorded the problem."


def capture(label, detail, expected, *, solve=False):
    if filename := os.environ.get("ERRORS_ROUND4_OUTPUT"):
        with Path(filename).open("a") as stream:
            stream.write(
                json.dumps({"label": label, "detail": detail, "expected": expected, "solve": solve})
                + "\n"
            )


@pytest.mark.parametrize(
    "message",
    [
        "streamed result has invalid impedance",
        "streamed directivity shape does not match its planes/angles",
        "combine members do not share one observation grid",
        "mesher point grid contains invalid surface points",
        "operands could not be broadcast together with shapes (3,) (4,)",
        "math domain error",
    ],
)
def test_solve_fault_owner_is_private(message, caplog):
    from server.jobs.runtime.core import _failure_message

    assert _failure_message(ValueError(message)) == SOLVE_FAILURE
    assert message in caplog.text
    capture(message, _failure_message(ValueError(message)), SOLVE_FAILURE, solve=True)


@pytest.mark.parametrize("during_mesh", [False, True])
def test_persisted_job_preserves_mesh_refusal_but_hides_solve_fault(
    during_mesh, tmp_path, monkeypatch, caplog
):
    from hornlab_mesher.errors import ConfigError
    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore
    from test_jobs_runtime import _request
    from server.engines.dryrun import DryRunEngine

    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    words = "The source setting cannot be used. Enter a plain number in Source radius."
    if during_mesh:

        def fail(*args, **kwargs):
            raise ConfigError(words)

        monkeypatch.setattr(DryRunEngine, "mesh_artifact", fail)
    else:

        def fail(*args, **kwargs):
            raise ValueError("streamed result has invalid impedance")

        monkeypatch.setattr(DryRunEngine, "solve", fail)

    async def run():
        runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
        try:
            job_id = await runtime.submit(_request(delay_ms=0))
            await runtime.wait_idle()
            row = await runtime.get_job(job_id)
            assert row["status"] == "error"
            expected = words if during_mesh else SOLVE_FAILURE
            assert row["error_message"] == expected
            capture(
                "persisted mesh refusal" if during_mesh else "persisted solve fault",
                row["error_message"],
                expected,
                solve=True,
            )
        finally:
            await runtime.shutdown()

    asyncio.run(run())
    if not during_mesh:
        assert "streamed result has invalid impedance" in caplog.text


@pytest.mark.parametrize(
    "message",
    [
        "could not convert string to float: '2*5'",
        "math domain error",
        "not enough values to unpack (expected 2, got 1)",
        "too many values to unpack (expected 2)",
        "zero-size array to reduction operation minimum which has no identity",
        "min() iterable argument is empty",
        "min() arg is an empty sequence",
        "operands could not be broadcast together with shapes (3,) (4,)",
        "invalid literal for int() with base 10: 'x'",
        "cannot reshape array of size 3 into shape (2,2)",
        "setting an array element with a sequence",
    ],
)
def test_library_message_shapes_are_private(message, caplog):
    from server.preview.errors import user_error_message

    words = user_error_message(ValueError(message))
    assert words == REQUEST_FAILURE
    assert message in caplog.text
    capture(message, words, REQUEST_FAILURE)


@pytest.mark.parametrize(
    "key,value,label",
    [
        ("radius", "2*5", "Source radius"),
        ("curvature", "1/2", "Source curvature"),
        ("radius", "10+2", "Source radius"),
        ("curvature", "1/0", "Source curvature"),
    ],
)
def test_source_number_refusal_crosses_real_preview(key, value, label):
    from server.engines.registry import EngineRegistry
    from server.jobs.mesh_preview import mount_solver_mesh
    from server.design.textcfg import parse

    # The first two are the review's real ATH import inputs; the others are JSON.
    if value in ("2*5", "1/2"):
        ath_key = "Source.Radius" if key == "radius" else "Source.Curv"
        design = parse(
            f"Throat.Profile = 1\nLength = 120\nCoverage.Angle = 45\n{ath_key} = {value}\n"
        ).design.model_dump(mode="json")
    else:
        design = {"formula": "OSSE", "L": 120, "a": 45, "source": {key: value}}
    app = FastAPI()
    mount_solver_mesh(app, EngineRegistry(detector=lambda: []))
    response = TestClient(app).post("/api/mesh-estimate", json={"design": design})
    assert response.status_code == 422
    detail = response.json()["detail"]
    words = f"{label} needs a plain number. Enter a plain number in {label}."
    assert isinstance(detail, dict), detail
    assert detail["user_message"] + " " + detail["next_step"] == words
    capture(key + "=" + value, detail, words)


def invariant_input(kind):
    from hornlab_mesher.freeform import validate_outer_offset_grid
    from hornlab_mesher.profile_formulas import _verify_meridian_self_contact
    from hornlab_mesher.passage_mesh import certify_passage_mesh

    if kind == "outer":
        validate_outer_offset_grid(np.zeros((8, 2, 3)), np.zeros((8, 3, 3)), full_circle=True)
    elif kind == "meridian":
        _verify_meridian_self_contact(np.array([[0, 0], [1, np.nan]]))
    else:
        certify_passage_mesh(
            SimpleNamespace(phase_plugs=(object(),)),
            np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]),
            np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]),
            [True] * 4,
        )


@pytest.mark.parametrize("kind", ["outer", "meridian", "passage"])
def test_actual_internal_invariants_stay_private_through_assembly_route(
    kind, tmp_path, monkeypatch, caplog
):
    from test_models_api import app_for
    from server.cadlink.store import CadLinkStore
    from server.jobs import models_api
    from hornlab_mesher.errors import ConfigError

    with pytest.raises(ValueError) as caught:
        invariant_input(kind)
    assert isinstance(caught.value, ConfigError)

    async def refuse(*args, **kwargs):
        raise caught.value

    monkeypatch.setattr(models_api, "assembly_front_context", refuse)
    store = CadLinkStore.for_data_dir(tmp_path)
    app, runtime = app_for(tmp_path, store)
    try:
        response = TestClient(app).post(
            "/api/models/assembly/context", json={"design": {"formula": "OSSE", "L": 120, "a": 45}}
        )
        assert response.status_code == 422  # Keep the existing ValueError catch/status.
        assert response.json()["detail"] == REQUEST_FAILURE
        assert str(caught.value) in caplog.text
        capture(kind, response.json()["detail"], REQUEST_FAILURE)
    finally:
        asyncio.run(runtime.shutdown())
        store.close()


def test_marker_keeps_legacy_catches_pickle_and_private_wrappers():
    import pickle
    from hornlab_mesher.errors import ConfigError, InternalInvariantError
    from server.preview.errors import user_error_message

    original = InternalInvariantError(
        "Internal invariant failed: outer grid does not match inner grid"
    )
    restored = pickle.loads(pickle.dumps(original))
    assert isinstance(restored, (ConfigError, ValueError))
    assert isinstance(restored, InternalInvariantError)
    assert str(restored) == str(original)
    for wrapper in (ValueError, ConfigError):
        error = wrapper(str(original))
        error.__cause__ = restored
        assert user_error_message(error) == REQUEST_FAILURE


def test_sewn_mapping_raise_uses_the_same_private_marker():
    import ast
    from importlib.resources import files

    tree = ast.parse(files("hornlab_mesher").joinpath("assembly_artifact.py").read_text())
    raises = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and node.exc.args
        and isinstance(node.exc.args[0], ast.Constant)
        and node.exc.args[0].value == "sewn assembly mapping is ambiguous"
    ]
    assert len(raises) == 1
    assert ast.unparse(raises[0].exc.func) == "InternalInvariantError"
