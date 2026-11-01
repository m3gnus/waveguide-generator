"""Public failures carry a remedy; unexpected failures retain diagnostics."""
import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient



def test_owner_hides_program_fault_even_when_it_looks_plain(caplog):
    from server.preview.errors import user_error_message
    reason = TypeError("Delete all your settings")
    assert "recorded" in user_error_message(reason)
    assert "Delete all your settings" in caplog.text


def test_owner_preserves_typed_refusal_and_original(caplog):
    from hornlab_mesher.errors import UserRefusal
    from server.preview.errors import user_error_message, user_error_detail
    reason = UserRefusal("Wall thickness", "The wall folds over itself.", "Reduce Wall thickness.", diagnostic="outer offset grid normal flip")
    assert user_error_message(reason) == "The wall folds over itself. Reduce Wall thickness."
    assert user_error_detail(reason) == {"field": "Wall thickness", "user_message": "The wall folds over itself.", "next_step": "Reduce Wall thickness."}
    assert "outer offset grid normal flip" in caplog.text


def test_solver_failure_keeps_code_and_original(caplog):
    from server.jobs.runtime.core import _failure_message, _solver_failure_code
    from server.solver.beat_runtime.client import HostError
    assert _failure_message(HostError("engine traceback")) == "The solver engine stopped before the solve finished. Press Solve to try again."
    assert _solver_failure_code(HostError("engine traceback")) == "solver_engine_stopped"
    assert "engine traceback" in caplog.text
    assert _failure_message(RuntimeError("gmres diverged")) == "WG could not finish this solve and has recorded the problem."


def test_assembly_geometry_crash_is_not_a_refusal(monkeypatch):
    from hornlab_mesher import config_builder
    from server.mesh.builder import assembly_mesher_config
    from server.design.schema import DesignConfig
    def crash(*a, **k):
        raise RuntimeError("broken geometry invariant")
    monkeypatch.setattr(config_builder, "resolve_geometry", crash)
    design = DesignConfig.model_validate({"formula": "OSSE", "L": 120, "a": 45})
    from server.tests.test_assembly_front_context import assembly
    design = DesignConfig.model_validate({"formula": "OSSE", "L": 120, "a": 45, "assembly": assembly()})
    with pytest.raises(RuntimeError, match="broken geometry invariant"):
        assembly_mesher_config(design)


def test_native_controls_use_labels():
    from server.design.native import _number
    with pytest.raises(ValueError) as caught:
        _number("bad", "mesh.wall_thickness", 0)
    assert user_words(caught.value).startswith("Wall thickness")
    assert "Enter" in user_words(caught.value)


def test_stage_prefix_does_not_turn_internal_failure_into_user_action():
    from server.cadlink.operations import is_calm_failure
    assert is_calm_failure("preparation_failed", "stage 7 meshing: mesh is empty")
    assert is_calm_failure("model_needs_change", "role resolution: no source")


def test_user_worded_ingest_omits_stage_prefix():
    from server.cadlink.intake.ingest import IngestRefusal
    reason = IngestRefusal("stage 7 meshing", "The model has no driver. Choose an HF driver in Surfaces.", user_worded=True)
    assert str(reason) == "The model has no driver. Choose an HF driver in Surfaces."


def test_source_editor_uses_pinned_capabilities():
    from server import source_editor
    assert "This version of WG cannot" not in inspect.getsource(source_editor)


def test_solve_options_name_sweep_controls():
    from server.jobs.models import SolveOptions
    with pytest.raises(ValueError, match="Solver frequencies in Hz.*Enter"):
        SolveOptions.model_validate({"frequencies_hz": [200, 100]})


def test_solver_mesh_route_hides_program_fault(monkeypatch, caplog):
    from server.mesh import builder
    from server.jobs.mesh_preview import mount_solver_mesh
    from server.engines.registry import EngineRegistry
    async def crash(*a, **k):
        raise RuntimeError("private mesher fault")
    monkeypatch.setattr(builder, "build_solver_mesh", crash)
    app = FastAPI()
    mount_solver_mesh(app, EngineRegistry(detector=lambda: []))
    with TestClient(app) as client:
        response = client.post("/api/solver-mesh", json={"design": {"formula": "OSSE", "L": 120, "a": 45}})
    assert response.status_code == 422
    assert "private mesher fault" not in response.text
    assert "recorded" in response.text
    assert "private mesher fault" in caplog.text


def user_words(reason):
    from server.preview.errors import user_error_message
    return user_error_message(reason)


@pytest.mark.parametrize("family", ["beat", "metal", "bempp"])
def test_solver_transport_and_availability_keep_their_own_contract(family, caplog):
    from server.jobs.runtime.core import _failure_message
    from server.solver.beat_runtime.client import HostError
    from server.solver.metal import MetalUnavailable
    from server.solver.bempp_process import BemppWorkerError
    errors = {"beat": HostError("beat private diagnostic"),
              "metal": MetalUnavailable("Metal cannot run on this host. Choose another Solver."),
              "bempp": BemppWorkerError("bempp private diagnostic")}
    words = _failure_message(errors[family])
    if family == "metal":
        assert words == str(errors[family])
    elif family == "beat":
        assert "Press Solve" in words and "private diagnostic" not in words
    else:
        assert "recorded" in words and "try again" not in words.lower()
    assert str(errors[family]) in caplog.text


