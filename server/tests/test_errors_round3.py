"""Review regressions cross the public HTTP and WGLink boundaries."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def capture(route, response):
    data = response.json()
    if filename := os.environ.get("ERROR_ROUTES_LOG"):
        with Path(filename).open("a") as stream:
            stream.write(
                json.dumps({"route": route, "status": response.status_code, "body": data}) + "\n"
            )
    assert "has recorded the problem" not in json.dumps(data), data
    return data


@pytest.mark.parametrize("case", ["depth", "overlap", "plug", "horn"])
def test_source_assembly_real_routes(case, tmp_path):
    from server.source_editor import create_router
    from test_source_assembly_editor import document

    body = document()
    path = "/api/source-editor/assembly/validate"
    expected = {
        "depth": "Enclosure depth",
        "overlap": "woofer",
        "plug": "Plug inlet Z",
        "horn": "circular",
    }[case]
    if case == "depth":
        body["dimensions"]["depth_mm"] = 24.05
    if case == "overlap":
        body["dimensions"]["woofer_xy_mm"] = [-13, 17]
    if case == "plug":
        body["phase_plugs"][0]["z0_mm"] = 0
    if case == "horn":
        path = "/api/source-editor/assembly/horn-profile"
        body = {"formula": "OSSE", "r0": 4, "a": "32+5*cos(2*p)", "a0": 6, "L": 24}
    app = FastAPI()
    app.include_router(create_router(tmp_path))
    response = TestClient(app).post(path, json=body)
    assert response.status_code == 422, response.text
    data = capture(path + ":" + case, response)
    assert expected in str(data)


@pytest.mark.parametrize(
    "case", ["roles", "scale", "segments", "velocity", "freeform", "frequencies", "edge", "ath"]
)
def test_real_design_and_import_routes(case, tmp_path):
    from test_models_api import app_for
    from test_file_mesh_roles_symmetry import box_mesh
    from test_design_assembly_record import assembly, design
    from server.mesh.file_import import mesh_text
    from server.cadlink.store import CadLinkStore
    from server.jobs.api import create_jobs_router
    from server.jobs.mesh_preview import mount_solver_mesh
    from server.preview.freeform import mount_freeform

    async def run():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        app.include_router(create_jobs_router(runtime))
        from server.engines.registry import EngineRegistry

        mount_solver_mesh(app, EngineRegistry(detector=lambda: []))
        mount_freeform(app)
        path = "/api/mesh-estimate"
        body = {"design": {"formula": "OSSE", "L": 120, "a": 45}}
        status = 422
        if case == "roles":
            path = "/api/models/files"
            body = {
                "filename": "box.msh",
                "msh_text": mesh_text(box_mesh()),
                "units": "m",
                "forward": "+z",
                "up": "+y",
                "domain_planes": [],
                "roles": {1: "RIGID", 101: "RIGID", 102: "RIGID"},
            }
        elif case in ("scale", "segments"):
            path = "/api/models/assembly/context"
            body = {"design": design(assembly=assembly())}
            if case == "scale":
                body["design"]["scale"] = 0
            else:
                body["design"]["mesh"] = {"length_segments": 0}
                status = 200
        elif case == "velocity":
            body["design"]["source"] = {"velocity": 3}
        elif case == "freeform":
            path = "/api/design/freeform/curves"
            profile = {"points": [[0, 12.7], [1, 40]]}
            body = {
                "content_key": "review",
                "design": {
                    "formula": "FREEFORM",
                    "length": 60,
                    "profile_h": profile,
                    "profile_v": profile,
                    "cross_sections": [{"t": 0, "shape": "ellipse"}, {"t": 1, "shape": "ellipse"}],
                    "cross_section": {"exponent": 4},
                },
            }
        elif case == "frequencies":
            path = "/api/solve"
            body["options"] = {"frequencies_hz": [500, 500]}
        elif case == "edge":
            path = "/api/solver-mesh"
            body["design"]["mesh"] = {"max_edge": 0.5}
        elif case == "ath":
            from server.design.textcfg import parse

            body["design"] = parse(
                "Throat.Profile = 1\nLength = 120\nCoverage.Angle = 45\nMesh.Enclosure = {\nDepth = 0\n}\n"
            ).design.model_dump(mode="json")
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(path, json=body)
            assert response.status_code == status, response.text
            data = capture(path + ":" + case, response)
            if case == "scale":
                assert data["error"]["code"] == "assembly_refused"
            if case == "segments":
                assert "Surface length samples" in data["refusal"]
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(run())


def test_imported_size_crosses_browser_prepare(tmp_path, monkeypatch):
    from server.cadlink import api
    from server.mesh.imported import validate_imported_sizes

    with pytest.raises(ValueError) as caught:
        validate_imported_sizes(
            [{"id": "HF"}], {"rigid_size_mm": 10, "transition_mm": 5, "source_size_mm": {"HF": 0}}
        )
    error = caught.value
    refusal = api._ingest_error(error)
    assert refusal.status_code == 422 and "positive size" in str(refusal.detail)
    monkeypatch.setattr(api, "_resolve_ingest_bundle", lambda *args: tmp_path)

    def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr(api, "ingest_bundle", refuse)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(cadlink_store=None, data_dir=tmp_path))
    )
    payload = api.CadReturnIngestRequest.model_validate(
        {
            "bundlePath": "review",
            "mesh": {"rigid_size_mm": 10, "transition_mm": 5, "source_size_mm": {"HF": 0}},
        }
    )
    with pytest.raises(Exception) as result:
        asyncio.run(api.post_ingest(payload, request))
    assert result.value.status_code == 422
    assert "positive size" in str(result.value.detail)


@pytest.mark.parametrize("selected", [None, "i1"])
def test_instance_refusal_reaches_projector(selected):
    from server.cadlink.intake.ingest import _admit_return, IngestRefusal
    from server.cadlink.joblane.request_status import project_request_status
    from server.cadlink.wglink_protocol import user_text_violations

    manifest = {
        "instances": [
            {"design_id": "d", "instance_id": "i1"},
            {"design_id": "d", "instance_id": "i2"},
        ],
        "coordinate_system": {"solver_anchor_instance_id": "i2"},
    }
    with pytest.raises(IngestRefusal) as caught:
        _admit_return(SimpleNamespace(get_design=lambda _: {"id": "d"}), manifest, "d", selected)
    words = str(caught.value)
    assert not user_text_violations(words)
    job = {
        "id": "refused",
        "status": "error",
        "task_metadata": {"cad": {"refusal": {"code": "model_needs_change", "message": words}}},
    }
    projected, incident = project_request_status(
        {"commandId": "r", "kind": "prepare_and_solve"}, None, job, {"message": words}
    )
    assert incident is None and projected["failure"]["recorded"] is False
    assert projected["failure"]["message"] == words

    if filename := os.environ.get("PROJECTOR_RESULTS_LOG"):
        with Path(filename).open("a") as stream:
            stream.write(
                json.dumps(
                    {"route": "WGLink instance " + str(selected), "words": words, "recorded": False}
                )
                + "\n"
            )


@pytest.mark.parametrize(
    "kind", [TypeError, KeyError, AttributeError, AssertionError, IndexError, OSError, RuntimeError]
)
def test_true_program_faults_stay_private(kind):
    from server.preview.errors import user_error_message, PROGRAM_FAILURE

    assert user_error_message(kind("private diagnostic")) == PROGRAM_FAILURE


def test_catalog_rejects_phantoms_and_covers_prose():
    import ast
    from importlib.resources import files
    from hornlab_mesher.controls import control_label, PROFILE_CONTROL_KEYS
    from hornlab_mesher.errors import control_refusal

    with pytest.raises(KeyError):
        control_refusal("wrong name", "Arc centre", "Change Arc centre.")
    # Named controls used by the reviewed prose must belong to the UI catalog.
    for label in [
        "Arc center radius",
        "Arc center z",
        "Imported ICW coefficients",
        "Body throat radius",
        "Horizontal mouth angle",
        "Vertical mouth angle",
        "In °",
        "Out °",
        "Solver",
        "Solver mode",
        "Symmetry",
    ]:
        assert control_label(label) == label
    for identifier in PROFILE_CONTROL_KEYS.values():
        for plane in ("H", "V"):
            assert control_label(identifier.replace("{plane}", plane))
    phantoms = [
        "Arc centre",
        "Export format",
        "Clear Subdomain interfaces",
        "Direct curvature coefficients",
        "Throat diameter",
        "Outgoing angle",
        "Incoming handle",
        "Outgoing handle",
        ".Mouth angle",
    ]
    for root in [Path(str(files("hornlab_mesher"))), Path(__file__).parents[1]]:
        for filename in root.rglob("*.py"):
            if "tests" in filename.parts:
                continue
            tree = ast.parse(filename.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id not in ("UserRefusal", "control_refusal"):
                    continue
                if (
                    node.func.id == "control_refusal"
                    and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                ):
                    control_label(node.args[1].value)
                prose = list(node.args[2:3]) + [
                    kw.value for kw in node.keywords if kw.arg == "message"
                ]
                for expression in prose:
                    text = "".join(
                        n.value
                        for n in ast.walk(expression)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    )
                    assert not any(word in text for word in phantoms), (filename, node.lineno, text)


def test_all_authored_ingest_refusals_pass_the_shared_guard():
    import ast
    import copy
    from server.cadlink.intake import ingest
    from server.cadlink.wglink_protocol import user_text_violations

    tree = ast.parse(Path(ingest.__file__).read_text())

    # Exercise every authored user_worded sentence, including formatted region
    # names. Dynamic scope-owner messages are covered by its admission tests.
    class ExampleNames(ast.NodeTransformer):
        def visit_FormattedValue(self, node):
            return ast.copy_location(ast.Constant("Sample model"), node)

    seen = 0
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Name)
            or node.func.id != "IngestRefusal"
        ):
            continue
        if not any(
            kw.arg == "user_worded"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        ):
            continue
        expr = copy.deepcopy(node.args[1])
        if isinstance(expr, ast.Name):
            # The shared scope owner is tested with real malformed manifests.
            assert expr.id == "message"
            continue
        expr = ExampleNames().visit(expr)
        message = eval(
            compile(ast.fix_missing_locations(ast.Expression(expr)), "<refusal>", "eval"),
            vars(ingest),
        )
        assert not user_text_violations(message), (
            node.lineno,
            message,
            user_text_violations(message),
        )
        seen += 1
    assert seen >= 14


@pytest.mark.parametrize("path", ["/api/models/assembly/context", "/api/models/assembly/compile"])
def test_child_failure_keeps_legacy_status_and_code(path, tmp_path, monkeypatch):
    from test_models_api import app_for
    from server.cadlink.store import CadLinkStore
    from server.jobs import models_api
    from server.mesh.child import MesherChildError

    async def refuse(*args, **kwargs):
        raise MesherChildError("child failure")

    monkeypatch.setattr(models_api, "assembly_front_context", refuse)
    monkeypatch.setattr(models_api, "compile_design_assembly", refuse)

    async def run():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        body = {"design": {"formula": "OSSE", "L": 120, "a": 45}}
        if path.endswith("/compile"):
            body.update(design_uid="wgu_00000000000000000000000000", design_revision=1)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                result = await client.post(path, json=body)
            assert result.status_code == 422
            assert result.json()["error"]["code"] == "assembly_refused"
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(run())


def test_arc_control_names_and_applied_remedy_cross_http(tmp_path):
    from hornlab_mesher.source_contour import flat, ContourDrive
    from server.source_editor import create_router, canonical_document

    contour = flat(8)
    body = canonical_document(
        contour, ContourDrive("test", tuple((s.id, 1) for s in contour.segments))
    )
    body["contour"]["points"][0]["z_mm"] = 3
    body["contour"]["segments"][0].update(kind="arc", center_mm=[0, 0], direction="cw")
    app = FastAPI()
    app.include_router(create_router(tmp_path))
    client = TestClient(app)
    response = client.post("/api/source-editor/validate", json=body)
    assert response.status_code == 422
    data = capture("/api/source-editor/validate:arc", response)
    assert "Arc center radius and Arc center z" in data["detail"]
    body["contour"]["segments"][0]["center_mm"] = [0, -55 / 6]
    assert client.post("/api/source-editor/validate", json=body).status_code == 200


def test_existing_ath_public_guidance_is_not_wrapped_again(monkeypatch):
    from server.design import textcfg

    from server.design.text_import import AthImportGuidance
    error = textcfg.TextConfigError(AthImportGuidance(
        "The ATH setting needs a change. Change the named ATH setting."))

    def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr(textcfg, "_parse", refuse)
    with pytest.raises(textcfg.TextConfigError) as caught:
        textcfg.parse("Length = 120")
    assert caught.value is error
    assert getattr(error, "user_refusal", None) is None
    assert error.public_message.startswith("The ATH setting")


@pytest.mark.parametrize("kind", [ValueError, None])
def test_plain_domain_words_are_public_even_through_validation_causes(kind):
    from hornlab_mesher.errors import ConfigError
    from server.preview.errors import user_error_message

    reason = (kind or ConfigError)("This profile cannot be used. Change its dimensions.")
    reason.__cause__ = ConfigError("authored input")
    assert user_error_message(reason) == str(reason)


@pytest.mark.parametrize(
    "kind", [TypeError, KeyError, AttributeError, AssertionError, IndexError, OSError]
)
@pytest.mark.parametrize(
    "message",
    [
        "The enclosure leaves an invalid result",
        "the acoustic control grid needed for the requested mm resolution",
    ],
)
def test_program_faults_never_become_geometry_refusals_by_text(kind, message):
    from server.preview.errors import public_error_message

    assert public_error_message(kind(message)) is None


def test_ui_catalog_matches_the_installed_mesher_package(tmp_path):
    from importlib.resources import files
    import shutil
    import subprocess

    frontend = Path(__file__).parents[2] / "frontend"
    node = shutil.which("node")
    assert node, "The catalog contract needs the frontend test runtime"
    output = tmp_path / "ui-controls.json"
    subprocess.run(
        [
            node,
            str(frontend / "node_modules/vitest/vitest.mjs"),
            "run",
            "src/design/refusalCatalog.test.ts",
        ],
        cwd=frontend,
        env={**os.environ, "CONTROL_CATALOG_OUTPUT": str(output)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(output.read_text()) == json.loads(
        files("hornlab_mesher").joinpath("control_labels.json").read_text()
    )


def test_fem_symmetry_refusal_crosses_the_projector(monkeypatch):
    from server.cadlink.intake import ingest
    from server.cadlink.joblane.request_status import project_request_status

    monkeypatch.setattr(ingest, "fem_regions_enabled", lambda: True)
    manifest = {
        "scope": {
            "fem_regions": [{"region_id": "chamber", "name": "Chamber", "surface_groups": []}]
        }
    }
    with pytest.raises(ingest.IngestRefusal) as caught:
        ingest._refuse_fem_domain(manifest, {}, ["y0"])
    words = str(caught.value)
    job = {
        "id": "refused",
        "status": "error",
        "task_metadata": {"cad": {"refusal": {"code": "model_needs_change", "message": words}}},
    }
    projected, incident = project_request_status(
        {"commandId": "r", "kind": "prepare_and_solve"}, None, job, {"message": words}
    )
    assert incident is None and projected["failure"]["recorded"] is False
    assert projected["failure"]["message"] == words
    if filename := os.environ.get("PROJECTOR_RESULTS_LOG"):
        with Path(filename).open("a") as stream:
            stream.write(
                json.dumps({"route": "WGLink FEM symmetry", "words": words, "recorded": False})
                + "\n"
            )


@pytest.mark.parametrize(
    "group",
    [
        {"kind": "moving", "lining": {}},
        {"kind": "rigid", "lining": {}},
        {
            "kind": "rigid",
            "lining": {"model": "miki", "thickness_mm": 0, "flow_resistivity_pa_s_per_m2": 1},
        },
        {"kind": "rigid", "relative_level_db": 0},
        {"kind": "moving", "relative_level_db": 1},
    ],
)
def test_dynamic_user_worded_scope_messages_pass_the_guard(group):
    from server.solver.coupled_admission import fem_group_options_problem
    from server.cadlink.wglink_protocol import user_text_violations

    result = fem_group_options_problem(
        [
            {
                "region_id": "chamber",
                "name": "Chamber",
                "surface_groups": [{"group_id": "surface", "name": "Surface", **group}],
            }
        ]
    )
    assert result is not None
    assert not user_text_violations(result[1])