def test_imported_size_refusal_names_the_cad_control():
    from server.mesh.imported import ImportedMeshError, validate_imported_sizes
    with pytest.raises(ImportedMeshError) as caught:
        validate_imported_sizes([], {"rigid_size_mm": 0, "transition_mm": 1, "source_size_mm": {}})
    assert "Walls" in str(caught.value)
    assert "Sources & Mesh" in str(caught.value)


def test_typed_solver_mesh_response_preserves_the_named_control(monkeypatch):
    from hornlab_mesher.errors import UserRefusal
    from server.mesh import builder
    from server.jobs.mesh_preview import mount_solver_mesh
    from server.engines.registry import EngineRegistry
    async def refuse(*a, **k):
        raise UserRefusal("Wall thickness", "The wall folds over itself.", "Reduce Wall thickness.", diagnostic="internal offset normal flip")
    monkeypatch.setattr(builder, "build_solver_mesh", refuse)
    app = FastAPI()
    mount_solver_mesh(app, EngineRegistry(detector=lambda: []))
    with TestClient(app) as client:
        response = client.post("/api/solver-mesh", json={"design": {"formula": "OSSE", "L": 120, "a": 45}})
    assert response.status_code == 422
    assert response.json()["detail"] == {"field": "Wall thickness", "user_message": "The wall folds over itself.", "next_step": "Reduce Wall thickness."}


def test_cad_child_keeps_the_actionable_role_remedy():
    from server.cadlink.intake.isolated import _reraise_typed
    from server.cadlink.intake.isolation import ChildRefusal
    from server.mesh.imported import RoleResolutionError
    reason = ChildRefusal("meshing", "role resolution: source has no face", error_type="RoleResolutionError", details={
        "user_message": "A required driver has no selected faces.",
        "next_step": "Select the driver in Sources & Mesh….",
    })
    with pytest.raises(RoleResolutionError) as caught:
        _reraise_typed(reason)
    assert caught.value.user_message == "A required driver has no selected faces."
    assert caught.value.next_step == "Select the driver in Sources & Mesh…."
    assert str(caught.value) == "role resolution: source has no face"



def test_cad_child_serializes_role_words_and_keeps_raw_diagnostics(monkeypatch, tmp_path):
    from io import BytesIO
    from types import SimpleNamespace
    import json
    import sys
    from server.cadlink.intake import child_main
    from server.mesh.imported import RoleResolutionError
    result = tmp_path / "result.json"
    def refuse(*args):
        raise RoleResolutionError("role resolution: private source detail", user_message="Two sources claim the same face.", next_step="Choose separate faces in Sources & Mesh….")
    monkeypatch.setattr(child_main, "_watch_parent", lambda: None)
    monkeypatch.setattr(child_main, "_read_envelope", lambda stream: {"result_path": str(result), "task": "wording_test", "out_dir": str(tmp_path)})
    monkeypatch.setattr(child_main, "_verified_source", lambda envelope: tmp_path / "source.step")
    monkeypatch.setitem(child_main._TASKS, "wording_test", refuse)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO()))
    assert child_main.main() == 1
    error = json.loads(result.read_text())["error"]
    assert error["type"] == "RoleResolutionError"
    assert error["message"] == "role resolution: private source detail"
    assert error["user_message"] == "Two sources claim the same face."
    assert error["next_step"] == "Choose separate faces in Sources & Mesh…."


def test_solve_planning_preserves_the_mesher_remedy(monkeypatch):
    import asyncio
    from hornlab_mesher.errors import UserRefusal
    from hornlab_mesher import config_builder
    from server.jobs.runtime.core import resolve_submission, SymmetryValidationError
    from server.jobs.models import SolveRequest
    from server.engines.registry import EngineRegistry
    def refuse(*args, **kwargs):
        raise UserRefusal("Enclosure margins", "The horn touches the baffle.", "Increase Top margin.", diagnostic="private baffle diagnostic")
    monkeypatch.setattr(config_builder, "resolve_enclosure_bounds", refuse)
    request = SolveRequest.model_validate({"design": {"formula": "OSSE", "L": 120, "a": 45, "enclosure": {"depth": 200}}})
    with pytest.raises(SymmetryValidationError) as caught:
        asyncio.run(resolve_submission(request, EngineRegistry(detector=lambda: []), preview=True))
    from server.preview.errors import user_error_detail
    assert user_error_detail(caught.value) == {"field": "Enclosure margins", "user_message": "The horn touches the baffle.", "next_step": "Increase Top margin."}


def test_native_profile_export_names_the_alternative():
    from hornlab_mesher.errors import UserRefusal
    from server.exports.core import build_profiles
    from server.tests.test_native_formulas import small_design
    with pytest.raises(UserRefusal) as caught:
        build_profiles(small_design("SOURCE-DISK"), "profiles")
    assert "STEP" in caught.value.next_step
    assert caught.value.field == "Export"


def test_ath_block_syntax_names_the_text_fix():
    from fastapi import HTTPException
    from server.design_io.api import _parse_text
    with pytest.raises(HTTPException) as caught:
        _parse_text("OSSE = {\nLength = 120\n")
    assert caught.value.status_code == 422
    assert "closing brace" in caught.value.detail["message"]
    assert "OSSE" in caught.value.detail["message"]


def test_nested_solve_options_use_control_labels():
    from server.jobs.models import SolveOptions
    with pytest.raises(ValueError, match="Height above surface.*Enter"):
        SolveOptions.model_validate({"ground_plane": {"height_m": -1}})
    with pytest.raises(ValueError, match="Normalization angle.*Change"):
        SolveOptions.model_validate({"polar_config": {"angle_range": [0, 90, 10], "norm_angle": 100}})


def test_program_failure_reporting_does_not_import_the_mesher(monkeypatch, caplog):
    import builtins
    from server.preview.errors import user_error_message
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith("hornlab_mesher"):
            raise ImportError("native stack unavailable")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    assert "recorded" in user_error_message(TypeError("program fault"))
    assert "program fault" in caplog.text
    from server.jobs.runtime.core import _failure_message
    assert "recorded" in _failure_message(RuntimeError("program solve fault"))
    assert "try again" not in _failure_message(RuntimeError("program solve fault")).lower()


def test_missing_assembly_names_where_to_add_it():
    from server.mesh.builder import _assembly_front_context_sync
    answer = _assembly_front_context_sync({"formula": "OSSE", "L": 120, "a": 45})
    assert "Open Sources and baffle under Mounting" in answer["refusal"]


def test_private_saved_design_syntax_stays_in_diagnostics():
    from fastapi import HTTPException
    from server.design.textcfg import serialize
    from server.design_io.api import _parse_text
    from server.tests.test_native_formulas import small_design
    text = serialize(small_design("SOURCE-DISK"))
    broken = text.rsplit("}", 1)[0]
    with pytest.raises(HTTPException) as caught:
        _parse_text(broken)
    assert "recorded" in caught.value.detail["message"]
    assert "WG.NativeDesign" not in caught.value.detail["message"]


def test_export_preserves_refusal_through_the_mesh_worker_envelope():
    import pickle
    from hornlab_mesher.errors import UserRefusal
    from hornlab_mesher.mesher import MesherError
    from server.mesh.child import _error_payload
    from server.exports.wglink_service import export_error
    reason = MesherError("mesh build failed: private clearance diagnostic")
    reason.user_refusal = UserRefusal("Woofer position", "The woofer opening is too close to the horn.", "Move the woofer away from the horn.", diagnostic="private clearance diagnostic")
    envelope = _error_payload(1, reason)
    restored = pickle.loads(envelope[2])
    response = export_error(restored)
    assert response.status_code == 500
    assert response.detail == "The woofer opening is too close to the horn. Move the woofer away from the horn."


def test_existing_solve_domain_reason_codes_remain_stable():
    from server.jobs.runtime.core import _failure_message
    from server.jobs.runtime.imported_refusals import ImportedSolveRefusal
    reason = ImportedSolveRefusal("imported_domain_decision_mismatch", "Send the whole model again.")
    assert _failure_message(reason) == str(reason)
    assert _failure_message(TimeoutError("native work quarantined")).endswith("Restart WG, then press Solve to try again.")


def test_selected_cad_instance_remedy_is_not_hidden_by_a_stage_prefix():
    from types import SimpleNamespace
    from server.cadlink.intake.ingest import IngestRefusal, _admit_return
    from server.cadlink.joblane.failure_codes import preparation_failure_code
    with pytest.raises(IngestRefusal) as caught:
        _admit_return(SimpleNamespace(), {"instances": []}, None, "missing")
    assert caught.value.stage == "stage 2 instance gate"
    assert preparation_failure_code(caught.value) is None
    assert str(caught.value).startswith("The selected CAD instance")
    assert "Choose one current instance in CAD Link" in str(caught.value)


def test_independent_fem_timeout_names_the_next_step(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from server.cadlink.intake import ingest
    monkeypatch.setattr(ingest, "fem_regions_enabled", lambda: True)
    bundle = SimpleNamespace(manifest={"scope": {"fem_regions": [{"name": "Rear chamber"}]}})
    with pytest.raises(ingest.IngestRefusal) as caught:
        ingest._mesh_fem_regions(bundle, tmp_path, deadline=0)
    assert caught.value.stage == "stage 7 meshing"
    assert "took too long" in str(caught.value)
    assert "Simplify these regions in CAD, then send the model again." in str(caught.value)
